from __future__ import annotations

import json
import os
import py_compile
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.core.config import get_settings

APP_ROOT = Path('/opt/atlasvm')

METADATA_FILES = [
    APP_ROOT / 'atlasvm_networks.json',
    APP_ROOT / 'atlasvm_network_meta.json',
    APP_ROOT / 'atlasvm_host_network.json',
    APP_ROOT / 'atlasvm_host_network_state.json',
    APP_ROOT / 'atlasvm_iscsi_targets.json',
    APP_ROOT / 'atlasvm_nfs_targets.json',
    APP_ROOT / 'atlasvm_smb_targets.json',
    APP_ROOT / 'atlasvm_storage_networks.json',
]


def shell(cmd: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or '', exc.stderr or 'command timed out')
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, '', str(exc))


def one_line(value: str, limit: int = 500) -> str:
    value = (value or '').strip().replace('\n', ' | ')
    if len(value) > limit:
        return value[: limit - 3] + '...'
    return value


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(('127.0.0.1', int(port))) != 0


def check_systemd_unit(unit: str) -> dict[str, str]:
    active = shell(['systemctl', 'is-active', unit], timeout=3).stdout.strip()
    enabled = shell(['systemctl', 'is-enabled', unit], timeout=3).stdout.strip()
    return {'active': active or 'unknown', 'enabled': enabled or 'unknown'}


def parse_json_file(path: Path) -> tuple[bool, str]:
    try:
        if not path.exists():
            return True, 'not present'

        data = json.loads(path.read_text() or '{}')

        if isinstance(data, dict):
            return True, f'{len(data)} item(s)'
        if isinstance(data, list):
            return True, f'{len(data)} item(s)'

        return True, type(data).__name__
    except Exception as exc:
        return False, str(exc)


