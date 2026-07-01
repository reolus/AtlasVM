# AtlasVM Community Edition Branch

Branch name: `standalone-free`

## Intent

This branch turns the current AtlasVM codebase into AtlasVM Community Edition. It manages one local host, exposes single-node virtualization management, and removes active multi-node UI and route behavior.

## Removed from active application

- `/nodes` UI route family
- `/api/node` token-protected node API route family
- Remote VM detail page route
- Remote VM action route
- Node registration UI
- Node compatibility UI
- Node readiness dashboard UI
- Node selector from VM list
- Local/remote VM location filter from VM list
- Remote VM links and actions from VM list
- Node status cards from VM list
- Cluster/node registry checks from Doctor
- Sidebar `Nodes` link

## Archived for Enterprise preview

The removed multi-node templates were moved to:

- `docs/enterprise-preview/templates/`

Historical multi-node documents and route audits were moved to:

- `docs/enterprise-preview/docs/`
- `docs/enterprise-preview/audits/`

## Preserved

- Dashboard
- Local VM inventory
- VM creation
- VM detail
- VM edit
- VM power controls
- Console
- Clone
- Delete confirmation
- Local disk management
- Local VM network management
- ISO attach/eject
- Snapshots
- Backups
- Storage
- Networks
- Templates
- ISOs
- Tasks and task kill
- Audit
- Settings
- Users
- Doctor
- ZFS
- Host network management
- Favicon/logo/UI shell

## Validation commands

Run on the AtlasVM host:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl status atlasvm --no-pager
journalctl -u atlasvm -n 120 --no-pager
```

Route sanity check:

```bash
python - <<'PY'
from app.main import app
for route in app.routes:
    path = getattr(route, 'path', '')
    methods = getattr(route, 'methods', None)
    endpoint = getattr(getattr(route, 'endpoint', None), '__name__', '')
    if methods:
        print(f"{','.join(sorted(methods)):18} {path:60} {endpoint}")
    else:
        print(f"{type(route).__name__:18} {path:60} {endpoint}")
PY
```

Expected route behavior:

- `/vms` exists and shows local VMs only.
- `/nodes` is not registered.
- `/api/node/self` is not registered.
- `/api/node/vms` is not registered.

## Known future Enterprise hooks

The multi-node service files remain dormant for now:

- `app/services/node_registry.py`
- `app/services/node_inventory.py`
- `app/services/node_client.py`
- `app/services/node_compatibility.py`
- `app/services/multinode_vm_inventory.py`
- `app/services/remote_vm_actions.py`

These should be moved into an Enterprise branch/module during the premium product split.
