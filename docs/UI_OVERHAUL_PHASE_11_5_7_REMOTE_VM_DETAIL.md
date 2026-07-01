# AtlasVM Phase 11.5.7 - Remote VM Detail and Multi-Node Readiness Polish

## Scope

UI-only polish for the remote VM detail page used by Phase 12 multi-node testing.

## Changed files

- `app/templates/remote_vm_detail.html`
- `app/static/style.css`
- `docs/UI_OVERHAUL_PHASE_11_5_7_REMOTE_VM_DETAIL.md`
- `docs/audits/ui_route_form_audit_phase_11_5_7.txt`

## Summary

The remote VM detail page now matches the Phase 11.5 UI shell and local VM detail style.

Improvements:

- Modern remote VM hero header.
- Remote/local ownership badges.
- State/autostart badges.
- KPI cards for state, vCPU, memory, disks, NICs, and node ownership.
- Section jump navigation.
- Remote-safe power action cards.
- Clear owning-node metadata panel.
- Storage and network resource cards with wrapping long values.
- Direct remote-console card.
- Explicit remote action boundary panel.
- Clear unavailable state when remote lookup fails.

## Intentional limitations

The manager still exposes only remote-safe power actions for remote VMs:

- start
- shutdown
- reboot
- force power off

The page remains read-only for storage, NICs, ISO, snapshots, backups, clone, and delete operations.
Those should remain local to the owning node until Phase 12 validates remote mutation rules.

## Validation

Run from `/opt/atlasvm` after deployment:

```bash
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
```

## Manual test checklist

- `/vms` remote VM Open link reaches `/nodes/{node_id}/vms/{vm_name}`.
- Remote VM detail loads when remote node is online.
- Offline/lookup failure state renders cleanly.
- Start button appears for stopped remote VM.
- Shutdown/Reboot/Force Off appear for running remote VM.
- Storage paths wrap without horizontal scrollbars.
- NIC values wrap without horizontal scrollbars.
- Owning node link works.
- Remote console link opens the owning node URL directly.
