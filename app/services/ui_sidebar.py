from __future__ import annotations

import socket
from typing import Any

from app.core.config import get_settings


def _safe_call(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _host_name() -> str:
    try:
        return socket.gethostname() or 'atlas-node-01'
    except Exception:
        return 'atlas-node-01'


def _vm_inventory() -> dict[str, Any]:
    from app.services.vm_inventory import list_vm_inventory
    return _safe_call(list_vm_inventory, {'total': 0, 'running': 0, 'offline': 0, 'vms': []})


def _networks() -> list[dict[str, Any]]:
    from app.services.network_phase8 import NetworkPhase8Service
    settings = get_settings()
    def load():
        return NetworkPhase8Service(settings.libvirt_uri).list_networks()
    return _safe_call(load, [])


def _storage() -> dict[str, Any]:
    from app.services.storage_phase9 import storage_overview
    return _safe_call(storage_overview, {})


def build_sidebar_context(current_path: str = '') -> dict[str, Any]:
    host = _host_name()
    vm_inventory = _vm_inventory()
    networks = _networks()
    storage = _storage()

    active = 'dashboard'
    if current_path.startswith('/vms') or current_path.startswith('/templates') or current_path.startswith('/isos') or current_path.startswith('/ui/vms'):
        active = 'vm'
    elif current_path.startswith('/networks'):
        active = 'network'
    elif current_path.startswith('/storage'):
        active = 'storage'
    elif current_path.startswith('/admin') or current_path.startswith('/backups') or current_path.startswith('/doctor') or current_path.startswith('/tasks') or current_path.startswith('/audit') or current_path.startswith('/events') or current_path.startswith('/users') or current_path.startswith('/settings') or current_path.startswith('/zfs') or current_path.startswith('/host/network'):
        active = 'admin'

    current_vm = ''
    parts = [p for p in current_path.split('/') if p]
    if len(parts) >= 2 and parts[0] == 'vms' and parts[1] not in {'new'}:
        current_vm = parts[1]

    nfs_targets = storage.get('nfs_targets') or {}
    smb_targets = storage.get('smb_targets') or {}
    iscsi_targets = storage.get('iscsi_targets') or {}
    lvm_summary = storage.get('lvm_summary') or []
    libvirt_pools = storage.get('libvirt_pools') or []
    zfs_pools = storage.get('zfs_pools') or []

    iscsi_children: dict[str, list[dict[str, Any]]] = {name: [] for name in iscsi_targets.keys()}
    for item in lvm_summary if isinstance(lvm_summary, list) else []:
        parent = str(item.get('iscsi_target') or item.get('target') or item.get('source_target') or '').strip()
        name = str(item.get('vg_name') or item.get('name') or item.get('pool') or '').strip()
        if parent and name:
            iscsi_children.setdefault(parent, []).append({'name': name, 'type': item.get('type') or 'LVM'})

    return {
        'active': active,
        'host_name': host,
        'host_online': True,
        'current_vm': current_vm,
        'vm_inventory': vm_inventory,
        'networks': networks,
        'storage': {
            'libvirt_pools': libvirt_pools,
            'zfs_pools': zfs_pools,
            'nfs_targets': nfs_targets,
            'smb_targets': smb_targets,
            'iscsi_targets': iscsi_targets,
            'iscsi_children': iscsi_children,
        },
    }
