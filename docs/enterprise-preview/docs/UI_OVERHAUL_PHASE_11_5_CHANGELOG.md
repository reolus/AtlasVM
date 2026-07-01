# AtlasVM Phase 11.5 UI Cleanup and Overhaul Changelog

## Cleanup step 1: route consolidation

Fixed:
- Removed the duplicate snapshot create route.
- Added canonical `/vms/...` POST routes for edit, disk add, ISO attach/eject, snapshots, and template conversion.
- Left legacy `/ui/vms/...` aliases in place for compatibility.
- Kept generic `/ui/vms/{name}/{action}` below specific VM routes.

Next phase:
- Eventually replace generic VM action dispatch with explicit `/vms/{name}/power/{action}` routes.

## Cleanup step 2: form alignment

Fixed:
- VM detail page now posts core forms to canonical `/vms/...` routes.
- Add-disk was moved to the dedicated disk management page.
- Delete was moved out of the VM detail page into the existing confirmation page.
- Disk management form now matches the unified `LibvirtService.add_disk()` backend.

Next phase:
- Add CSRF protection or signed intent tokens for destructive POST forms if AtlasVM is exposed beyond trusted admin networks.

## Cleanup step 3: service safety

Fixed:
- `LibvirtService.add_disk()` now returns structured metadata and rolls back newly-created storage if `attachDeviceFlags()` fails.
- `_redefine_domain()` now raises a clear runtime error instead of undefining/redefining domains.
- `mark_vm_as_template()` now delegates to `set_template()` instead of redefining XML.
- VM network update/add/remove routes now use `detachDeviceFlags()` and `attachDeviceFlags()` for config changes instead of `defineXML()`.
- VM-side VLAN tags are no longer written by the UI helper; VLAN responsibility stays on host/libvirt networks.

Next phase:
- Move all VM network mutation logic out of `main.py` into a single service class with tests.

## Cleanup step 4: production tree cleanup

Fixed:
- Moved old `.before-*` service backup files to `docs/patch-history/services/`.
- Added `scripts/audit_ui_routes.py`.
- Added `docs/UI_ROUTE_CONTRACT.md`.
- Added `docs/audits/ui_route_form_audit.txt`.

Next phase:
- Add CI checks that run py_compile and `scripts/audit_ui_routes.py` before merge.

## UI overhaul step 1: shell and navigation

Fixed:
- Replaced the top-only navigation with a persistent sidebar and top status bar.
- Added shared cards, panels, stat grids, state pills, action rows, alerts, and responsive layout styles.

Next phase:
- Add active nav highlighting and role-aware hiding/disabling of actions.

## UI overhaul step 2: VM detail page

Fixed:
- Rebuilt VM detail as an overview dashboard.
- Split quick actions, settings, template status, storage, network, ISO, backup, snapshots, danger zone, and XML into clearer panels.
- Reduced inline destructive actions.

Next phase:
- Split VM detail into tabs or separate subpages once multi-node action permissions are finalized.

## UI overhaul step 3: disk page

Fixed:
- Rebuilt disk management around the unified add-disk route.
- Removed manual disk naming from the UI to avoid repeated orphan/collision problems.

Next phase:
- Add orphan volume detection and cleanup workflow directly in the storage UI.
