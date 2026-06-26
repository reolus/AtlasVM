# Debian Install Notes

## 1. Install host packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev pkg-config gcc virtinst bridge-utils genisoimage qemu-utils qemu-utils
```

## 2. Confirm virtualization support

```bash
lscpu | grep Virtualization
systemctl status libvirtd
virsh list --all
```

## 3. Confirm default network and storage

```bash
virsh net-list --all
virsh pool-list --all
```

If the default network is inactive:

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

If the default storage pool does not exist:

```bash
sudo mkdir -p /var/lib/libvirt/images
sudo virsh pool-define-as default dir --target /var/lib/libvirt/images
sudo virsh pool-start default
sudo virsh pool-autostart default
```

## 4. Install app

```bash
cd phase1_vm_manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8443
```

## 5. Production-ish deployment

Use the included systemd unit as a starting point:

```bash
sudo ./scripts/install_debian.sh
sudo nano /opt/phase1-vm-manager/.env
sudo systemctl start phase1-vm-manager
sudo systemctl status phase1-vm-manager
```

Do not expose this naked to the internet. Put it behind TLS and a VPN unless you want your hypervisor to become a community resource.
