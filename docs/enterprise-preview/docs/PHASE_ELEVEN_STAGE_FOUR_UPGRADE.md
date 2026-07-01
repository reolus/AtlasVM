# Phase 11.4 Upgrade Notes

Install from the Phase 11.4 package:

```bash
cd /opt
cp -a atlasvm atlasvm.before-phase11-stage4.$(date +%Y%m%d-%H%M%S)

unzip /path/to/AtlasVM_phase11_stage4_remote_controls.zip -d /tmp/atlasvm-phase11-stage4
rsync -a /tmp/atlasvm-phase11-stage4/phase11_stage4/ /opt/atlasvm/

cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm
systemctl status atlasvm --no-pager
```

Validate the local node API:

```bash
TOKEN=$(cat /opt/atlasvm/atlasvm_node_token)

curl -k -H "X-AtlasVM-Node-Token: $TOKEN" \
  https://127.0.0.1/api/node/vms

curl -k -H "X-AtlasVM-Node-Token: $TOKEN" \
  https://127.0.0.1/api/node/vms/<VMNAME>
```

Validate the UI:

```text
/vms
/nodes
/nodes/<node_id>/vms/<vm_name>
```

Only safe remote controls are included: start, graceful shutdown, reboot, and force power off. Remote delete, migration, and HA remain later phases.
