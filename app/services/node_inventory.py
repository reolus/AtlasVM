from __future__ import annotations

import os
import subprocess
import time
import xml.etree.ElementTree as ET
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



def lvm_inventory() -> dict[str, Any]:
    data: dict[str, Any] = {'vgs': [], 'lvs': [], 'ok': True, 'error': ''}

    vgs_result = run(['vgs', '--noheadings', '--separator', '|', '-o', 'vg_name,vg_size,vg_free'], timeout=6)
    if vgs_result.returncode == 0:
        for line in vgs_result.stdout.splitlines():
            parts = [part.strip() for part in line.split('|')]
            if len(parts) >= 3 and parts[0]:
                data['vgs'].append({'name': parts[0], 'size': parts[1], 'free': parts[2]})
    else:
        data['ok'] = False
        data['error'] = vgs_result.stderr.strip() or vgs_result.stdout.strip()

    lvs_result = run(['lvs', '-a', '--noheadings', '--separator', '|', '-o', 'lv_name,vg_name,lv_size,lv_attr,pool_lv,data_percent,metadata_percent'], timeout=6)
    if lvs_result.returncode == 0:
        for line in lvs_result.stdout.splitlines():
            parts = [part.strip() for part in line.split('|')]
            if len(parts) >= 7 and parts[0]:
                data['lvs'].append({
                    'name': parts[0],
                    'vg_name': parts[1],
                    'size': parts[2],
                    'attr': parts[3],
                    'pool_lv': parts[4],
                    'data_percent': parts[5],
                    'metadata_percent': parts[6],
                })
    elif not data['error']:
        data['ok'] = False
        data['error'] = lvs_result.stderr.strip() or lvs_result.stdout.strip()

    return data


def iscsi_inventory() -> dict[str, Any]:
    sessions = []
    result = run(['iscsiadm', '-m', 'session'], timeout=6)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            sessions.append(line)
        return {'ok': True, 'sessions': sessions, 'error': ''}
    return {'ok': False, 'sessions': [], 'error': result.stderr.strip() or result.stdout.strip() or 'no active sessions'}


def zfs_inventory() -> dict[str, Any]:
    pools = []
    result = run(['zpool', 'list', '-H', '-o', 'name,health,capacity'], timeout=6)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                pools.append({'name': parts[0], 'health': parts[1], 'capacity': parts[2]})
        return {'ok': True, 'pools': pools, 'error': ''}
    return {'ok': False, 'pools': [], 'error': result.stderr.strip() or result.stdout.strip()}


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


def _pool_struct_from_xml(name: str, active: bool, xml_text: str) -> dict[str, Any]:
    item: dict[str, Any] = {'name': name, 'active': active, 'xml': xml_text[:500]}
    try:
        root = ET.fromstring(xml_text)
        item['type'] = root.get('type', '')
        item['target_path'] = root.findtext('./target/path') or ''
        item['source_name'] = root.findtext('./source/name') or ''
        item['source_device'] = ''
        device = root.find('./source/device')
        if device is not None:
            item['source_device'] = device.get('path', '')
        hosts = []
        for host in root.findall('./source/host'):
            hosts.append({'name': host.get('name', ''), 'port': host.get('port', '')})
        item['source_hosts'] = hosts
        capacity = root.find('./capacity')
        allocation = root.find('./allocation')
        available = root.find('./available')
        item['capacity_bytes'] = int(capacity.text or 0) if capacity is not None else 0
        item['allocation_bytes'] = int(allocation.text or 0) if allocation is not None else 0
        item['available_bytes'] = int(available.text or 0) if available is not None else 0
    except Exception as exc:
        item['parse_error'] = str(exc)
    return item


def _network_struct_from_xml(name: str, active: bool, xml_text: str) -> dict[str, Any]:
    item: dict[str, Any] = {'name': name, 'active': active, 'xml': xml_text[:500]}
    try:
        root = ET.fromstring(xml_text)
        bridge = root.find('bridge')
        if bridge is not None:
            item['bridge'] = bridge.get('name', '')
        forward = root.find('forward')
        if forward is not None:
            item['forward_mode'] = forward.get('mode', '')
        vlan = root.find('./vlan/tag')
        if vlan is not None:
            item['vlan_tag'] = vlan.get('id', '')
    except Exception as exc:
        item['parse_error'] = str(exc)
    return item


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
                try:
                    net = conn.networkLookupByName(name)
                    networks.append(_network_struct_from_xml(name, name in active_networks, net.XMLDesc()))
                except Exception as exc:
                    networks.append({'name': name, 'active': name in active_networks, 'error': str(exc)})

            pools = []
            active_pools = set(conn.listStoragePools() or [])
            defined_pools = set(conn.listDefinedStoragePools() or [])
            for name in sorted(active_pools | defined_pools):
                try:
                    pool = conn.storagePoolLookupByName(name)
                    pools.append(_pool_struct_from_xml(name, bool(pool.isActive()), pool.XMLDesc()))
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
        'storage': {
            'lvm': lvm_inventory(),
            'iscsi': iscsi_inventory(),
            'zfs': zfs_inventory(),
        },
        'vm_inventory': vm_inventory,
        'doctor': doctor_summary(),
    }
