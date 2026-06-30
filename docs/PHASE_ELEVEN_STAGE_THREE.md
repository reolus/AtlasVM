# AtlasVM Phase 11.3 - Node Compatibility and Cluster Readiness

Phase 11.3 adds compatibility checks between registered AtlasVM nodes. It does not perform migration, failover, fencing, or HA. It answers a simpler and more important question first: are these hosts similar enough to safely plan shared operations?

## Added

- `app/core/version.py`
- `app/services/node_compatibility.py`
- `app/templates/node_compatibility.html`
- `/nodes/compatibility`
- `/nodes/{node_id}/compatibility`
- `/api/node/compatibility`
- structured node inventory fields for networks, storage pools, LVM, iSCSI, and ZFS
- Doctor cluster registry/reachability checks
- `VERSION` file for stable node version reporting

## Checks

Compatibility compares:

- AtlasVM version
- remote node reachability
- node clock drift
- core services
- libvirt inventory health
- VM network names
- bridge names for common networks
- storage pool names
- storage pool types
- active state of common pools
- shared-looking storage paths
- LVM volume group visibility
- iSCSI session state
- ZFS pool names where used
- obvious local VM disk source mistakes

## Install

```bash
cd /opt
cp -a atlasvm atlasvm.before-phase11-stage3.$(date +%Y%m%d-%H%M%S)

unzip /path/to/AtlasVM_phase11_stage3_compatibility.zip -d /tmp/atlasvm-phase11-stage3
rsync -a /tmp/atlasvm-phase11-stage3/phase11_stage3/ /opt/atlasvm/

cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm
systemctl status atlasvm --no-pager
```

## Test

```bash
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)

curl -k -H "X-AtlasVM-Node-Token: $TOKEN" https://127.0.0.1/api/node/compatibility
```

Open:

- `/nodes`
- `/nodes/compatibility`
- `/nodes/<node_id>/compatibility`
- `/doctor`

## Notes

A node can be reachable and still not be cluster-ready. Warnings are expected until networks, storage pool names, bridge names, LVM volume groups, and shared storage are aligned across hosts.
