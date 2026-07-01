# AtlasVM Phase 11.5.6 - VM Detail No-Scroll Resource Cards

## Purpose

Fix the VM detail Storage and Network panels so short resource lists do not show unnecessary horizontal scrollbars.

## Changes

- Replaced the Storage mini-table with a wrapping resource-list layout.
- Replaced the Network mini-table with a responsive resource-card layout.
- Added `.resource-list`, `.resource-row`, `.wrap-code`, and `.nic-resource-row` styles.
- Long disk paths, MAC addresses, bridges, and network names now wrap inside the card instead of creating horizontal scrollbars.

## Files changed

- `app/templates/vm_detail.html`
- `app/static/style.css`
- `docs/UI_OVERHAUL_PHASE_11_5_6_VM_DETAIL_NO_SCROLL.md`
- `docs/audits/ui_route_form_audit_phase_11_5_6.txt`

## Scope

UI-only. No backend, route, libvirt, task, backup, or database changes.
