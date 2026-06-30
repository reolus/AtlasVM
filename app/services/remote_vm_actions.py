from __future__ import annotations

import time
from typing import Any

from app.services.libvirt_service import LibvirtService
from app.services.node_registry import local_node_self
from app.services.vm_inventory import list_vm_inventory

_ALLOWED_ACTIONS = {'start', 'shutdown', 'reboot', 'poweroff'}


def get_local_vm_record(vm_name: str) -> dict[str, Any]:
    vm_name = (vm_name or '').strip()
    if not vm_name:
        raise RuntimeError('VM name is required.')

    inventory = list_vm_inventory()
    for vm in inventory.get('vms', []):
        if vm.get('name') == vm_name:
            return vm

    raise RuntimeError(f'VM not found: {vm_name}')


def local_vm_detail_payload(vm_name: str) -> dict[str, Any]:
    return {
        'ok': True,
        'self': local_node_self(),
        'vm': get_local_vm_record(vm_name),
        'time': int(time.time()),
    }


def perform_local_vm_action(vm_name: str, action: str) -> dict[str, Any]:
    vm_name = (vm_name or '').strip()
    action = (action or '').strip().lower()

    if not vm_name:
        raise RuntimeError('VM name is required.')

    if action not in _ALLOWED_ACTIONS:
        raise RuntimeError(f'Unsupported remote-safe VM action: {action}')

    lv = LibvirtService()
    try:
        if action == 'start':
            lv.start_vm(vm_name)
            message = f'Started VM {vm_name}.'
        elif action == 'shutdown':
            lv.shutdown_vm(vm_name)
            message = f'Sent graceful shutdown to VM {vm_name}.'
        elif action == 'reboot':
            lv.reboot_vm(vm_name)
            message = f'Sent reboot to VM {vm_name}.'
        elif action == 'poweroff':
            lv.force_stop_vm(vm_name)
            message = f'Forced power off for VM {vm_name}.'
        else:
            raise RuntimeError(f'Unsupported action: {action}')
    finally:
        lv.close()

    # Refresh from the inventory parser after action so caller gets a useful state.
    try:
        vm = get_local_vm_record(vm_name)
    except Exception:
        vm = {'name': vm_name, 'state': 'unknown', 'running': False}

    return {
        'ok': True,
        'self': local_node_self(),
        'action': action,
        'message': message,
        'vm': vm,
        'time': int(time.time()),
    }
