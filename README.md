# AtlasVM Phase 2

AtlasVM is a single-node KVM/libvirt virtualization manager built on FastAPI. Phase 2 turns the original proof of concept into a usable host manager with browser console support, ISO management, VM detail pages, snapshots, storage visibility, network visibility, task history, and audit logging.

This is not a Proxmox replacement yet. It is a product foundation. A small one. With sharp edges. Naturally.

## Phase 2 Features

- Dashboard with host and VM summary
- VM creation wizard with ISO picker, firmware option, autostart, and start-after-create
- VM details page
- VM power actions: start, shutdown, reboot, force stop
- VM delete with or without disks
- Browser console launch using noVNC/websockify
- ISO library with upload and delete
- Storage pool list/detail/refresh
- Network list and start/stop/autostart actions
- Basic libvirt snapshot create/list/revert/delete
- Task log
- Audit/event log
- REST API under `/api/v1`
- Debian install script and systemd service

## Host Requirements

Recommended host layout:

- Debian 13
- KVM-capable CPU
- libvirt/QEMU
- ZFS or directory-backed storage pool for VM disks
- ISO directory such as `/atlasvm-vmdata/iso`
- VM disk directory such as `/atlasvm-vmdata/vm-disks`

## Install

From the repo directory on the AtlasVM host:

```bash
sudo ./scripts/install_debian.sh
sudo nano /etc/atlasvm/atlasvm.env
sudo systemctl start atlasvm
sudo systemctl status atlasvm --no-pager
```

Open:

```text
http://ATLASVM-IP:8443
```

## Important Debian Python Note

The app expects Debian's packaged libvirt bindings:

```bash
apt install -y python3-libvirt
python3 -m venv --system-site-packages .venv
```

Do not install `libvirt-python` from pip unless you enjoy debugging compiler output instead of your product.

## noVNC Console

Install:

```bash
apt install -y novnc websockify
```

AtlasVM starts a noVNC proxy per VM when the Console button is clicked. The VM must have VNC graphics in libvirt XML. AtlasVM-created VMs include this by default.

Console ports default to `6080-6099`. Change these in `/etc/atlasvm/atlasvm.env` if needed.

## Environment

Example:

```env
ATLASVM_APP_NAME=AtlasVM
ATLASVM_HOST=0.0.0.0
ATLASVM_PORT=8443
ATLASVM_USERNAME=admin
ATLASVM_PASSWORD=change-this-password
ATLASVM_DATABASE_URL=sqlite:///./atlasvm.db
ATLASVM_LIBVIRT_URI=qemu:///system
ATLASVM_DEFAULT_STORAGE_POOL=atlasvm-default
ATLASVM_ISO_POOL=atlasvm-iso
ATLASVM_DEFAULT_NETWORK=default
ATLASVM_VM_DISK_PATH=/atlasvm-vmdata/vm-disks
ATLASVM_ISO_PATH=/atlasvm-vmdata/iso
ATLASVM_CONSOLE_PORT_BASE=6080
ATLASVM_CONSOLE_PORT_MAX=6099
```

## Smoke Test

```bash
curl -u admin:'YOUR_PASSWORD' http://127.0.0.1:8443/api/v1/health
curl -u admin:'YOUR_PASSWORD' http://127.0.0.1:8443/api/v1/host
curl -u admin:'YOUR_PASSWORD' http://127.0.0.1:8443/api/v1/vms
```

## Development Notes

Phase 2 still runs privileged as root because it manages libvirt, storage files, and console proxies. Phase 3 should introduce a narrower service user and polkit rules instead of letting root handle everything like a medieval king with a flamethrower.

## Phase 3

Phase 3 adds a host/ZFS health dashboard, VM edit workflows, offline clone, shutdown-first backups, backup listing, definition restore, ZFS visibility/actions, safer delete confirmations, and AtlasVM Doctor.

Important routes:

- `/doctor` - AtlasVM sanity checks
- `/zfs` - ZFS pools, datasets, snapshots, scrub
- `/backups` - Backup inventory and definition restore
- `/vms/{name}` - VM detail, edit, backup, clone, ISO attach/eject, add disk

See `docs/PHASE_THREE_DESIGN.md` and `docs/PHASE_THREE_UPGRADE.md`.
