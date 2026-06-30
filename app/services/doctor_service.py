from __future__ import annotations

import json
import os
import py_compile
import shutil
import socket
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.core.config import get_settings

APP_ROOT = Path('/opt/atlasvm')


def check_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(('127.0.0.1', port)) != 0


def shell(cmd: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, '', str(exc))


def run_doctor() -> list[dict[str, Any]]:
    settings = get_settings()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = '', category: str = 'General', status: str | None = None) -> None:
        severity = status or ('ok' if ok else 'warning')
        checks.append({'name': name, 'ok': bool(ok), 'status': severity, 'severity': severity, 'category': category, 'detail': (detail or '').strip()})

    for binary in ['qemu-img', 'virsh', 'websockify', 'zpool', 'zfs', 'iscsiadm', 'pvs', 'vgs', 'lvs']:
        exists = shutil.which(binary) is not None
        add(f'{binary} installed', exists, shutil.which(binary) or 'missing', 'Core tools', 'ok' if exists else 'warning')

    add('noVNC files available', Path('/usr/share/novnc/vnc.html').exists(), '/usr/share/novnc/vnc.html', 'Console')
    add('ISO path exists', Path(settings.iso_path).exists(), settings.iso_path, 'Paths')
    add('backup path exists/writable', Path(settings.backup_path).exists() and os.access(settings.backup_path, os.W_OK), settings.backup_path, 'Backups')
    add('console public host set', bool(settings.console_public_host), settings.console_public_host or 'falls back to request host', 'Console', 'info')
    add('console base port available or reusable', check_port_available(settings.console_port_base), str(settings.console_port_base), 'Console', 'ok' if check_port_available(settings.console_port_base) else 'info')

    compile_errors = []
    app_path = Path('app') if Path('app').exists() else APP_ROOT / 'app'
    if app_path.exists():
        for pyfile in sorted(app_path.rglob('*.py')):
            try:
                py_compile.compile(str(pyfile), doraise=True)
            except Exception as exc:
                compile_errors.append(f'{pyfile}: {exc}')
    add('Python files compile', not compile_errors, '; '.join(compile_errors) if compile_errors else 'all app Python files compiled', 'Application')

    try:
        import libvirt
        conn = libvirt.open(settings.libvirt_uri)
        add('libvirt connection', conn is not None, settings.libvirt_uri, 'Libvirt')
        if conn:
            networks = set(conn.listNetworks() or []) | set(conn.listDefinedNetworks() or [])
            pools = set(conn.listStoragePools() or []) | set(conn.listDefinedStoragePools() or [])
            add('default network exists', settings.default_network in networks, settings.default_network, 'Libvirt')
            add('default storage pool exists', settings.default_storage_pool in pools, settings.default_storage_pool, 'Libvirt')

            vm_vlan = []
            missing_sources = []
            for dom in conn.listAllDomains(0):
                root = ET.fromstring(dom.XMLDesc(0))
                for iface in root.findall('./devices/interface'):
                    mac = iface.find('mac')
                    if iface.find('vlan') is not None:
                        vm_vlan.append(f"{dom.name()}:{mac.get('address') if mac is not None else 'unknown'}")
                for disk in root.findall('./devices/disk'):
                    if disk.get('device') != 'disk':
                        continue
                    src = disk.find('source')
                    source = (src.get('file') or src.get('dev') or src.get('name')) if src is not None else ''
                    if source.startswith('/') and not Path(source).exists():
                        missing_sources.append(f'{dom.name()}:{source}')
            add('no VM-side VLAN tags', not vm_vlan, '; '.join(vm_vlan) if vm_vlan else 'none detected', 'VM networking', 'ok' if not vm_vlan else 'warning')
            add('VM disk sources exist', not missing_sources, '; '.join(missing_sources) if missing_sources else 'all visible disk sources exist', 'VM storage', 'ok' if not missing_sources else 'warning')
            conn.close()
    except Exception as exc:
        add('libvirt checks', False, str(exc), 'Libvirt')

    for cmd, category, name in [
        (['zpool', 'status', '-x'], 'ZFS', 'zpool health'),
        (['vgs', '--noheadings', '-o', 'vg_name,vg_size,vg_free'], 'LVM', 'volume groups visible'),
        (['lvs', '-a', '-o', 'lv_name,vg_name,lv_size,lv_attr,pool_lv,data_percent,metadata_percent', '--noheadings'], 'LVM', 'logical volumes visible'),
        (['iscsiadm', '-m', 'session'], 'iSCSI', 'active sessions'),
    ]:
        if shutil.which(cmd[0]) is None:
            continue
        result = shell(cmd)
        ok = result.returncode == 0
        detail = result.stdout.strip() or result.stderr.strip()
        add(name, ok, detail or 'no output', category, 'ok' if ok else 'info')

    try:
        from app.services.backup_service import BackupService
        svc = BackupService()
        targets = svc.list_targets()
        backups = svc.list_backups()
        bad_targets = [t['name'] for t in targets if not (t.get('enabled') and t.get('exists') and t.get('writable'))]
        add('backup targets configured', bool(targets), f'{len(targets)} target(s)', 'Backups')
        add('backup targets writable', not bad_targets, ', '.join(bad_targets) if bad_targets else 'all enabled targets writable', 'Backups', 'ok' if not bad_targets else 'warning')
        add('backup inventory readable', True, f'{len(backups)} backup(s)', 'Backups')
    except Exception as exc:
        add('backup service loads', False, str(exc), 'Backups')

    return checks
