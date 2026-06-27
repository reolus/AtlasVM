from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any

from app.core.config import get_settings


def check_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(('127.0.0.1', port)) != 0


def run_doctor() -> list[dict[str, Any]]:
    settings = get_settings()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = '') -> None:
        checks.append({'name': name, 'ok': ok, 'status': 'ok' if ok else 'warning', 'detail': detail})

    add('qemu-img installed', shutil.which('qemu-img') is not None, shutil.which('qemu-img') or 'missing')
    add('virsh installed', shutil.which('virsh') is not None, shutil.which('virsh') or 'missing')
    add('websockify installed', shutil.which('websockify') is not None, shutil.which('websockify') or 'missing')
    add('noVNC files available', Path('/usr/share/novnc/vnc.html').exists(), '/usr/share/novnc/vnc.html')
    add('backup path exists/writable', Path(settings.backup_path).exists() and Path(settings.backup_path).is_dir(), settings.backup_path)
    add('ISO path exists', Path(settings.iso_path).exists(), settings.iso_path)
    add('console public host set', bool(settings.console_public_host), settings.console_public_host or 'falls back to request host')
    add('console base port available or reusable', check_port_available(settings.console_port_base), str(settings.console_port_base))

    try:
        import libvirt
        conn = libvirt.open(settings.libvirt_uri)
        add('libvirt connection', conn is not None, settings.libvirt_uri)
        if conn:
            conn.close()
    except Exception as exc:
        add('libvirt connection', False, str(exc))

    try:
        import subprocess
        result = subprocess.run(['zpool', 'list', '-H'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        add('ZFS pool visibility', result.returncode == 0, result.stdout.strip() or result.stderr.strip())
    except Exception as exc:
        add('ZFS pool visibility', False, str(exc))

    return checks
