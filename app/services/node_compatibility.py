from __future__ import annotations

import time
from typing import Any

from app.services.node_client import node_inventory_remote
from app.services.node_inventory import node_inventory
from app.services.node_registry import get_node, list_nodes, local_node_self


def _names(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get('name') or '').strip() for item in items if str(item.get('name') or '').strip()}


def _by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get('name') or '').strip(): item for item in items if str(item.get('name') or '').strip()}


def _storage_summary(inv: dict[str, Any]) -> dict[str, Any]:
    libvirt = inv.get('libvirt') or {}
    pools = libvirt.get('pools') or []
    storage = inv.get('storage') or {}
    lvm = storage.get('lvm') or {}
    iscsi = storage.get('iscsi') or {}
    zfs = storage.get('zfs') or {}
    return {
        'pools': pools,
        'pool_names': _names(pools),
        'pool_by_name': _by_name(pools),
        'vgs': lvm.get('vgs') or [],
        'vg_names': _names(lvm.get('vgs') or []),
        'iscsi_sessions': iscsi.get('sessions') or [],
        'zfs_pools': zfs.get('pools') or [],
        'zfs_pool_names': _names(zfs.get('pools') or []),
    }


def _network_summary(inv: dict[str, Any]) -> dict[str, Any]:
    networks = (inv.get('libvirt') or {}).get('networks') or []
    return {
        'networks': networks,
        'names': _names(networks),
        'by_name': _by_name(networks),
    }


def add_check(checks: list[dict[str, Any]], category: str, name: str, ok: bool, detail: str = '', severity: str | None = None) -> None:
    sev = severity or ('ok' if ok else 'warning')
    checks.append({
        'category': category,
        'name': name,
        'ok': bool(ok),
        'severity': sev,
        'status': sev,
        'detail': detail,
    })


