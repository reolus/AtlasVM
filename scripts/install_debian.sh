#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/phase1-vm-manager"
APP_USER="phase1vm"

apt update
apt install -y python3 python3-venv python3-pip qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev pkg-config gcc virtinst bridge-utils genisoimage qemu-utils qemu-utils

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

usermod -aG libvirt,kvm "$APP_USER"
mkdir -p "$APP_DIR"
cp -R . "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Edit $APP_DIR/.env and change APP_PASSWORD before starting the service."
fi

.venv/bin/python scripts/init_db.py
cp systemd/phase1-vm-manager.service /etc/systemd/system/phase1-vm-manager.service
systemctl daemon-reload
systemctl enable phase1-vm-manager

echo "Installed. Edit $APP_DIR/.env, then run: systemctl start phase1-vm-manager"
