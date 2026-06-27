# AtlasVM Phase 7.5 UI Cleanup

Phase 7.5 focuses on interface cleanup before VLAN-aware networking.

## Included

- Replaced the prior logo with the wide navy/yellow AtlasVM logo.
- Preserved logo aspect ratio in the shared header and login page.
- Removed duplicate header text: `AtlasVM` and `Single-node KVM/libvirt virtualization manager`.
- Added a branded `/login` page.
- Added `/logout`.
- Added signed browser-session cookie authentication.
- Kept HTTP Basic fallback for curl/API-style access.
- Updated README branding asset at `docs/assets/atlasvm-logo.png`.

## Notes

Set `ATLASVM_SESSION_SECRET` in `.env` for stable production session signing. If omitted, AtlasVM falls back to the configured admin password as the signing secret.
