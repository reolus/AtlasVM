# AtlasVM Phase 3 Design

Phase 3 turns AtlasVM from a basic single-node VM launcher into a more practical single-node virtualization manager.

## Added in Phase 3

- Host dashboard with VM, task, backup, and ZFS health summaries.
- Improved browser console behavior using direct `websockify --web /usr/share/novnc`.
- Console opens cleanly in a new tab from the VM detail page.
- Robust VNC display parsing for `:0`, `0`, and raw TCP port formats.
- VM basic edit workflow for memory, vCPU, and description.
- Attach/eject ISO workflow for powered-off VMs.
- Add disk workflow for powered-off VMs.
- Offline clone workflow using `qemu-img convert`.
- Shutdown-first backup workflow with XML export, disk copy/convert, metadata, optional compression, and retention pruning.
- Backup listing page and definition restore workflow.
- ZFS pool, dataset, and snapshot visibility.
- ZFS scrub start action.
- ZFS dataset snapshot action.
- Safer VM delete form requiring typed VM name confirmation.
- AtlasVM Doctor page and CLI helper.
- API phase marker updated to Phase 3.

## Backup behavior

The first backup implementation is intentionally conservative. By default, AtlasVM requires the VM to be shut down before backup. This avoids pretending crash-consistent live backup is solved just because a web button exists. Backups are stored under `ATLASVM_BACKUP_PATH` and include:

- VM libvirt XML
- metadata.json
- copied or converted disks
- optional `.tar.zst` archive when `zstd` is installed, otherwise `.tar.gz`

## Limitations

- VM edits require the guest to be shut down.
- ISO attach/eject requires shutdown.
- Add disk requires shutdown.
- Clone requires shutdown.
- Backup restore currently restores the VM definition. Disk restore is staged by backup metadata but should still be reviewed before production use.
- Local user/RBAC is still not implemented. Basic auth remains in place.

These limits are deliberate. This is the part where the software tries not to eat your storage pool while pretending to be enterprise-grade.
