# AtlasVM Phase 11.2 - Multi-node VM Inventory

Phase 11.2 extends the Phase 11 node foundation by making the VM inventory node-aware.

## What this stage adds

- Multi-node VM inventory service.
- Rich local VM inventory exposed through `/api/node/inventory`.
- New token-protected `/api/node/vms` endpoint.
- `/vms` page can show VMs from all registered nodes.
- `/vms?node=<node_id>` filters inventory to a single node.
- Node status cards on the VM inventory page.
- Node-aware VM table with a node column.
- Local VM actions remain available.
- Remote VM rows are view-only and link to node detail.
- Node detail page shows richer VM inventory when the remote node supports Phase 11.2.

## What this stage does not add

- Remote start/stop/reboot.
- Remote VM delete.
- Live migration.
- HA restart policy.
- Quorum or fencing.

Those are later phases. This stage is visibility and validation, not distributed chaos with buttons.

## Install

```bash
cd /opt
cp -a atlasvm atlasvm.before-phase11-stage2.$(date +%Y%m%d-%H%M%S)

unzip /path/to/AtlasVM_phase11_stage2_multinode_vms.zip -d /tmp/atlasvm-phase11-stage2
rsync -a /tmp/atlasvm-phase11-stage2/phase11_stage2/ /opt/atlasvm/

cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm
systemctl status atlasvm --no-pager
```

## Test local node VM endpoint

```bash
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)

curl -k \
  -H "X-AtlasVM-Node-Token: $TOKEN" \
  https://127.0.0.1/api/node/vms
```

## Test node inventory endpoint

```bash
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)

curl -k \
  -H "X-AtlasVM-Node-Token: $TOKEN" \
  https://127.0.0.1/api/node/inventory
```

The response should include `vm_inventory`.

## UI checks

Open:

```text
https://<atlasvm-host>/vms
```

Expected:

- Node filter appears.
- Node status cards appear.
- VM table includes a Node column.
- Local VMs have Details, Console, and Disks actions.
- Remote VMs link to Node Detail and do not show destructive VM controls.

## Notes

A Phase 11.1 remote node still works. AtlasVM falls back to the older `libvirt.vms` payload when rich `vm_inventory` is not available.

For best results, install Phase 11.2 on every AtlasVM node.
