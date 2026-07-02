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

## VM Associated Page Theme Pass

This follow-up pass updates the VM detail satellite pages so they use the same local Bootstrap-compatible and Font Awesome-compatible AtlasVM CE theme introduced in the UI overhaul.

Updated pages include:

- `/vms/new` create VM form
- `/vms/{name}` VM details polish
- `/vms/{name}/disks` disk management
- `/vms/{name}/network` NIC management
- `/vms/{name}/metrics` VM metrics
- `/vms/{name}/clone` clone confirmation/form
- `/vms/{name}/delete-confirm` delete confirmation
- `/templates` VM templates
- `/isos` ISO library
- `/vms/{name}/console` console page

The patch is visual/template-focused. It does not introduce libvirt undefine/redefine flows for normal VM changes. Existing VM mutation behavior remains routed through the current service methods and route handlers.

## Console handling follow-up

The VM console page and start route were updated after a real-world failure where `/ui/vms/{name}/console` raised `RuntimeError: VM does not expose a VNC console`. AtlasVM now redirects to the themed console page with a useful error instead of throwing a server-side 500.

This intentionally does not add or redefine VM graphics devices. Adding a VNC graphics device by undefining/redefining a domain is not snapshot-safe and remains outside normal UI behavior. The console page now explains that the VM must be running and must already expose an active VNC display.

## Console query-string fix

Follow-up console patch: fixed nested noVNC URL redirect handling.

AtlasVM redirects to `/vms/{name}/console?url=...` after starting a noVNC proxy. The noVNC URL itself contains query parameters such as `host`, `port`, `autoconnect`, and `resize`. Those nested query characters must be fully percent-encoded before being placed into AtlasVM's outer `url=` parameter.

Without full encoding, the browser could receive a truncated noVNC URL like:

```text
/vnc.html?host=10.21.50.34
```

instead of:

```text
/vnc.html?host=10.21.50.34&port=6090&autoconnect=1&resize=scale
```

The console redirect now uses a helper that encodes the entire noVNC URL as the value of the outer `url` parameter. This preserves the noVNC port and connection options.

## Follow-up: standalone console and network/storage theme pass

- Changed console launch behavior so successful console starts redirect directly to the standalone noVNC URL. VM detail console buttons already open in a new tab, so browser iframe/security issues no longer block the primary console workflow.
- Left the themed `/vms/{name}/console` page available for error handling and manual launch guidance.
- Updated VM inventory console actions to open in a new tab as well.
- Overhauled network pages to the Bootstrap-style local theme:
  - `/networks`
  - `/networks/new`
  - `/networks/{name}`
  - `/networks/{name}/edit`
  - `/host/network`
- Overhauled storage pages/forms to the newer theme:
  - `/storage`
  - `/storage/networks/new`
  - `/storage/nfs/new`
  - `/storage/smb/new`
  - `/storage/iscsi/new`
  - `/storage/iscsi/{name}/lvm-thin`
  - `/storage/{pool_name}` template styling where used
  - `/zfs`
- No libvirt undefine/redefine behavior was added. Console handling still only starts noVNC/websockify against an existing VNC display.

## Admin section theme follow-up

The Admin section pages were normalized to the same local Bootstrap-compatible / Font Awesome-compatible AtlasVM theme used by the dashboard, VM, network, and storage sections.

Updated pages include:

- `/admin`
- `/backups`
- `/doctor`
- `/tasks`
- `/audit`
- `/events`
- `/users`
- `/settings`
- `/zfs`
- `/host/network`

Changes include themed hero headers, Admin return actions, KPI/stat cards where useful, `atlas-panel` cards, `atlas-table-wrap` responsive tables, consistent form/button styling, and Admin active-section context for pages that previously rendered without the shared sidebar context.

No VM XML mutation logic was changed. This UI pass does not use libvirt undefine/redefine.
