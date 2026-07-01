# AtlasVM UI Route Contract

This contract is the cleanup target for the Standalone Free branch after removing active multi-node UI and route behavior.

## Rules

- Prefer canonical `/vms/...` routes for VM feature forms.
- Keep legacy `/ui/vms/...` aliases temporarily only for compatibility.
- Keep `/ui/vms/{name}/{action}` below every specific VM route.
- Do not use libvirt undefine/redefine for routine VM edits on snapshot-capable VMs.
- VM-side VLAN tags are not written by the UI. VLAN placement belongs on host bridge/libvirt network configuration.

## Canonical VM routes

| Method | Path | Purpose | Service behavior |
|---|---|---|---|
| GET | `/vms` | Local VM inventory | `list_vm_inventory()` |
| GET | `/vms/new` | New VM form | UI form |
| POST | `/vms/new` | Create VM | `LibvirtService.create_vm()` |
| GET | `/vms/{name}` | VM overview | `LibvirtService.get_vm()` |
| POST | `/vms/{name}/edit` | VM memory/vCPU/description | `LibvirtService.update_vm_basic()` |
| POST | `/vms/{name}/template` | Convert template state | `LibvirtService.set_template()` |
| GET | `/vms/{name}/disks` | Disk management | `get_vm_disks()` |
| POST | `/vms/{name}/disks/add` | Add disk | `LibvirtService.add_disk()` with rollback on attach failure |
| POST | `/vms/{name}/disks/{target_dev}/remove` | Remove disk | `remove_disk_from_vm()` |
| GET | `/vms/{name}/network` | NIC management | libvirt interface listing |
| POST | `/vms/{name}/network` | Replace NIC network | `detachDeviceFlags()` / `attachDeviceFlags()` |
| POST | `/vms/{name}/network/add` | Add NIC | `attachDeviceFlags()` |
| POST | `/vms/{name}/network/remove` | Remove NIC | `detachDeviceFlags()` |
| POST | `/vms/{name}/iso/attach` | Attach ISO | `updateDeviceFlags()` / `attachDeviceFlags()` |
| POST | `/vms/{name}/iso/eject` | Eject ISO | `updateDeviceFlags()` |
| POST | `/vms/{name}/snapshots` | Create snapshot | `LibvirtService.create_snapshot()` |
| POST | `/vms/{name}/snapshots/{snapshot}/revert` | Revert snapshot | `LibvirtService.revert_snapshot()` |
| POST | `/vms/{name}/snapshots/{snapshot}/delete` | Delete snapshot | `LibvirtService.delete_snapshot()` |
| GET | `/vms/{name}/clone` | Clone page | UI form |
| POST | `/vms/{name}/clone` | Clone VM | `LibvirtService.clone_vm()` |
| GET | `/vms/{name}/delete-confirm` | Delete confirmation | UI form |
| POST | `/vms/{name}/delete` | Delete VM | `LibvirtService.delete_vm()` |

## Known legacy aliases retained

- `/ui/vms/{name}/edit`
- `/ui/vms/{name}/iso/attach`
- `/ui/vms/{name}/iso/eject`
- `/ui/vms/{name}/disks/add`
- `/ui/vms/{name}/snapshots`
- `/ui/vms/{name}/snapshots/{snapshot}/{action}`
- `/ui/vms/{name}/template`
- `/ui/vms/{name}/{action}` for power/autostart compatibility

## Service safety changes in this overhaul

- `LibvirtService.add_disk()` now returns structured disk metadata and rolls back created storage if attach fails.
- `_redefine_domain()` is disabled and raises loudly if legacy code tries to use it.
- `mark_vm_as_template()` now delegates to `set_template()` instead of redefining XML.
- VM network update/add/remove routes use libvirt device APIs instead of `defineXML()` for routine NIC changes.
- Old `.before-*` service backups were moved under `docs/patch-history/services/` so production service scans are clean.

Run `scripts/audit_ui_routes.py` after UI route/template changes.
