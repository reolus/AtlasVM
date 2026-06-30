# AtlasVM Phase 11.4 - Safe Remote Node Controls

Phase 11.4 adds conservative remote VM controls on top of the Phase 11 node registry, remote inventory, and compatibility work.

## Included

- Token-protected node VM detail endpoint
- Token-protected remote-safe VM action endpoints
- Manager-side remote VM detail page
- Manager-side remote start, shutdown, reboot, and force power off actions
- Remote console link to the owning node
- `/vms` remote action buttons
- Node detail VM action links

## New local node API endpoints

These endpoints are available on every AtlasVM node and require `X-AtlasVM-Node-Token`.

```text
GET  /api/node/vms/{vm_name}
POST /api/node/vms/{vm_name}/start
POST /api/node/vms/{vm_name}/shutdown
POST /api/node/vms/{vm_name}/reboot
POST /api/node/vms/{vm_name}/poweroff
```

## New manager routes

```text
GET  /nodes/{node_id}/vms/{vm_name}
POST /nodes/{node_id}/vms/{vm_name}/start
POST /nodes/{node_id}/vms/{vm_name}/shutdown
POST /nodes/{node_id}/vms/{vm_name}/reboot
POST /nodes/{node_id}/vms/{vm_name}/poweroff
```

## What this intentionally does not include

- Remote delete
- Remote disk delete
- Remote network changes
- Migration
- HA failover
- Quorum or fencing

Those are intentionally held for later phases because they are sharper than start/stop operations and need stronger safety checks.

## Test commands

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm

TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)
curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/vms
curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/vms/Test6
```

To test a safe action against a stopped test VM:

```bash
curl -k -X POST \
  -H "X-AtlasVM-Node-Token: $TOKEN" \
  https://127.0.0.1/api/node/vms/Test6/start
```

Use the web UI for normal use:

```text
/vms
/nodes/{node_id}/vms/{vm_name}
```