def compare_inventories(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    local_self = local.get('self') or {}
    remote_self = remote.get('self') or {}

    local_version = str(local_self.get('version') or '')
    remote_version = str(remote_self.get('version') or '')
    add_check(
        checks,
        'Version',
        'AtlasVM versions match',
        bool(local_version and remote_version and local_version == remote_version),
        f'local={local_version or "unknown"}; remote={remote_version or "unknown"}',
    )

    remote_ok = bool(remote.get('ok', True)) and not remote.get('error')
    add_check(checks, 'Reachability', 'remote node inventory reachable', remote_ok, remote.get('error', 'remote inventory loaded'))

    local_time = int(((local.get('health') or {}).get('time')) or (local_self.get('time')) or time.time())
    remote_time = int(((remote.get('health') or {}).get('time')) or (remote_self.get('time')) or 0)
    drift = abs(local_time - remote_time) if remote_time else 0
    add_check(checks, 'Time', 'node clock drift under 60 seconds', remote_time and drift <= 60, f'drift={drift}s', 'ok' if remote_time and drift <= 60 else 'warning')

    local_services = (local.get('health') or {}).get('services') or {}
    remote_services = (remote.get('health') or {}).get('services') or {}
    for svc in ['atlasvm', 'nginx', 'libvirtd', 'iscsid']:
        local_state = local_services.get(svc, 'unknown')
        remote_state = remote_services.get(svc, 'unknown')
        desired = 'active' if svc != 'iscsid' else 'active'
        ok = local_state == desired and remote_state == desired
        add_check(checks, 'Services', f'{svc} active on both nodes', ok, f'local={local_state}; remote={remote_state}', 'ok' if ok else 'warning')

    local_libvirt = local.get('libvirt') or {}
    remote_libvirt = remote.get('libvirt') or {}
    add_check(checks, 'Libvirt', 'local libvirt inventory ok', bool(local_libvirt.get('ok')), local_libvirt.get('error', 'ok'))
    add_check(checks, 'Libvirt', 'remote libvirt inventory ok', bool(remote_libvirt.get('ok')), remote_libvirt.get('error', 'ok'))

    local_net = _network_summary(local)
    remote_net = _network_summary(remote)
    common_networks = sorted(local_net['names'] & remote_net['names'])
    missing_remote_networks = sorted(local_net['names'] - remote_net['names'])
    extra_remote_networks = sorted(remote_net['names'] - local_net['names'])

    add_check(checks, 'Networks', 'VM network names match', not missing_remote_networks, f'missing on remote: {", ".join(missing_remote_networks) or "none"}; extra remote: {", ".join(extra_remote_networks) or "none"}')

    mismatched_bridges = []
    for name in common_networks:
        l = local_net['by_name'].get(name, {})
        r = remote_net['by_name'].get(name, {})
        if (l.get('bridge') or '') != (r.get('bridge') or ''):
            mismatched_bridges.append(f'{name}: local={l.get("bridge", "") or "none"}, remote={r.get("bridge", "") or "none"}')
    add_check(checks, 'Networks', 'matching bridge names for common networks', not mismatched_bridges, '; '.join(mismatched_bridges) if mismatched_bridges else f'{len(common_networks)} common network(s)')

    local_storage = _storage_summary(local)
    remote_storage = _storage_summary(remote)
    common_pools = sorted(local_storage['pool_names'] & remote_storage['pool_names'])
    missing_remote_pools = sorted(local_storage['pool_names'] - remote_storage['pool_names'])
    extra_remote_pools = sorted(remote_storage['pool_names'] - local_storage['pool_names'])

    add_check(checks, 'Storage', 'storage pool names match', not missing_remote_pools, f'missing on remote: {", ".join(missing_remote_pools) or "none"}; extra remote: {", ".join(extra_remote_pools) or "none"}')

    pool_type_mismatches = []
    inactive_pools = []
    for name in common_pools:
        l = local_storage['pool_by_name'].get(name, {})
        r = remote_storage['pool_by_name'].get(name, {})
        if (l.get('type') or '') != (r.get('type') or ''):
            pool_type_mismatches.append(f'{name}: local={l.get("type", "")}, remote={r.get("type", "")}')
        if not l.get('active') or not r.get('active'):
            inactive_pools.append(f'{name}: local_active={l.get("active")}, remote_active={r.get("active")}')
    add_check(checks, 'Storage', 'matching storage pool types', not pool_type_mismatches, '; '.join(pool_type_mismatches) if pool_type_mismatches else f'{len(common_pools)} common pool(s)')
    add_check(checks, 'Storage', 'common storage pools active on both nodes', not inactive_pools, '; '.join(inactive_pools) if inactive_pools else f'{len(common_pools)} common active pool(s)')

    shared_candidates = []
    for name in common_pools:
        l = local_storage['pool_by_name'].get(name, {})
        r = remote_storage['pool_by_name'].get(name, {})
        if (l.get('type') or '') in {'logical', 'iscsi', 'netfs', 'fs', 'dir'}:
            if (l.get('target_path') or '') == (r.get('target_path') or ''):
                shared_candidates.append(name)
    add_check(checks, 'Storage', 'shared-looking storage pools visible', bool(shared_candidates), ', '.join(shared_candidates) or 'no common pools share the same target path', 'ok' if shared_candidates else 'info')

    common_vgs = sorted(local_storage['vg_names'] & remote_storage['vg_names'])
    missing_remote_vgs = sorted(local_storage['vg_names'] - remote_storage['vg_names'])
    add_check(checks, 'LVM', 'LVM volume groups visible on both nodes', not missing_remote_vgs, f'common: {", ".join(common_vgs) or "none"}; missing on remote: {", ".join(missing_remote_vgs) or "none"}', 'ok' if not missing_remote_vgs else 'warning')

    local_iscsi = local_storage['iscsi_sessions']
    remote_iscsi = remote_storage['iscsi_sessions']
    add_check(checks, 'iSCSI', 'iSCSI session state available', bool(local_iscsi) == bool(remote_iscsi), f'local_sessions={len(local_iscsi)}; remote_sessions={len(remote_iscsi)}', 'ok' if bool(local_iscsi) == bool(remote_iscsi) else 'warning')

    local_zfs = local_storage['zfs_pool_names']
    remote_zfs = remote_storage['zfs_pool_names']
    add_check(checks, 'ZFS', 'ZFS pool names match where used', not (local_zfs - remote_zfs), f'local={", ".join(sorted(local_zfs)) or "none"}; remote={", ".join(sorted(remote_zfs)) or "none"}', 'ok' if not (local_zfs - remote_zfs) else 'warning')

    disk_warnings = []
    for vm in (local.get('vm_inventory') or {}).get('vms') or []:
        for disk in vm.get('disks') or []:
            src = str(disk.get('source') or '')
            dtype = str(disk.get('type') or '')
            fmt = str(disk.get('format') or '')
            if '/dev/disk/by-path/' in src and fmt == 'qcow2':
                disk_warnings.append(f"{vm.get('name')}:{src} has qcow2 format under /dev/disk/by-path")
            if dtype == 'file' and src.startswith('/dev/'):
                disk_warnings.append(f"{vm.get('name')}:{src} is type=file but source is /dev")
    add_check(checks, 'VM storage', 'local VM disk sources look sane', not disk_warnings, '; '.join(disk_warnings) if disk_warnings else 'no obvious local VM disk source issues', 'ok' if not disk_warnings else 'warning')

    warnings = sum(1 for c in checks if c.get('severity') == 'warning')
    errors = sum(1 for c in checks if c.get('severity') == 'error')
    ok_count = sum(1 for c in checks if c.get('severity') == 'ok')
    info = sum(1 for c in checks if c.get('severity') == 'info')
    ready = remote_ok and errors == 0 and warnings == 0

    return {
        'ok': True,
        'ready': ready,
        'summary': {'ok': ok_count, 'warning': warnings, 'error': errors, 'info': info, 'total': len(checks)},
        'local': local_self,
        'remote': remote_self,
        'checks': checks,
    }


def compatibility_for_node(node: dict[str, Any]) -> dict[str, Any]:
    local = node_inventory()
    remote = node_inventory_remote(node)
    if not remote.get('ok') and remote.get('error'):
        return {
            'ok': False,
            'ready': False,
            'local': local.get('self') or local_node_self(),
            'remote': {'node_id': node.get('node_id'), 'name': node.get('name'), 'api_url': node.get('api_url')},
            'summary': {'ok': 0, 'warning': 1, 'error': 0, 'info': 0, 'total': 1},
            'checks': [{
                'category': 'Reachability',
                'name': 'remote node inventory reachable',
                'ok': False,
                'severity': 'warning',
                'status': 'warning',
                'detail': remote.get('error', 'remote inventory failed'),
            }],
        }
    return compare_inventories(local, remote)


def compatibility_for_node_id(node_id: str) -> dict[str, Any]:
    node = get_node(node_id)
    if not node:
        return {'ok': False, 'error': 'Node not found.', 'checks': [], 'summary': {'ok': 0, 'warning': 0, 'error': 1, 'info': 0, 'total': 1}}
    return compatibility_for_node(node)


def all_node_compatibility() -> dict[str, Any]:
    results = []
    for node in list_nodes():
        if not node.get('enabled', True):
            continue
        item = dict(node)
        item['compatibility'] = compatibility_for_node(node)
        results.append(item)
    return {'ok': True, 'nodes': results, 'generated_at': int(time.time())}
