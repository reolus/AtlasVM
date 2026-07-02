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
- Added `/admin` and `app/templates/admin.html`.
- Reworked dashboard, login, and VM inventory templates around the new shell.

## Notes

The Bootstrap and Font Awesome files are locally vendored compatibility subsets so the interface works without public CDN access. Replace them with official full distributions later if desired.
