# AtlasVM Phase 10 Design: Backup, Restore, Retention, and Clone Reliability

Phase 10 turns AtlasVM backups into a storage-aware protection layer instead of a simple file copy.

## Goals

- Back up file-backed VM disks and block-backed LVM/LVM-thin disks.
- Store portable qcow2 backup images with VM XML and metadata.
- Restore a backup as a new VM to either directory/qcow2 storage or logical/LVM storage.
- Allow backup targets with roles and health checks.
- Apply retention per VM and per backup target.
- Improve clone/delete behavior for block-backed VM disks.
- Surface backup target and backup inventory health in Doctor.

## Backup model

A Phase 10 backup directory contains:

```text
metadata.json
<vm-name>.xml
disks/
  disk1.qcow2
  disk2.qcow2
```

The backup image format is qcow2 even when the source disk is a raw block device. This makes backups portable and lets restore choose the target storage type later.

## Supported source disk types

- `disk type='file'` with `source file=...`
- `disk type='block'` with `source dev=...`
- named sources are recorded when visible, though restore expects backup qcow2 images.

## Backup consistency

If the VM is stopped, backups are marked `offline-consistent`.

If the VM is running and shutdown-only protection is disabled, backups are marked `crash-consistent`. That is useful, but not magic. Application-consistent backups still require guest/application cooperation. Computers remain petty.

## Backup targets

AtlasVM always exposes the default backup path from settings. Additional targets are stored in:

```text
/opt/atlasvm/atlasvm_backup_targets.json
```

Targets may point to local, NFS, SMB, or custom mounted paths. The UI only treats them as usable if the path exists and is writable.

## Restore as new VM

Restore reads the backed-up VM XML, removes the UUID and MAC addresses, optionally changes networks, recreates disks in the selected storage pool, and defines the VM under a new name.

Directory-like pools receive qcow2 disks. Logical/LVM pools receive raw block volumes populated from the backup qcow2 image.

## Retention

Phase 10 retention keeps the most recent N backups per VM per target. This is intentionally conservative and predictable.

## Clone reliability

Clone logic is tightened to detect file and block disk sources. It creates destination volumes in the selected pool and uses `qemu-img convert` instead of assuming every disk is a qcow2 file.
