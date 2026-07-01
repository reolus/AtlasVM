# AtlasVM LVM-thin Storage Fix

This patch fixes two related issues:

1. New VM creation on logical/LVM-thin pools must create thin logical volumes with `lvcreate -V ... -T <vg>/<thinpool>` instead of creating qcow2 file paths under `/dev`.
2. The New VM storage selector should show LVM-thin usable free space from the thin pool `Data%`, not the remaining unallocated VG space reported by libvirt.

For logical pools with a detected thin pool, AtlasVM now creates raw block-backed disks and attaches them with `<disk type='block'>` and `<source dev='...'>`.
