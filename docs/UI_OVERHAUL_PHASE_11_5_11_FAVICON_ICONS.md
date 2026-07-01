# AtlasVM Phase 11.5.11 - Favicon and Web Icon Set

## Purpose

Phase 11.5.11 adds the AtlasVM icon artwork as the browser favicon and related web/app icons.

This phase is an asset and template metadata update. It does not change VM, node, storage, backup, task, audit, libvirt, or database behavior.

## Files added or updated

Updated:

- `app/templates/base.html`
- `app/main.py`

Added:

- `app/static/favicon.ico`
- `app/static/favicon-16x16.png`
- `app/static/favicon-32x32.png`
- `app/static/favicon-48x48.png`
- `app/static/apple-touch-icon.png`
- `app/static/android-chrome-192x192.png`
- `app/static/android-chrome-512x512.png`
- `app/static/mstile-150x150.png`
- `app/static/site.webmanifest`
- `app/static/browserconfig.xml`
- `app/static/atlasvm-favicon-source.png`
- `docs/assets/atlasvm-favicon-source.png`

## Template changes

`base.html` now declares:

- standard favicon ICO
- PNG favicons for 16, 32, and 48 px
- Apple touch icon
- web app manifest
- browser tile color metadata
- theme color metadata

## Runtime compatibility

A lightweight `/favicon.ico` route was added to return `app/static/favicon.ico` directly. This prevents browsers and older clients from requesting `/favicon.ico` and receiving a 404 even though the template points at `/static/favicon.ico`.

## Validation

Run after deployment:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py scripts/*.py
python scripts/audit_ui_routes.py
systemctl restart atlasvm
systemctl restart nginx
```

Then hard refresh the browser or open an incognito window. If the browser tab still shows the old favicon, clear site data or directly open:

```text
https://<atlasvm-host>/favicon.ico
https://<atlasvm-host>/static/favicon-32x32.png
https://<atlasvm-host>/static/site.webmanifest
```
