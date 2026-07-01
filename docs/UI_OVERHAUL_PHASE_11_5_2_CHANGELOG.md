# AtlasVM Phase 11.5.2 UI Overflow Fix

## Scope

This is a UI-only patch. It does not change VM, storage, libvirt, task, or audit backend behavior.

## Fixed

- Added overflow containment for wide operational tables.
- Reworked the Tasks template so long background-task messages wrap inside the message cell instead of widening the whole page.
- Reworked the Audit template to use the same AtlasVM panel/table styling as the rest of the new UI shell.
- Added table wrapper styling for `.table-scroll` and `.table-responsive`.
- Added `.task-message`, `.message-cell`, `.timestamp-cell`, `.action-cell`, and `.empty-cell` styles.
- Corrected the Tasks empty-state colspan from 7 to 8.

## Validation

Run on the AtlasVM host after deployment:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
```

## Next Phase

Phase 11.5.3 should focus on Virtual Machines list polish: state pills, node/locality labels, compact action controls, and Phase 12 multi-node readiness indicators.
