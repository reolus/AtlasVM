# AtlasVM Community Edition Removal Audit

Branch target: `standalone-free`

Purpose: remove active multi-node functionality from the current AtlasVM product and leave a clean single-node free edition. This audit was created before patching and then updated with the actual patch results.

## Multi-node code discovered

### Active routes discovered in `app/main.py`

The following routes were identified as multi-node or remote-control-plane features and were removed from the standalone branch:

- `GET /api/node/self`
- `GET /api/node/health`
- `GET /api/node/inventory`
- `GET /api/node/vms`
- `GET /api/node/vms/{vm_name}`
- `POST /api/node/vms/{vm_name}/{action}`
- `GET /api/node/doctor`
- `GET /api/node/compatibility`
- `GET /nodes`
- `GET /nodes/new`
- `POST /nodes`
- `POST /nodes/register-local`
- `GET /nodes/compatibility`
- `GET /nodes/{node_id}/compatibility`
- `GET /nodes/{node_id}/vms/{vm_name}`
- `POST /nodes/{node_id}/vms/{vm_name}/{action}`
- `GET /nodes/{node_id}`
- `POST /nodes/{node_id}/delete`

### Templates discovered

The following templates were multi-node-specific and were moved to `docs/enterprise-preview/templates/` instead of being destroyed:

- `app/templates/nodes.html`
- `app/templates/node_detail.html`
- `app/templates/node_compatibility.html`
- `app/templates/node_form.html`
- `app/templates/remote_vm_detail.html`

### Services discovered

The following service files are multi-node/remote-node related. They were left dormant in this patch because they are not imported by `app/main.py` anymore and may be useful when the Enterprise branch is split out. Yes, boring restraint won over the primal urge to delete things. Civilization survives another day.

- `app/services/node_registry.py`
- `app/services/node_inventory.py`
- `app/services/node_client.py`
- `app/services/node_compatibility.py`
- `app/services/multinode_vm_inventory.py`
- `app/services/remote_vm_actions.py`

Recommended future Enterprise extraction: move these files into an enterprise module/package or feature branch instead of keeping them in the standalone tree forever.

### Navigation links discovered

The sidebar contained a direct `Nodes` link in `app/templates/base.html`. It was removed.

The `/vms` page contained:

- `Manage Nodes` button
- node selector
- node/remote search placeholder
- local/remote location filter
- Node table column
- remote VM detail links
- remote console links
- remote power actions
- node status card grid

These were removed. The `/vms` page now lists local VMs only using `list_vm_inventory()`.

### Doctor checks discovered

`app/services/doctor_service.py` included Cluster/node registry checks. These were removed from the standalone branch so the Doctor page remains local-host focused.

### Documentation discovered

Phase 11/12 multi-node documents and UI route audit outputs were moved under `docs/enterprise-preview/` so the standalone docs do not present removed features as active product behavior.

Moved examples include:

- Phase 11 node registration, multi-node inventory, compatibility, and remote VM control docs
- Phase 12 operator/test/preflight/rollback docs
- UI overhaul docs for remote VM detail, node readiness, and compatibility readiness
- historical UI route audit files containing `/nodes` links

### Database and schema usage

No database schema migrations were removed in this pass. Existing node registry flat files such as `atlasvm_nodes.json`, `atlasvm_node_id`, and `atlasvm_node_token` were left untouched to avoid destructive cleanup on a live install. They are no longer used by active standalone routes.

Standalone cleanup can remove or ignore these files later after confirming they are not needed for rollback or Enterprise branch comparison.

## Local features to preserve

The following local features were intentionally preserved:

- Dashboard
- Local VM list
- Local VM detail
- Local VM power actions
- Local console
- Local snapshots
- Local backup
- Task list and task kill
- ISO attach/eject
- Disk management
- Network management
- Storage pools
- ZFS pages
- Host network page
- Doctor page
- Audit page
- Settings
- Local users
- Favicon/logo/UI shell

## Validation checklist

Run on the target host from `/opt/atlasvm`:

```bash
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl status atlasvm --no-pager
journalctl -u atlasvm -n 120 --no-pager
```

Manual validation:

- Dashboard loads.
- `/vms` loads local VMs only.
- `/vms` has no node selector, remote filter, remote badges, or remote action links.
- VM detail loads.
- Console opens for running local VM.
- Storage page loads.
- Networks page loads.
- ISO page loads.
- Backups page loads.
- Snapshots page loads.
- ISO attach/eject works.
- Disk page loads.
- Network page loads.
- Tasks page loads.
- Audit page loads.
- Sidebar has no Nodes link.
- `/nodes` should return 404.
- `/api/node/*` should return 404.

## Patch result

After patching, static route inspection found no active `/nodes` or `/api/node` route decorators in `app/main.py`.
