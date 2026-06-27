# AtlasVM Phase 5 Design

Phase 5 completes four production-operations foundations:

## Phase 5A: Background task queue

Long-running operations now queue background tasks instead of blocking the browser request. The first implementation is intentionally single-node and in-process. It records task rows with `queued`, `running`, `success`, and `failed` status values in the existing task table.

Background-enabled operations include VM backup, VM clone, template clone, and backup restore-as-new.

## Phase 5B: Restore backup as new VM

Backups can now restore as a new VM. AtlasVM copies backed-up disks into the selected/default storage pool, rewrites the VM XML with a new name, removes the old UUID, clears fixed VNC ports, removes MAC addresses so libvirt generates fresh addresses, and defines the VM.

The older definition-only restore remains available for emergency/manual recovery.

## Phase 5C: Template and clone workflow

Powered-off VMs can be marked as templates. Template clones use full disk copies through qemu-img and are queued as background tasks. Clones are cleaned so they do not retain the template marker, stale UUID, fixed console port, or old MAC addresses.

## Phase 5D: VM metrics

AtlasVM now exposes basic libvirt metrics for each VM, including CPU time, memory stats where available, block I/O, and interface I/O. Metrics are visible from the VM detail page and a dedicated VM metrics page.

## Limitations

- Background jobs are in-process. If the AtlasVM service restarts, running jobs may be interrupted.
- Restore-as-new assumes file-backed disks in the backup metadata.
- Template state is stored as an AtlasVM marker in the VM description.
- Metrics depend on libvirt and QEMU availability; inactive guests may show partial counters.
