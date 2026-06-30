from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.services.doctor_service import run_doctor
from app.services.node_registry import local_node_self
from app.services.vm_inventory import list_vm_inventory


def run(cmd: list[str], timeout: int = 6) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, '', str(exc))


def service_state(name: str) -> str:
    result = run(['systemctl', 'is-active', name], timeout=3)
    return result.stdout.strip() or 'unknown'


def meminfo() -> dict[str, int]:
    data: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            data[key] = int(value.strip().split()[0]) * 1024
    except Exception:
        pass
    return data


def host_health() -> dict[str, Any]:
    mem = meminfo()
    total = mem.get('MemTotal', 0)
    available = mem.get('MemAvailable', 0)
    used = max(total - available, 0) if total else 0

    root = os.statvfs('/')
    root_total = root.f_blocks * root.f_frsize
    root_free = root.f_bavail * root.f_frsize
    root_used = root_total - root_free

    load1, load5, load15 = os.getloadavg()

    return {
        'hostname': local_node_self().get('hostname'),
        'management_ip': local_node_self().get('management_ip'),
        'uptime_seconds': float(Path('/proc/uptime').read_text().split()[0]) if Path('/proc/uptime').exists() else 0,
        'load': {'1m': load1, '5m': load5, '15m': load15},
        'memory': {'total': total, 'used': used, 'available': available, 'used_percent': round((used / total) * 100, 2) if total else 0},
        'root_disk': {'total': root_total, 'used': root_used, 'free': root_free, 'used_percent': round((root_used / root_total) * 100, 2) if root_total else 0},
        'services': {
            'atlasvm': service_state('atlasvm.service'),
            'nginx': service_state('nginx.service'),
            'libvirtd': service_state('libvirtd.service'),
            'virtqemud': service_state('virtqemud.service'),
            'iscsid': service_state('iscsid.service'),
        },
        'time': int(time.time()),
    }


def libvirt_inventory() -> dict[str, Any]:
    try:
        import libvirt
        from app.core.config import get_settings

        settings = get_settings()
        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            return {'ok': False, 'error': 'Could not connect to libvirt.'}

        try:
            networks = []
            active_networks = set(conn.listNetworks() or [])
            defined_networks = set(conn.listDefinedNetworks() or [])
            for name in sorted(active_networks | defined_networks):
                networks.append({'name': name, 'active': name in active_networks})

            pools = []
            active_pools = set(conn.listStoragePools() or [])
            defined_pools = set(conn.listDefinedStoragePools() or [])
            for name in sorted(active_pools | defined_pools):
                try:
                    pool = conn.storagePoolLookupByName(name)
                    pools.append({'name': name, 'active': bool(pool.isActive()), 'xml': pool.XMLDesc()[:500]})
                except Exception as exc:
                    pools.append({'name': name, 'active': name in active_pools, 'error': str(exc)})

            vms = []
            for dom in conn.listAllDomains(0):
                try:
                    vms.append({'name': dom.name(), 'uuid': dom.UUIDString(), 'active': bool(dom.isActive()), 'state': dom.state()[0]})
                except Exception as exc:
                    vms.append({'name': 'unknown', 'error': str(exc)})

            return {'ok': True, 'networks': networks, 'pools': pools, 'vms': vms}
        finally:
            conn.close()
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def doctor_summary() -> dict[str, int]:
    summary = {'ok': 0, 'warning': 0, 'error': 0, 'info': 0, 'total': 0}
    for check in run_doctor():
        summary['total'] += 1
        sev = check.get('severity') or check.get('status') or ('ok' if check.get('ok') else 'warning')
        if sev not in summary:
            sev = 'warning'
        summary[sev] += 1
    return summary


def node_inventory() -> dict[str, Any]:
    vm_inventory = {'total': 0, 'running': 0, 'offline': 0, 'vms': [], 'error': ''}
    try:
        vm_inventory = list_vm_inventory()
    except Exception as exc:
        vm_inventory['error'] = str(exc)

    return {
        'self': local_node_self(),
        'health': host_health(),
        'libvirt': libvirt_inventory(),
        'vm_inventory': vm_inventory,
        'doctor': doctor_summary(),
    }
