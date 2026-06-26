# Phase One VM Manager

A small single-node virtualization manager for Debian/Ubuntu using FastAPI, libvirt, KVM/QEMU, Linux bridges, and a simple web UI.

## Features

- Web dashboard
- Local username/password login
- Host resource summary
- List virtual machines
- Create basic KVM virtual machines
- Start, shutdown, force stop, reboot, and delete VMs
- Attach ISO media
- Create and delete qcow2 disks
- List libvirt storage pools
- List Linux bridges detected from libvirt networks
- Task/event log stored in SQLite
- API-first design
- Systemd unit example

## Target platform

Test target:

- Debian 12 or Ubuntu Server 22.04/24.04
- KVM-capable CPU
- libvirt/QEMU installed
- Python 3.11+

## Quick install

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev pkg-config gcc virtinst bridge-utils genisoimage qemu-utils
sudo usermod -aG libvirt,kvm $USER
newgrp libvirt

cd phase1_vm_manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8443
```

Open:

```text
http://SERVER-IP:8443
```

Default login is controlled in `.env`.

## Important safety note

This app manages local virtualization through libvirt. Do not expose it directly to the internet. Put it behind a VPN, reverse proxy with TLS, or both. Humanity already has enough problems without unauthenticated VM control panels floating around.

## Project structure

```text
app/
  main.py                 FastAPI app
  api/                    REST endpoints
  core/                   config, database, auth helpers
  services/               libvirt and VM logic
  templates/              Jinja2 HTML pages
  static/                 CSS
scripts/
  init_db.py              initializes SQLite database
  install_debian.sh       rough Debian installer
systemd/
  phase1-vm-manager.service
```

## API examples

List VMs:

```bash
curl -u admin:change-this-password http://localhost:8443/api/v1/vms
```

Create a VM:

```bash
curl -u admin:change-this-password -X POST http://localhost:8443/api/v1/vms \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"test-vm",
    "memory_mb":2048,
    "vcpus":2,
    "disk_gb":20,
    "storage_pool":"default",
    "network":"default",
    "iso_path":"/var/lib/libvirt/images/debian.iso"
  }'
```

Start VM:

```bash
curl -u admin:change-this-password -X POST http://localhost:8443/api/v1/vms/test-vm/start
```

## Roadmap for Phase Two

- noVNC console proxy
- Cloud-init template support
- VM clone workflow
- Scheduled snapshots
- ZFS and LVM-thin abstractions
- Better RBAC
- Audit trail export
- Host network bridge creation
- Backup/restore jobs
