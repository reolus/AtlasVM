# Phase 11.3 Upgrade Notes

Phase 11.3 is a staged upgrade over Phase 11.2.

## Before upgrade

Confirm Phase 11.2 works:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl status atlasvm --no-pager
```

## After upgrade

Run:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm
```

Then verify:

```bash
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)
curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/inventory
curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/compatibility
```

## Expected UI changes

- Nodes page has a Compatibility button.
- Node detail page has a compatibility summary.
- Compatibility report page groups checks by category.
- Doctor includes cluster registry and reachability checks.

## Known behavior

Two nodes are not considered ready just because they are online. Matching network names, bridge names, storage pool names/types, LVM visibility, and shared storage visibility matter. Annoying, yes. Also the difference between a cluster and a complaint generator.
