# AtlasVM Phase 6 Design

Phase 6 focuses on ZFS and backup maturity. The goal is to make AtlasVM aware of the storage layer instead of treating every disk as a plain file forever, because pretending storage does not matter is how people accidentally build sadness.

## Phase 6A: ZFS-native snapshot management

AtlasVM now exposes expanded ZFS snapshot management from the `/zfs` page.

Capabilities:

- View pool health, pool capacity, and warning banners.
- View datasets with used space, available space, snapshot usage, quota fields, and mountpoints.
- Create normal or recursive ZFS snapshots.
- Destroy ZFS snapshots.
- View recent ZFS snapshots.

The UI keeps ZFS snapshots separate from libvirt snapshots. They are not the same feature and should not be described as interchangeable.

## Phase 6B: Backup retention policy

AtlasVM continues to support the simple per-VM retention policy from configuration:

```text
ATLASVM_BACKUP_KEEP_LAST=5
```

Phase 6 adds a manual retention action from the `/backups` page. Operators can apply retention across all VMs or a specific VM and can override the keep count for that run.

This is intentionally conservative: keep the newest N backups per VM, delete older backup directories, and remove matching archives.

## Phase 6C: ZFS send export

AtlasVM now supports exporting ZFS snapshots using `zfs send`.

Capabilities:

- Queue a ZFS send export as a background task.
- Optional recursive export using `zfs send -R`.
- Optional zstd compression when `zstd` is installed.
- Write exports under the AtlasVM backup path in `zfs-exports`.
- Write sidecar metadata JSON for each export.
- List and delete ZFS send exports from the UI.

Default export location:

```text
<ATLASVM_BACKUP_PATH>/zfs-exports
```

## Phase 6D: Storage health warnings

The ZFS page now surfaces warnings for:

- Pool health not `ONLINE`.
- Pool capacity over 80 percent.
- Pool capacity over 90 percent.
- zpool status indications that should be reviewed.

These warnings are also available from `zfs_service.pool_status()` for future dashboard integration.

## Safety posture

Phase 6 deliberately does not add ZFS rollback from the web UI. Rollback is dangerous because it can discard writes and break VM state expectations. If rollback is added later, it should require explicit typed confirmation and VM shutdown validation.

## Future work

Phase 7 should focus on networking. Later ZFS work should add replication targets, receive/import workflows, snapshot retention classes, and VM-aware ZFS snapshot grouping.
