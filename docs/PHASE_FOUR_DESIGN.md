# AtlasVM Phase 4 Design

Phase 4 begins the production-hardening pass for AtlasVM.

## Included in this build

- AtlasVM logo integrated into the web interface and packaged as `/static/atlasvm-logo.png`.
- Local user database with PBKDF2 password hashing.
- Automatic seeding of the first admin user from the existing `ATLASVM_USERNAME` and `ATLASVM_PASSWORD` settings.
- Admin-only user management page at `/users`.
- Read-only platform settings page at `/settings`.
- VM detail page now passes `current_iso` and shows the loaded installer ISO.
- Template-safe ISO dropdown behavior so the page does not explode when no media is loaded.
- Phase 4 docs for upgrade and operating notes.

## Deferred Phase 4 work

The next hardening pass should add true background task execution, CSRF/session-based auth, viewer/operator enforcement, cloud-init templates, ZFS-native VM snapshot workflows, live guest-agent-aware backups, VM metrics, and editable platform settings.

## Security note

This build still supports HTTP Basic authentication for compatibility. The local user table is a step toward real user management, not the final destination. The next security pass should move to signed sessions and CSRF-protected forms.
