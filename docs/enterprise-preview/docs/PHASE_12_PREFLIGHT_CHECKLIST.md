# AtlasVM Phase 12 Pre-Flight Checklist

Phase 12 introduces multi-node testing. This checklist is intended to be completed before adding or exercising additional nodes.

## 1. Code and service validation

Run these commands on the AtlasVM manager before testing:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
systemctl status atlasvm --no-pager
journalctl -u atlasvm -n 120 --no-pager
```

Pass criteria:

- Python compile succeeds.
- UI route/form audit completes.
- `atlasvm.service` is active.
- nginx is active.
- No new traceback appears in the AtlasVM journal.

## 2. Local baseline hold point

Before registering or testing additional nodes:

- Confirm the local dashboard loads.
- Confirm `/vms` loads.
- Confirm one local VM detail page loads.
- Confirm `/nodes` loads.
- Confirm `/nodes/compatibility` loads.
- Confirm `/tasks` and `/audit` load without horizontal overflow.

Do not continue to multi-node testing if the single-node UI is unstable.

## 3. Backup and rollback hold point

Before Phase 12 testing:

```bash
cd /opt/atlasvm
cp atlasvm.db "atlasvm.db.pre_phase12.$(date +%Y%m%d_%H%M%S).bak"
git status
```

Recommended:

- Commit or tag the repository before testing.
- Keep the Phase 11.5.10 ZIP available.
- Record the currently deployed git commit.
- Confirm at least one known-good rollback command path.

## 4. Node registration readiness

Minimum readiness:

- At least two nodes are registered or ready to register.
- Manager node is online.
- Remote node API URL is reachable from the manager.
- Node token is known and installed.
- Time is reasonably synchronized between nodes.
- DNS or IP naming is stable.
- Firewalls allow the required AtlasVM node API traffic.

## 5. Compatibility review

Open:

```text
/nodes/compatibility
```

Pass criteria:

- No blocking compatibility errors.
- Warnings are understood and documented.
- Storage pool differences are expected.
- Network/bridge differences are expected.
- Version/API differences are expected or remediated.

Do not proceed if compatibility errors affect inventory, VM lookup, or safe remote power actions.

## 6. Remote boundary confirmation

Phase 12 should initially validate conservative remote control only.

Allowed during first pass:

- Remote inventory view.
- Remote VM detail view.
- Remote console link behavior.
- Remote safe power actions where supported.

Not in scope for first pass unless explicitly promoted later:

- Remote disk add/remove.
- Remote NIC add/remove/update.
- Remote ISO attach/eject.
- Remote snapshot create/revert/delete.
- Remote backup.
- Remote clone.
- Remote delete.

## 7. Operator signoff

Before beginning tests, record:

- Date/time.
- AtlasVM version or git commit.
- Manager node name/IP.
- Remote node name/IP.
- Known-good local VM used for baseline.
- Remote VM used for visibility tests.
- Remote VM used for power tests, if any.
- Rollback point.

