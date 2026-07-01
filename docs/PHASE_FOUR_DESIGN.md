# AtlasVM Phase 4 Design

Phase 4 turns AtlasVM from a single-admin lab interface into a more controlled management surface.

## Completed in this package

- AtlasVM logo integrated into the shared web header and favicon.
- Database-backed local users with PBKDF2 password hashing.
- Role helpers: viewer, operator, admin.
- Role enforcement for core routes.
- User administration: create, disable/enable, delete, change role, reset password.
- Guardrails preventing removal of the last active administrator.
- Editable settings page that writes safe platform settings to `.env`.
- Flash-style success/error messages through redirects.
- Backup delete action and backup restore-definition workflow retained.
- Improved Users, Settings, and Backup templates.

## Roles

- Viewer: read-only UI access.
- Operator: VM operations, snapshots, backups, ISO upload/delete, storage refresh, ZFS actions.
- Admin: users, settings, network changes, VM deletion, and everything below.

## Still intended for a later phase

- True asynchronous background job worker.
- Live application-consistent backup using QEMU guest agent freeze/thaw.
- Full ZFS-native VM replication.
- Cloud-init template provisioning.
- Network creation/editing wizard.
