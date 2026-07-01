# AtlasVM Phase 12 Rollback Plan

Use this plan if Phase 12 multi-node testing causes unstable UI behavior, unsafe remote actions, or service failures.

## Immediate stop conditions

Stop testing if any of the following occur:

- Remote action targets the wrong VM.
- Remote action targets the wrong node.
- A local VM is modified while testing a remote VM.
- `atlasvm.service` repeatedly fails to start.
- Compatibility or node pages trigger server tracebacks.
- A remote action exposes unsupported disk/NIC/ISO/snapshot/backup/delete behavior.

## Stop remote testing

Disable further remote testing operationally:

1. Stop using remote power buttons.
2. Record the node, VM, action, and time.
3. Save the relevant journal output.
4. Keep the remote VM in its current state until reviewed.

Useful command:

```bash
journalctl -u atlasvm -n 200 --no-pager
```

## Revert code to prior git state

If the issue appears related to UI/deployment changes:

```bash
cd /opt/atlasvm
git status
git log --oneline -5
# choose known-good commit
git checkout <known-good-commit>
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
systemctl restart atlasvm
systemctl restart nginx
```

## Restore database backup

Only restore the database if node registration or state data is corrupted and you intentionally want to return to the pre-test state.

```bash
cd /opt/atlasvm
systemctl stop atlasvm
cp atlasvm.db atlasvm.db.failed_phase12.$(date +%Y%m%d_%H%M%S).bak
cp atlasvm.db.pre_phase12.YYYYMMDD_HHMMSS.bak atlasvm.db
systemctl start atlasvm
```

## Remove or disable test node

If a node registration itself is the problem, disable or remove the test node using the existing AtlasVM node management process. Record the node ID before removal.

## Post-rollback validation

After rollback:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl status atlasvm --no-pager
journalctl -u atlasvm -n 120 --no-pager
```

Then verify:

- `/` loads.
- `/vms` loads.
- `/nodes` loads.
- `/nodes/compatibility` loads.
- Local VM detail page loads.

