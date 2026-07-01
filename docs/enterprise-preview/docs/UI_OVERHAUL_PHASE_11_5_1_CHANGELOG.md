# AtlasVM Phase 11.5.1 UI Polish Changelog

## Scope

This phase applies the next UI polish pass after the Phase 11.5 route/form/service cleanup and initial dark shell overhaul.

No VM mutation service behavior was changed in this phase. This patch is focused on layout polish, navigation clarity, and visual consistency.

## Fixed / Improved

### Sidebar logo

- Changed the sidebar brand area to use the full AtlasVM logo image instead of a tiny icon plus duplicate text.
- Increased logo display width to 168px on desktop.
- Centered and enlarged the logo on responsive/mobile layouts.

### Active navigation

- Added active state logic in `app/templates/base.html` based on `request.url.path`.
- Sidebar items now highlight when the current route belongs to that section.
- `/vms` and `/ui/vms` both activate the Virtual Machines section for backward compatibility.

### Topbar page identity

- Replaced the raw path-only display with a page title and short descriptive hint.
- Added page mapping for Dashboard, Virtual Machines, Nodes, Storage, Networks, Templates, ISO Library, Backups, Tasks, Host Network, ZFS, Doctor, Audit, Settings, Users, and Login.

### Visual polish

- Slightly softened text contrast with `--text-soft` and updated table text color.
- Increased content padding from 1.35rem to 1.75rem on desktop.
- Increased topbar height slightly for better page title hierarchy.
- Added table row hover state.
- Strengthened sidebar hover and active states.
- Added sidebar overflow handling for smaller screens.

## Validation performed in sandbox

```bash
python3 -m py_compile app/main.py app/services/*.py scripts/*.py
python3 scripts/audit_ui_routes.py
```

The static route/form audit was saved to:

```text
docs/audits/ui_route_form_audit_phase_11_5_1.txt
```

## Next recommended phase

Phase 11.5.2 should focus on the Virtual Machines list page:

- Replace any remaining old table-only presentation with a cleaner operations console layout.
- Add state pills for running/stopped/template status.
- Add node labels and local/remote indicators.
- Make power/action buttons compact and consistent.
- Prepare the VM list for Phase 12 multi-node testing by showing whether each VM is local, remote, or unavailable.
