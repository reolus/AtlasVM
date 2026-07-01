# AtlasVM Phase 11.5.4 - Virtual Machines List Polish

## Scope

This phase refreshes the `/vms` page as the primary VM operating console before Phase 12 multi-node testing.

## Changes

- Rebuilt `app/templates/vms.html` around a dashboard-style VM inventory console.
- Added top hero actions for Create VM and Manage Nodes.
- Added KPI cards for total, running, offline, and nodes in scope.
- Added client-side VM search across name, node, UUID, IPs, disks, and network fields.
- Added client-side state and local/remote filters.
- Reworked the VM table into a compact operational layout:
  - VM identity and quick links
  - state pill and autostart marker
  - node/local/remote badges
  - compact resource summary
  - contained network and disk foldouts
  - compact local/remote action buttons
- Rebuilt node status cards below the VM table.
- Added CSS rules to `app/static/style.css` for the VM list console.

## Backend impact

No backend service methods, libvirt mutation logic, database schema, task logic, audit logic, or node APIs were changed.

## Validation

Run after deployment:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
```

Then verify:

- `/vms` loads.
- Search filters rows without reloading.
- State filter works.
- Local/remote filter works.
- Local VM Open/Console/Disks/Network links work.
- Remote VM Open/Console links work when a remote node is present.
- Start/Shutdown buttons still route to the existing handlers.
