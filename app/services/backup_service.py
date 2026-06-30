from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import libvirt

from app.core.config import get_settings
from app.services.vm_storage_utils import (
    create_volume_from_image,
    delete_disk_source,
    disk_records_from_domain_xml,
    qemu_convert,
    qemu_virtual_size_gb,
    safe_name,
    set_disk_source,
    validate_simple_name,
)

BACKUP_TARGETS_FILE = Path('/opt/atlasvm/atlasvm_backup_targets.json')
BACKUP_FORMAT = 'atlasvm-backup-v2'


@dataclass
class BackupResult:
    vm_name: str
    backup_dir: str
    archive_path: str | None
    metadata_path: str
    disk_count: int
    xml_path: str


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _bool(value: Any) -> bool:
    return str(value or '').lower() in {'1', 'true', 'yes', 'on'}


class BackupService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.backup_root = Path(self.settings.backup_path)
        self.backup_root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------
    # Targets / policy
    # ---------------------------------------------------------------------
    def list_targets(self) -> list[dict[str, Any]]:
        targets = [
            {
                'name': 'default',
                'label': 'Default local backup path',
                'path': str(self.backup_root),
                'kind': 'local',
                'roles': ['backups'],
                'enabled': True,
                'writable': os.access(self.backup_root, os.W_OK),
                'exists': self.backup_root.exists(),
                'managed': True,
            }
        ]

        data: dict[str, Any] = {}
        if BACKUP_TARGETS_FILE.exists():
            try:
                data = json.loads(BACKUP_TARGETS_FILE.read_text() or '{}')
            except Exception:
                data = {}

        for name, item in sorted(data.items()):
            path = Path(str(item.get('path') or ''))
            targets.append({
                'name': name,
                'label': item.get('label') or name,
                'path': str(path),
                'kind': item.get('kind') or 'custom',
                'roles': item.get('roles') or ['backups'],
                'enabled': bool(item.get('enabled', True)),
                'writable': path.exists() and os.access(path, os.W_OK),
                'exists': path.exists(),
                'managed': False,
            })

        # Auto-surface mounted NFS/SMB targets marked for backups by Phase 9.
        for meta_file, kind in [
            (Path('/opt/atlasvm/atlasvm_nfs_targets.json'), 'nfs'),
            (Path('/opt/atlasvm/atlasvm_smb_targets.json'), 'smb'),
        ]:
            try:
                if not meta_file.exists():
                    continue
                data = json.loads(meta_file.read_text() or '{}')
                for name, item in data.items():
                    roles = item.get('roles') or []
                    if 'backups' not in roles:
                        continue
                    path = Path(item.get('mount_path') or item.get('path') or '')
                    if not path:
                        continue
                    target_name = f'{kind}-{name}'
                    if any(t['name'] == target_name for t in targets):
                        continue
                    targets.append({
                        'name': target_name,
                        'label': f'{kind.upper()} {name}',
                        'path': str(path),
                        'kind': kind,
                        'roles': roles,
                        'enabled': True,
                        'writable': path.exists() and os.access(path, os.W_OK),
                        'exists': path.exists(),
                        'managed': True,
                    })
            except Exception:
                pass

        return targets

    def save_target(self, name: str, path: str, kind: str = 'custom', label: str = '', enabled: bool = True) -> dict[str, Any]:
        name = validate_simple_name(name, 'target name')
        root = Path(path).resolve()
        allowed_prefixes = ['/atlasvm-vmdata/', '/atlasvm-storage/', '/mnt/', '/srv/', '/var/backups/']
        if not any(str(root).startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError('Backup target path must be under /atlasvm-vmdata, /atlasvm-storage, /mnt, /srv, or /var/backups')
        root.mkdir(parents=True, exist_ok=True)
        if not os.access(root, os.W_OK):
            raise ValueError(f'Backup target is not writable: {root}')
        data = {}
        if BACKUP_TARGETS_FILE.exists():
            try:
                data = json.loads(BACKUP_TARGETS_FILE.read_text() or '{}')
            except Exception:
                data = {}
        data[name] = {
            'label': label or name,
            'path': str(root),
            'kind': kind or 'custom',
            'roles': ['backups'],
            'enabled': bool(enabled),
        }
        BACKUP_TARGETS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data[name]

    def delete_target(self, name: str) -> None:
        if name == 'default':
            raise ValueError('The default backup target cannot be deleted')
        if not BACKUP_TARGETS_FILE.exists():
            return
        data = json.loads(BACKUP_TARGETS_FILE.read_text() or '{}')
        data.pop(name, None)
        BACKUP_TARGETS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def target_by_name(self, target_name: str | None = None) -> dict[str, Any]:
        target_name = target_name or 'default'
        for target in self.list_targets():
            if target['name'] == target_name:
                if not target.get('enabled', True):
                    raise RuntimeError(f'Backup target is disabled: {target_name}')
                path = Path(target['path'])
                path.mkdir(parents=True, exist_ok=True)
                if not os.access(path, os.W_OK):
                    raise RuntimeError(f'Backup target is not writable: {path}')
                return target
        raise RuntimeError(f'Backup target not found: {target_name}')

    def retention_policy(self) -> dict[str, object]:
        return {
            'backup_keep_last': int(self.settings.backup_keep_last),
            'backup_path': str(self.backup_root),
            'policy': 'keep_last_per_vm_per_target',
            'format': BACKUP_FORMAT,
        }

    # ---------------------------------------------------------------------
    # Backup / listing / deletion
    # ---------------------------------------------------------------------
    def create_backup(
        self,
        vm_name: str,
        compress: bool = True,
        require_shutdown: bool | None = None,
        target_name: str | None = None,
    ) -> BackupResult:
        require_shutdown = self.settings.backup_require_shutdown if require_shutdown is None else require_shutdown
        target = self.target_by_name(target_name)
        target_root = Path(target['path'])

        conn = libvirt.open(self.settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt')

        try:
            dom = conn.lookupByName(vm_name)
            is_active = bool(dom.isActive())
            if require_shutdown and is_active:
                raise RuntimeError('Shutdown-only backup is enabled. Stop the VM before backing it up.')

            state_code, _ = dom.state()
            state = self._state_name(state_code)
            xml_text = dom.XMLDesc(0)
            disks = disk_records_from_domain_xml(xml_text)

            safe_vm = safe_name(vm_name)
            stamp = _utc_stamp()
            dest = target_root / safe_vm / stamp
            dest.mkdir(parents=True, exist_ok=False)
            disk_dir = dest / 'disks'
            disk_dir.mkdir()

            xml_path = dest / f'{safe_vm}.xml'
            xml_path.write_text(xml_text, encoding='utf-8')

            disk_metadata: list[dict[str, Any]] = []
            for record in disks:
                source = record.get('source') or ''
                index = int(record.get('index') or (len(disk_metadata) + 1))
                if not source or (source.startswith('/') and not Path(source).exists()):
                    disk_metadata.append({**record, 'copied': False, 'error': 'source missing'})
                    continue

                target_file = disk_dir / f'disk{index}.qcow2'
                qemu_convert(source, str(target_file), 'qcow2')
                info = {
                    **record,
                    'copied': True,
                    'backup_image': str(target_file),
                    'backup_format': 'qcow2',
                    'virtual_size_gb': qemu_virtual_size_gb(str(target_file), fallback_gb=1),
                }
                disk_metadata.append(info)

            metadata = {
                'format': BACKUP_FORMAT,
                'vm_name': vm_name,
                'uuid': dom.UUIDString(),
                'created_at': stamp,
                'state': state,
                'was_running': is_active,
                'backup_consistency': 'crash-consistent' if is_active else 'offline-consistent',
                'target': {'name': target['name'], 'path': target['path'], 'kind': target.get('kind')},
                'libvirt_uri': self.settings.libvirt_uri,
                'xml': str(xml_path),
                'disks': disk_metadata,
                'notes': [],
            }
            if is_active:
                metadata['notes'].append('VM was running. Backup is crash-consistent unless the guest flushed application data itself.')

            metadata_path = dest / 'metadata.json'
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

            archive_path = None
            if compress:
                archive_path = str(self._archive(dest))

            self._prune(vm_name, target_name=target['name'])
            return BackupResult(vm_name, str(dest), archive_path, str(metadata_path), len(disk_metadata), str(xml_path))
        finally:
            conn.close()

    def list_backups(self, vm_name: str | None = None) -> list[dict[str, Any]]:
        backups: list[dict[str, Any]] = []
        for target in self.list_targets():
            root = Path(target['path'])
            if not root.exists():
                continue
            vm_roots = [root / safe_name(vm_name)] if vm_name else [p for p in root.iterdir() if p.is_dir()]
            for vm_root in vm_roots:
                if not vm_root.exists():
                    continue
                for item in vm_root.iterdir():
                    metadata_path = item / 'metadata.json'
                    if not item.is_dir() or not metadata_path.exists():
                        continue
                    try:
                        data = json.loads(metadata_path.read_text(encoding='utf-8'))
                    except Exception:
                        data = {}
                    archive = item.with_suffix('.tar.gz')
                    backups.append({
                        'vm_name': data.get('vm_name') or vm_root.name,
                        'created_at': data.get('created_at') or item.name,
                        'path': str(item),
                        'archive': str(archive) if archive.exists() else None,
                        'disk_count': len(data.get('disks') or []),
                        'state': data.get('state'),
                        'format': data.get('format', 'unknown'),
                        'target_name': target['name'],
                        'target_kind': target.get('kind'),
                        'consistency': data.get('backup_consistency') or 'unknown',
                        'size_gb': self._dir_size_gb(item),
                    })
        return sorted(backups, key=lambda b: b['created_at'], reverse=True)

    def delete_backup(self, backup_dir: str) -> None:
        backup_path = self._validate_backup_dir(backup_dir)
        archive = backup_path.with_suffix('.tar.gz')
        shutil.rmtree(backup_path, ignore_errors=False)
        archive.unlink(missing_ok=True)

    # ---------------------------------------------------------------------
    # Restore
    # ---------------------------------------------------------------------
    def restore_as_new_vm(
        self,
        backup_dir: str,
        new_name: str,
        storage_pool: str | None = None,
        network_name: str | None = None,
        start_after_restore: bool = False,
    ) -> dict[str, str]:
        new_name = validate_simple_name(new_name, 'new VM name')
        backup_path = self._validate_backup_dir(backup_dir)
        metadata = json.loads((backup_path / 'metadata.json').read_text(encoding='utf-8'))
        xml_path = Path(metadata.get('xml') or '')
        if not xml_path.exists():
            xml_path = next(backup_path.glob('*.xml'), None)
        if not xml_path or not xml_path.exists():
            raise ValueError('Backup is missing domain XML')

        storage_pool = storage_pool or self.settings.default_storage_pool
        domain_xml = xml_path.read_text(encoding='utf-8')
        root = ET.fromstring(domain_xml)

        name_elem = root.find('./name')
        if name_elem is None:
            name_elem = ET.SubElement(root, 'name')
        name_elem.text = new_name
        uuid_elem = root.find('./uuid')
        if uuid_elem is not None:
            root.remove(uuid_elem)

        for graphics in root.findall('./devices/graphics'):
            if graphics.get('type') == 'vnc':
                graphics.set('port', '-1')
                graphics.set('autoport', 'yes')

        for iface in root.findall('./devices/interface'):
            mac = iface.find('mac')
            if mac is not None:
                iface.remove(mac)
            if network_name:
                source = iface.find('source')
                if source is None:
                    source = ET.SubElement(iface, 'source')
                source.attrib.clear()
                source.set('network', network_name)
                iface.set('type', 'network')
            vlan = iface.find('vlan')
            if vlan is not None:
                iface.remove(vlan)

        disk_records = [d for d in metadata.get('disks') or [] if d.get('copied') and d.get('backup_image')]
        restored: list[str] = []
        disk_index = 0
        for disk_elem in root.findall('./devices/disk'):
            if disk_elem.get('device') != 'disk':
                continue
            if disk_index >= len(disk_records):
                break
            record = disk_records[disk_index]
            disk_index += 1
            backup_image = Path(record['backup_image'])
            if not backup_image.exists():
                alt = backup_path / 'disks' / backup_image.name
                backup_image = alt
            if not backup_image.exists():
                raise FileNotFoundError(f'Missing backup disk image: {record.get("backup_image")}')

            size_gb = int(record.get('virtual_size_gb') or qemu_virtual_size_gb(str(backup_image), 1))
            volume_name = f'{new_name}-disk{disk_index}'
            vol = create_volume_from_image(storage_pool, volume_name, str(backup_image), size_gb, prefer_format='qcow2')
            set_disk_source(disk_elem, vol, target_dev=record.get('target_dev') or f'vd{chr(ord("a") + disk_index - 1)}')
            restored.append(vol['path'])

        desc = root.find('./description')
        if desc is not None and desc.text:
            desc.text = desc.text.replace('[ATLASVM_TEMPLATE]', '').strip()

        conn = libvirt.open(self.settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt')
        try:
            try:
                conn.lookupByName(new_name)
                raise ValueError(f'VM already exists: {new_name}')
            except libvirt.libvirtError:
                pass
            domain = conn.defineXML(ET.tostring(root, encoding='unicode'))
            if domain is None:
                raise RuntimeError('libvirt failed to define restored VM')
            if start_after_restore:
                domain.create()
            return {'status': 'ok', 'name': domain.name(), 'disks': ', '.join(restored)}
        finally:
            conn.close()

    def restore_definition(self, backup_dir: str, new_name: str | None = None) -> dict[str, str]:
        backup_path = self._validate_backup_dir(backup_dir)
        metadata_path = backup_path / 'metadata.json'
        xml_path = next(backup_path.glob('*.xml'), None)
        if not metadata_path.exists() or xml_path is None:
            raise ValueError('Backup directory does not contain metadata.json and VM XML')
        xml = xml_path.read_text(encoding='utf-8')
        if new_name:
            xml = self._replace_name(xml, new_name)
        conn = libvirt.open(self.settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt')
        try:
            domain = conn.defineXML(xml)
            if domain is None:
                raise RuntimeError('libvirt failed to define restored VM')
            return {'status': 'ok', 'name': domain.name()}
        finally:
            conn.close()

    # ---------------------------------------------------------------------
    # Retention / health
    # ---------------------------------------------------------------------
    def prune_backups(self, vm_name: str | None = None, keep_last: int | None = None, target_name: str | None = None) -> dict[str, object]:
        keep = max(0, int(self.settings.backup_keep_last if keep_last is None else keep_last))
        deleted: list[str] = []
        targets = [self.target_by_name(target_name)] if target_name else self.list_targets()
        for target in targets:
            root = Path(target['path'])
            if not root.exists():
                continue
            vm_names = [vm_name] if vm_name else [p.name for p in root.iterdir() if p.is_dir()]
            for item_vm in vm_names:
                all_backups = [b for b in self.list_backups(item_vm) if b.get('target_name') == target['name']]
                for old in all_backups[keep:]:
                    path = Path(old['path'])
                    if path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                        deleted.append(str(path))
                    if old.get('archive'):
                        archive = Path(old['archive'])
                        archive.unlink(missing_ok=True)
                        deleted.append(str(archive))
        return {'status': 'ok', 'keep_last': keep, 'deleted': deleted, 'deleted_count': len(deleted)}

    def backup_health(self) -> dict[str, Any]:
        backups = self.list_backups()
        latest_by_vm: dict[str, dict[str, Any]] = {}
        for backup in backups:
            latest_by_vm.setdefault(backup['vm_name'], backup)
        return {
            'backup_count': len(backups),
            'latest_by_vm': latest_by_vm,
            'targets': self.list_targets(),
        }

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------
    def _validate_backup_dir(self, backup_dir: str) -> Path:
        backup_path = Path(backup_dir).resolve()
        valid_roots = [Path(t['path']).resolve() for t in self.list_targets()]
        if not any(root == backup_path or root in backup_path.parents for root in valid_roots):
            raise ValueError('Backup path is outside configured AtlasVM backup targets')
        if not (backup_path / 'metadata.json').exists():
            raise ValueError('Backup directory does not contain metadata.json')
        return backup_path

    def _archive(self, source_dir: Path) -> Path:
        archive_path = source_dir.with_suffix('.tar.gz')
        with tarfile.open(archive_path, 'w:gz') as tar:
            tar.add(source_dir, arcname=source_dir.name)
        return archive_path

    def _prune(self, vm_name: str, target_name: str | None = None) -> None:
        keep = max(0, int(self.settings.backup_keep_last))
        if keep <= 0:
            return
        self.prune_backups(vm_name=vm_name, keep_last=keep, target_name=target_name)

    def _dir_size_gb(self, path: Path) -> float:
        total = 0
        try:
            for item in path.rglob('*'):
                if item.is_file():
                    total += item.stat().st_size
        except Exception:
            pass
        return round(total / 1024 / 1024 / 1024, 2)

    def _replace_name(self, xml: str, new_name: str) -> str:
        validate_simple_name(new_name, 'VM name')
        xml = re.sub(r'<name>.*?</name>', f'<name>{new_name}</name>', xml, count=1)
        xml = re.sub(r'<uuid>.*?</uuid>', '', xml, count=1)
        return xml

    def _state_name(self, state_code: int) -> str:
        return {
            libvirt.VIR_DOMAIN_NOSTATE: 'nostate',
            libvirt.VIR_DOMAIN_RUNNING: 'running',
            libvirt.VIR_DOMAIN_BLOCKED: 'blocked',
            libvirt.VIR_DOMAIN_PAUSED: 'paused',
            libvirt.VIR_DOMAIN_SHUTDOWN: 'shutdown',
            libvirt.VIR_DOMAIN_SHUTOFF: 'shutoff',
            libvirt.VIR_DOMAIN_CRASHED: 'crashed',
            libvirt.VIR_DOMAIN_PMSUSPENDED: 'suspended',
        }.get(state_code, 'unknown')
