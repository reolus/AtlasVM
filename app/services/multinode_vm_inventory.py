from __future__ import annotations

import time
from typing import Any

from app.services.node_client import node_inventory_remote
from app.services.node_registry import ensure_local_node_registered, list_nodes, local_node_self
from app.services.vm_inventory import list_vm_inventory


def _lower(value: Any) -> str:
    return str(value or '').strip().lower()


def _state_text(vm: dict[str, Any]) -> str:
    state = vm.get('state')
    if isinstance(state, int):
        # Fallback for older Phase 11.1 remote inventory that returned libvirt numeric state.
        return 'running' if vm.get('active') else 'offline'
    return str(state or ('running' if vm.get('active') or vm.get('running') else 'offline'))


def _is_running(vm: dict[str, Any]) -> bool:
    if isinstance(vm.get('running'), bool):
        return bool(vm.get('running'))
    if isinstance(vm.get('active'), bool):
        return bool(vm.get('active'))
    return 'running' in _lower(vm.get('state'))


def _normalize_vm(vm: dict[str, Any], node: dict[str, Any], local: bool, source: str = '') -> dict[str, Any]:
    state = _state_text(vm)
    running = _is_running(vm)

    normalized = dict(vm)
    normalized.update({
        'name': vm.get('name') or vm.get('domain') or 'unknown',
        'uuid': vm.get('uuid') or '',
        'state': state,
        'running': running,
        'node_id': node.get('node_id'),
        'node_name': node.get('name') or node.get('hostname') or node.get('api_url'),
        'node_api_url': node.get('api_url') or '',
        'node_local': bool(local),
        'node_enabled': bool(node.get('enabled', True)),
        'node_source': source,
    })

    normalized.setdefault('vcpu', vm.get('vcpu') or vm.get('vcpus') or '')
    normalized.setdefault('memory', vm.get('memory') or vm.get('current_memory') or '')
    normalized.setdefault('autostart', vm.get('autostart', False))
    normalized.setdefault('ips', vm.get('ips') or [])
    normalized.setdefault('disks', vm.get('disks') or [])
    normalized.setdefault('interfaces', vm.get('interfaces') or [])

    return normalized


def local_node_for_inventory() -> dict[str, Any]:
    node = ensure_local_node_registered()
    self_info = local_node_self()
    node.setdefault('node_id', self_info.get('node_id'))
    node.setdefault('name', self_info.get('name'))
    node.setdefault('api_url', self_info.get('api_url'))
    node['local'] = True
    return node


def local_multinode_inventory() -> dict[str, Any]:
    node = local_node_for_inventory()
    inv = list_vm_inventory()
    vms = [_normalize_vm(vm, node, local=True, source='local') for vm in inv.get('vms', [])]
    return {
        'node': node,
        'ok': True,
        'error': '',
        'vms': vms,
        'total': len(vms),
        'running': sum(1 for vm in vms if vm.get('running')),
        'offline': sum(1 for vm in vms if not vm.get('running')),
    }


def remote_vms_from_inventory_payload(payload: dict[str, Any], node: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if not payload.get('ok', False):
        return [], str(payload.get('error') or 'remote inventory failed')

    # Phase 11.2 nodes return rich VM inventory here.
    vm_inventory = payload.get('vm_inventory') or payload.get('vms')
    if isinstance(vm_inventory, dict) and isinstance(vm_inventory.get('vms'), list):
        return [_normalize_vm(vm, node, local=False, source='remote-rich') for vm in vm_inventory.get('vms', [])], ''

    # Phase 11.1 nodes return a simpler libvirt inventory.
    libvirt_info = payload.get('libvirt') or {}
    if isinstance(libvirt_info, dict) and isinstance(libvirt_info.get('vms'), list):
        return [_normalize_vm(vm, node, local=False, source='remote-basic') for vm in libvirt_info.get('vms', [])], ''

    return [], 'remote inventory did not include VM data'


def multinode_vm_inventory(selected_node_id: str = 'all', include_disabled: bool = False) -> dict[str, Any]:
    selected_node_id = str(selected_node_id or 'all')
    local_self = local_node_self()
    local_id = local_self.get('node_id')
    local_node = local_node_for_inventory()

    registry_nodes = list_nodes()
    node_map: dict[str, dict[str, Any]] = {}

    node_map[str(local_node.get('node_id'))] = local_node
    for node in registry_nodes:
        node_map[str(node.get('node_id'))] = dict(node)

    all_nodes = sorted(node_map.values(), key=lambda item: str(item.get('name') or item.get('api_url') or '').lower())

    records: list[dict[str, Any]] = []
    node_statuses: list[dict[str, Any]] = []

    for node in all_nodes:
        node_id = str(node.get('node_id') or '')
        is_local = node_id == local_id or bool(node.get('local'))
        enabled = bool(node.get('enabled', True))

        if selected_node_id not in {'all', node_id}:
            continue
        if not enabled and not include_disabled:
            node_statuses.append({**node, 'ok': False, 'error': 'disabled', 'vm_count': 0})
            continue

        if is_local:
            local_inv = local_multinode_inventory()
            records.extend(local_inv['vms'])
            node_statuses.append({**node, 'ok': True, 'error': '', 'vm_count': len(local_inv['vms']), 'local': True})
            continue

        payload = node_inventory_remote(node)
        remote_vms, error = remote_vms_from_inventory_payload(payload, node)
        records.extend(remote_vms)
        node_statuses.append({
            **node,
            'ok': bool(payload.get('ok')) and not error,
            'error': error or str(payload.get('error') or ''),
            'vm_count': len(remote_vms),
            'latency_ms': payload.get('_latency_ms'),
            'local': False,
        })

    records = sorted(records, key=lambda vm: (str(vm.get('node_name') or '').lower(), str(vm.get('name') or '').lower()))

    return {
        'total': len(records),
        'running': sum(1 for vm in records if vm.get('running')),
        'offline': sum(1 for vm in records if not vm.get('running')),
        'vms': records,
        'nodes': all_nodes,
        'node_statuses': node_statuses,
        'selected_node_id': selected_node_id,
        'generated_at': int(time.time()),
    }
