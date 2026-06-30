from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = False, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def safe_name(name: str) -> str:
    value = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in (name or '').strip())
    return value.strip('._') or 'item'


def validate_simple_name(name: str, label: str = 'name') -> str:
    value = (name or '').strip()
    if not value:
        raise ValueError(f'{label} is required')
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$', value):
        raise ValueError(f'{label} must start with a letter or number and may contain letters, numbers, dot, underscore, or hyphen')
    return value


def pool_xml(pool_name: str) -> ET.Element:
    result = run(['virsh', 'pool-dumpxml', pool_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Could not inspect storage pool {pool_name}')
    return ET.fromstring(result.stdout)


def pool_type(pool_name: str) -> str:
    return pool_xml(pool_name).get('type', '')


def ensure_pool_active(pool_name: str) -> None:
    info = run(['virsh', 'pool-info', pool_name], check=False)
    if info.returncode != 0:
        raise RuntimeError(info.stderr or info.stdout or f'Storage pool does not exist: {pool_name}')
    if 'State:' in info.stdout and 'running' not in info.stdout.lower():
        started = run(['virsh', 'pool-start', pool_name], check=False)
        if started.returncode != 0:
            raise RuntimeError(started.stderr or started.stdout or f'Could not start storage pool {pool_name}')
    run(['virsh', 'pool-refresh', pool_name], check=False)


def volume_path(pool_name: str, volume_name: str) -> str:
    result = run(['virsh', 'vol-path', '--pool', pool_name, volume_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Could not resolve volume {volume_name} in {pool_name}')
    return result.stdout.strip()


def create_volume_for_pool(pool_name: str, volume_name: str, size_gb: int, prefer_format: str = 'qcow2') -> dict[str, Any]:
    pool_name = validate_simple_name(pool_name, 'pool name')
    volume_name = validate_simple_name(volume_name, 'volume name')
    if size_gb < 1:
        raise ValueError('size_gb must be at least 1')

    ensure_pool_active(pool_name)
    ptype = pool_type(pool_name)

    if ptype == 'logical':
        if volume_name.endswith('.qcow2'):
            volume_name = volume_name[:-6]
        cmd = ['virsh', 'vol-create-as', '--pool', pool_name, '--name', volume_name, '--capacity', f'{size_gb}G']
        result = run(cmd, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or 'Failed to create logical volume')
        path = volume_path(pool_name, volume_name)
        return {
            'pool': pool_name,
            'volume': volume_name,
            'path': path,
            'pool_type': ptype,
            'disk_type': 'block',
            'source_attr': 'dev',
            'driver_type': 'raw',
            'format': 'raw',
        }

    if ptype in {'dir', 'fs', 'netfs'}:
        fmt = 'raw' if prefer_format == 'raw' else 'qcow2'
        if fmt == 'qcow2' and not volume_name.endswith('.qcow2'):
            volume_name = f'{volume_name}.qcow2'
        cmd = ['virsh', 'vol-create-as', '--pool', pool_name, '--name', volume_name, '--capacity', f'{size_gb}G', '--format', fmt]
        result = run(cmd, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or 'Failed to create disk volume')
        path = volume_path(pool_name, volume_name)
        return {
            'pool': pool_name,
            'volume': volume_name,
            'path': path,
            'pool_type': ptype,
            'disk_type': 'file',
            'source_attr': 'file',
            'driver_type': fmt,
            'format': fmt,
        }

    raise RuntimeError(f'Unsupported VM disk storage pool type: {ptype}')


def qemu_convert(source: str, destination: str, output_format: str) -> None:
    cmd = ['qemu-img', 'convert', '-p', '-O', output_format, source, destination]
    result = run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'qemu-img convert failed from {source} to {destination}')


def create_volume_from_image(pool_name: str, volume_name: str, source_image: str, size_gb: int, prefer_format: str = 'qcow2') -> dict[str, Any]:
    vol = create_volume_for_pool(pool_name, volume_name, size_gb, prefer_format=prefer_format)
    qemu_convert(source_image, vol['path'], vol['driver_type'])
    run(['virsh', 'pool-refresh', pool_name], check=False)
    return vol


def disk_records_from_domain_xml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    idx = 0
    for disk in root.findall('./devices/disk'):
        if disk.get('device') != 'disk':
            continue
        idx += 1
        driver = disk.find('driver')
        source = disk.find('source')
        target = disk.find('target')
        if source is None:
            continue
        source_attr = ''
        source_value = ''
        for attr in ('file', 'dev', 'name'):
            if source.get(attr):
                source_attr = attr
                source_value = source.get(attr) or ''
                break
        records.append({
            'index': idx,
            'type': disk.get('type', ''),
            'device': disk.get('device', ''),
            'driver_type': driver.get('type', '') if driver is not None else '',
            'driver_name': driver.get('name', '') if driver is not None else '',
            'source_attr': source_attr,
            'source': source_value,
            'target_dev': target.get('dev', '') if target is not None else f'vd{chr(ord("a") + idx - 1)}',
            'target_bus': target.get('bus', '') if target is not None else 'virtio',
        })
    return records


def disk_sources_from_domain_xml(xml_text: str) -> list[str]:
    return [r['source'] for r in disk_records_from_domain_xml(xml_text) if r.get('source')]


def set_disk_source(disk_elem: ET.Element, volume: dict[str, Any], target_dev: str | None = None) -> None:
    disk_elem.set('type', volume['disk_type'])
    disk_elem.set('device', 'disk')
    driver = disk_elem.find('driver')
    if driver is None:
        driver = ET.SubElement(disk_elem, 'driver')
    driver.set('name', 'qemu')
    driver.set('type', volume['driver_type'])
    if volume['disk_type'] == 'block':
        driver.set('cache', 'none')
        driver.set('io', 'native')
    source = disk_elem.find('source')
    if source is None:
        source = ET.SubElement(disk_elem, 'source')
    source.attrib.clear()
    source.set(volume['source_attr'], volume['path'])
    target = disk_elem.find('target')
    if target is None:
        target = ET.SubElement(disk_elem, 'target')
    if target_dev:
        target.set('dev', target_dev)
    if not target.get('bus'):
        target.set('bus', 'virtio')


def find_libvirt_volume_by_path(path: str) -> dict[str, str] | None:
    pools = run(['virsh', 'pool-list', '--all', '--name'], check=False)
    if pools.returncode != 0:
        return None
    for pool in [p.strip() for p in pools.stdout.splitlines() if p.strip()]:
        vols = run(['virsh', 'vol-list', '--pool', pool, '--name'], check=False)
        if vols.returncode != 0:
            continue
        for volume in [v.strip() for v in vols.stdout.splitlines() if v.strip()]:
            path_result = run(['virsh', 'vol-path', '--pool', pool, volume], check=False)
            if path_result.returncode == 0 and path_result.stdout.strip() == path:
                return {'pool': pool, 'volume': volume}
    return None


def delete_disk_source(path: str) -> dict[str, Any]:
    path = (path or '').strip()
    if not path:
        return {'deleted': False, 'message': 'empty disk source'}
    match = find_libvirt_volume_by_path(path)
    if match:
        result = run(['virsh', 'vol-delete', '--pool', match['pool'], match['volume']], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f'Failed to delete {match}')
        return {'deleted': True, 'message': f"deleted volume {match['volume']} from {match['pool']}"}
    if path.startswith('/dev/'):
        if not path.startswith('/dev/mapper/') and len(Path(path).parts) < 3:
            raise RuntimeError(f'Refusing suspicious block path: {path}')
        result = run(['lvremove', '-y', path], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f'Failed to remove LV {path}')
        return {'deleted': True, 'message': f'removed logical volume {path}'}
    file_path = Path(path)
    allowed = ['/atlasvm-vmdata/', '/atlasvm-storage/', '/var/lib/libvirt/images/']
    if not any(str(file_path).startswith(prefix) for prefix in allowed):
        raise RuntimeError(f'Refusing to delete disk outside known storage paths: {path}')
    if file_path.exists():
        file_path.unlink()
        return {'deleted': True, 'message': f'deleted file {path}'}
    return {'deleted': False, 'message': f'file already missing: {path}'}


def qemu_virtual_size_gb(image_path: str, fallback_gb: int = 1) -> int:
    result = run(['qemu-img', 'info', '--output=json', image_path], check=False)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout or '{}')
            size = int(data.get('virtual-size') or 0)
            if size > 0:
                return max(1, int((size + (1024 ** 3 - 1)) // (1024 ** 3)))
        except Exception:
            pass
    try:
        size = Path(image_path).stat().st_size
        return max(1, int((size + (1024 ** 3 - 1)) // (1024 ** 3)))
    except Exception:
        return fallback_gb