def run_doctor() -> list[dict[str, Any]]:
    settings = get_settings()
    checks: list[dict[str, Any]] = []

    def add(category: str, name: str, ok: bool, detail: str = '', severity: str | None = None) -> None:
        if severity is None:
            severity = 'ok' if ok else 'warning'

        checks.append({
            'category': category,
            'name': name,
            'ok': bool(ok),
            'status': severity,
            'severity': severity,
            'detail': one_line(detail),
        })

    def info(category: str, name: str, detail: str) -> None:
        add(category, name, True, detail, 'info')

    started = time.time()

    for binary in ['python3', 'virsh', 'qemu-img', 'qemu-system-x86_64', 'websockify']:
        add('Core tools', f'{binary} installed', command_exists(binary), shutil.which(binary) or 'missing')

    for binary in ['zpool', 'zfs', 'iscsiadm', 'pvs', 'vgs', 'lvs', 'lsblk']:
        add(
            'Storage tools',
            f'{binary} installed',
            command_exists(binary),
            shutil.which(binary) or 'missing',
            None if command_exists(binary) else 'warning',
        )

    add('Console', 'noVNC files available', Path('/usr/share/novnc/vnc.html').exists(), '/usr/share/novnc/vnc.html')

    console_port_ok = port_available(settings.console_port_base)
    add(
        'Console',
        'console port base available or reusable',
        console_port_ok,
        str(settings.console_port_base),
        'ok' if console_port_ok else 'info',
    )

    info('Console', 'console public host', settings.console_public_host or 'falls back to request host')

    if command_exists('systemctl'):
        for unit in [
            'atlasvm.service',
            'nginx.service',
            'libvirtd.service',
            'virtqemud.service',
            'iscsid.service',
            'open-iscsi.service',
            'systemd-networkd.service',
            'atlasvm-network-reconcile.service',
        ]:
            state = check_systemd_unit(unit)

            if unit in {'libvirtd.service', 'virtqemud.service'} and state['active'] in {'unknown', 'inactive', 'failed'}:
                severity = 'info'
            elif unit in {'iscsid.service', 'open-iscsi.service', 'atlasvm-network-reconcile.service'} and state['active'] in {'unknown', 'inactive'}:
                severity = 'info'
            else:
                severity = 'ok' if state['active'] == 'active' else 'warning'

            add(
                'System services',
                unit,
                state['active'] == 'active' or severity == 'info',
                f"active={state['active']}, enabled={state['enabled']}",
                severity,
            )

    paths = [
        ('App root', APP_ROOT, True),
        ('ISO path', Path(settings.iso_path), True),
        ('Template path', Path(settings.template_path), False),
        ('Backup path', Path(settings.backup_path), True),
        ('VM disk path', Path(settings.vm_disk_path), True),
    ]

    for label, path, must_exist in paths:
        exists = path.exists()
        writable = os.access(path, os.W_OK) if exists else False
        ok = exists and (writable or not path.is_dir())

        if not must_exist and not exists:
            add('Paths', label, True, f'{path} not present', 'info')
        else:
            add('Paths', label, ok, f'{path}; exists={exists}; writable={writable}')

    for path in METADATA_FILES:
        ok, detail = parse_json_file(path)
        add('Metadata', path.name, ok, detail, 'ok' if ok else 'warning')

    compile_errors = []
    if APP_ROOT.exists() and (APP_ROOT / 'app').exists():
        for pyfile in sorted((APP_ROOT / 'app').rglob('*.py')):
            try:
                py_compile.compile(str(pyfile), doraise=True)
            except Exception as exc:
                compile_errors.append(f'{pyfile.relative_to(APP_ROOT)}: {exc}')

    add(
        'Application',
        'Python files compile',
        not compile_errors,
        '; '.join(compile_errors) if compile_errors else 'all app/*.py files compiled',
    )

    try:
        import libvirt

        conn = libvirt.open(settings.libvirt_uri)
        add('Libvirt', 'libvirt connection', conn is not None, settings.libvirt_uri)

        if conn:
            try:
                active_networks = set(conn.listNetworks() or [])
                inactive_networks = set(conn.listDefinedNetworks() or [])
                all_networks = sorted(active_networks | inactive_networks)

                add('Libvirt networks', 'networks visible', bool(all_networks), ', '.join(all_networks) or 'none')
                add(
                    'Libvirt networks',
                    f'default network {settings.default_network}',
                    settings.default_network in all_networks,
                    'active' if settings.default_network in active_networks else 'inactive or missing',
                )
            except Exception as exc:
                add('Libvirt networks', 'network inventory', False, str(exc))

            try:
                active_pools = set(conn.listStoragePools() or [])
                inactive_pools = set(conn.listDefinedStoragePools() or [])
                all_pools = sorted(active_pools | inactive_pools)

                add('Libvirt storage', 'storage pools visible', bool(all_pools), ', '.join(all_pools) or 'none')
                add(
                    'Libvirt storage',
                    f'default pool {settings.default_storage_pool}',
                    settings.default_storage_pool in all_pools,
                    'active' if settings.default_storage_pool in active_pools else 'inactive or missing',
                )
                add(
                    'Libvirt storage',
                    f'ISO pool {settings.iso_pool}',
                    settings.iso_pool in all_pools,
                    'active' if settings.iso_pool in active_pools else 'inactive or missing',
                )

                for pool_name in all_pools:
                    try:
                        pool = conn.storagePoolLookupByName(pool_name)
                        root = ET.fromstring(pool.XMLDesc())
                        pool_type = root.get('type', '')
                        target = root.findtext('./target/path') or ''
                        state = 'active' if pool.isActive() else 'inactive'
                        severity = 'ok' if pool.isActive() else 'warning'

                        add(
                            'Libvirt storage',
                            f'pool {pool_name}',
                            pool.isActive(),
                            f'type={pool_type}; state={state}; target={target}',
                            severity,
                        )
                    except Exception as exc:
                        add('Libvirt storage', f'pool {pool_name}', False, str(exc))
            except Exception as exc:
                add('Libvirt storage', 'storage inventory', False, str(exc))

            try:
                vm_names = sorted((conn.listDefinedDomains() or []) + [dom.name() for dom in conn.listAllDomains() if dom.isActive()])
                vm_names = sorted(set(vm_names), key=lambda value: value.lower())

                info('VM inventory', 'VM count', str(len(vm_names)))

                network_set = set()
                try:
                    network_set = set(conn.listNetworks() or []) | set(conn.listDefinedNetworks() or [])
                except Exception:
                    pass

                vlan_warnings = []
                missing_sources = []
                xml_errors = []

                for vm_name in vm_names:
                    try:
                        dom = conn.lookupByName(vm_name)
                        xml = dom.XMLDesc(0)
                        root = ET.fromstring(xml)

                        for iface in root.findall('./devices/interface'):
                            mac_el = iface.find('mac')
                            mac = mac_el.get('address', '') if mac_el is not None else 'unknown-mac'

                            source_el = iface.find('source')
                            network = source_el.get('network') if source_el is not None else None

                            if network and network not in network_set:
                                missing_sources.append(f'{vm_name}:{mac}->{network}')

                            if iface.find('vlan') is not None:
                                vlan_warnings.append(f'{vm_name}:{mac}')

                        for disk in root.findall('./devices/disk'):
                            if disk.get('device') != 'disk':
                                continue

                            source_el = disk.find('source')
                            source = ''

                            if source_el is not None:
                                source = source_el.get('file') or source_el.get('dev') or source_el.get('name') or ''

                            if source and source.startswith('/') and not Path(source).exists():
                                missing_sources.append(f'{vm_name}:disk->{source}')
                    except Exception as exc:
                        xml_errors.append(f'{vm_name}: {exc}')

                add(
                    'VM inventory',
                    'VM XML parse',
                    not xml_errors,
                    '; '.join(xml_errors) if xml_errors else 'all VM XML parsed',
                )

                add(
                    'VM networking',
                    'no VM-side VLAN tags',
                    not vlan_warnings,
                    '; '.join(vlan_warnings) if vlan_warnings else 'no double-tagged VM NICs detected',
                    'ok' if not vlan_warnings else 'warning',
                )

                add(
                    'VM inventory',
                    'VM references resolve',
                    not missing_sources,
                    '; '.join(missing_sources) if missing_sources else 'network and disk references look resolvable',
                    'ok' if not missing_sources else 'warning',
                )
            except Exception as exc:
                add('VM inventory', 'VM inventory checks', False, str(exc))

            conn.close()
    except Exception as exc:
        add('Libvirt', 'libvirt connection', False, str(exc))

    if command_exists('zpool'):
        result = shell(['zpool', 'list', '-H', '-o', 'name,health,capacity'], timeout=8)
        add('ZFS', 'zpool list', result.returncode == 0, result.stdout.strip() or result.stderr.strip())

        status = shell(['zpool', 'status', '-x'], timeout=8)
        detail = status.stdout.strip() or status.stderr.strip()
        healthy = status.returncode == 0 and ('all pools are healthy' in detail.lower() or 'no pools available' not in detail.lower())

        add('ZFS', 'zpool status', healthy, detail, 'ok' if healthy else 'warning')

    if command_exists('vgs'):
        result = shell(['vgs', '--noheadings', '-o', 'vg_name,vg_size,vg_free'], timeout=8)
        add(
            'LVM',
            'volume groups visible',
            result.returncode == 0,
            result.stdout.strip() or result.stderr.strip(),
            'ok' if result.returncode == 0 else 'warning',
        )

    if command_exists('lvs'):
        result = shell(['lvs', '-a', '-o', 'lv_name,vg_name,lv_size,lv_attr,pool_lv,data_percent,metadata_percent', '--noheadings'], timeout=8)
        add(
            'LVM',
            'logical volumes visible',
            result.returncode == 0,
            result.stdout.strip() or result.stderr.strip(),
            'ok' if result.returncode == 0 else 'warning',
        )

    if command_exists('iscsiadm'):
        sessions = shell(['iscsiadm', '-m', 'session'], timeout=8)
        ok = sessions.returncode == 0

        add(
            'iSCSI',
            'active sessions',
            ok,
            sessions.stdout.strip() or sessions.stderr.strip() or 'no active sessions',
            'ok' if ok else 'info',
        )

    try:
        from app.services.storage_phase9 import storage_overview

        overview = storage_overview()

        add('Phase 9 storage', 'storage overview loads', True, ', '.join(sorted(overview.keys())))
        add('Phase 9 storage', 'libvirt pools in overview', bool(overview.get('libvirt_pools')), f"{len(overview.get('libvirt_pools') or [])} pool(s)")

        if 'iscsi_targets' in overview:
            targets = overview.get('iscsi_targets') or {}
            add('Phase 9 storage', 'iSCSI target metadata', True, f'{len(targets)} target(s)', 'info')

        if 'lvm_summary' in overview:
            summary = overview.get('lvm_summary') or {}
            vgs_list = summary.get('vgs') or [] if isinstance(summary, dict) else []
            add('Phase 9 storage', 'LVM summary', True, f'{len(vgs_list)} volume group(s)', 'info')
    except Exception as exc:
        add('Phase 9 storage', 'storage overview loads', False, str(exc))

    info('Doctor', 'runtime', f'{time.time() - started:.2f}s')

    return checks


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {'ok': 0, 'warning': 0, 'error': 0, 'info': 0, 'total': len(checks)}

    for check in checks:
        status = check.get('severity') or check.get('status') or ('ok' if check.get('ok') else 'warning')
        if status not in summary:
            status = 'warning'
        summary[status] += 1

    return summary
