# AtlasVM Phase 11.5.3 - Logo Refresh

## Scope

This phase refreshes the AtlasVM sidebar/product logo asset only.

## Changes

- Replaced `app/static/atlasvm-logo.png` with the updated logo whose background matches the sidebar.
- Replaced `docs/assets/atlasvm-logo.png` with the same updated source asset.
- No backend, route, service, libvirt, task, audit, or database logic was changed.

## Validation

This is a static asset-only change. After deployment, restart AtlasVM/nginx if needed and hard-refresh the browser.

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
```

Then refresh the browser with Ctrl+F5.
