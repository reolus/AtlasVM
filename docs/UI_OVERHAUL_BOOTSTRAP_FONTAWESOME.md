# AtlasVM Community Edition UI Overhaul

This patch introduces a Bootstrap-inspired and Font Awesome-compatible local UI layer for AtlasVM Community Edition.

## Changes

- Added local vendored Bootstrap-compatible CSS/JS under `app/static/vendor/bootstrap/`.
- Added local Font Awesome-compatible icon CSS under `app/static/vendor/fontawesome/`.
- Reworked the main shell in `app/templates/base.html`.
- Sidebar is fixed full-height and no longer scrolls away with page content.
- Sidebar has its own internal scroll region for contextual trees and menus.
- Rock Bluffs Labs, LLC is pinned to the sidebar bottom.
- Added top icon navigation for Dashboard, VM, Network, Storage, and Admin.
- Added contextual sidebar trees for VM, Network, Storage, and Admin sections.
- Added `app/services/ui_sidebar.py` to safely build sidebar context.
- Added `/admin` and `app/templates/admin.html` so the Admin icon no longer 404s.
- Reworked dashboard, login, and VM inventory templates around the new shell.
- Fixed active-section detection so Dashboard is only active on `/`.
- Admin grouping now covers `/admin`, `/backups`, `/doctor`, `/tasks`, `/audit`, `/users`, `/settings`, `/zfs`, and `/host/network`.
- VM grouping covers `/vms`, `/ui/vms`, `/templates`, and `/isos`.
- Network grouping covers `/networks`.
- Storage grouping covers `/storage`.

## Local vendor assets

The Bootstrap and Font Awesome files in this patch are local compatibility subsets, not the official full upstream distributions. They exist so AtlasVM Community Edition works on isolated/internal networks without CDN access. Replace them with official full distributions later if desired.

## Route and edition notes

- `/admin` is an active Community Edition route.
- No active `/nodes` or `/api/node` routes are registered in `app/main.py`.
- Multi-node and enterprise manager concepts remain dormant/future-only for this branch.

## Validation performed

```bash
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
```

Additional static checks were performed for:

- `/admin` route presence.
- Jinja2 template parse validity.
- Absence of active `/nodes` and `/api/node` routes in `app/main.py`.
- Absence of CDN references in active app templates/static assets.
