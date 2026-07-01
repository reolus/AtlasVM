# AtlasVM Phase 2 Design

## Goal

Phase 2 makes AtlasVM usable for a single-node KVM host. The major objective is to let an administrator create a VM, select an ISO, open a browser console, install the OS, manage power state, inspect storage/network information, and create snapshots.

## New Modules

- `ConsoleService`: starts noVNC/websockify proxy sessions against VM VNC ports.
- `TaskLog`: stores visible task results for long-running or destructive actions.
- Expanded `LibvirtService`: handles ISOs, storage pool volumes, networks, autostart, snapshots, and VM details.

## UI Pages

- `/`: dashboard
- `/vms/new`: VM creation wizard
- `/vms/{name}`: VM detail page
- `/vms/{name}/console`: browser console wrapper
- `/isos`: ISO library
- `/storage`: storage pool overview
- `/storage/{name}`: storage volume detail
- `/networks`: libvirt network overview
- `/tasks`: task log
- `/events`: audit log

## Console Flow

1. User clicks Console.
2. AtlasVM asks libvirt for the VM VNC display.
3. AtlasVM starts a noVNC/websockify proxy on a port in the configured range.
4. User is redirected to a console wrapper page with an iframe and direct link.

## Snapshot Caveat

Phase 2 uses libvirt snapshots. Snapshot behavior depends heavily on disk format and VM state. qcow2-backed VM disks are the intended Phase 2 path. Native ZFS snapshot orchestration should be added later.

## Security Caveat

Phase 2 uses HTTP Basic authentication and runs as root for simplicity. This is acceptable for a lab or isolated management network, not a hardened multi-tenant platform. Phase 3 should add real users, sessions, password hashing, API tokens, and a least-privilege execution model.
