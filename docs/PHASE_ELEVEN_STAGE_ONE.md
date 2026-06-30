# AtlasVM Phase 11.1 - Node Registry and Local Node API

This stage starts multi-node support without migration or HA.

## Adds

- `/opt/atlasvm/atlasvm_nodes.json` node registry
- `/opt/atlasvm/atlasvm_node_id` local node identity
- `/opt/atlasvm/atlasvm_node_token` local node API token
- Token-protected node API endpoints:
  - `GET /api/node/self`
  - `GET /api/node/health`
  - `GET /api/node/inventory`
  - `GET /api/node/doctor`
- Node UI:
  - `GET /nodes`
  - `GET /nodes/new`
  - `GET /nodes/{node_id}`

## Not included yet

- VM migration
- HA failover
- quorum/witness
- remote destructive VM actions

## Test

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/node_registry.py app/services/node_inventory.py app/services/node_client.py
systemctl restart atlasvm
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)
curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/health
```

Then open `/nodes` and register the local node.
