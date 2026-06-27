#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/atlasvm"
ENV_FILE="/etc/atlasvm/atlasvm.env"
SERVICE_FILE="/etc/systemd/system/atlasvm.service"

echo "Installing AtlasVM Phase 2 host packages..."
apt update
apt install -y \
  qemu-system-x86 qemu-utils \
  libvirt-daemon-system libvirt-clients virtinst libosinfo-bin \
  ovmf swtpm \
  bridge-utils dnsmasq-base iproute2 iptables nftables \
  python3 python3-venv python3-pip python3-dev python3-libvirt \
  build-essential pkg-config libvirt-dev libxml2-dev libxslt1-dev zlib1g-dev \
  git curl unzip sudo \
  zfsutils-linux lvm2 thin-provisioning-tools parted gdisk smartmontools \
  htop iotop iftop nload lsof ncdu jq tree rsync net-tools tcpdump ethtool lm-sensors \
  nginx openssl acl polkitd pkexec \
  novnc websockify

systemctl enable --now libvirtd virtlogd virtlockd

mkdir -p /etc/atlasvm /var/lib/atlasvm /var/log/atlasvm /run/atlasvm
mkdir -p /srv/atlasvm/imports /srv/atlasvm/uploads /srv/atlasvm/tmp

if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

if [ ! -d "$APP_DIR" ]; then
  mkdir -p "$APP_DIR"
fi
rsync -a --delete --exclude '.git' ./ "$APP_DIR/"

cd "$APP_DIR"
rm -rf .venv
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

cat > "$SERVICE_FILE" <<'SERVICE'
[Unit]
Description=AtlasVM Virtualization Manager
After=network-online.target libvirtd.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/atlasvm
EnvironmentFile=/etc/atlasvm/atlasvm.env
ExecStart=/opt/atlasvm/.venv/bin/uvicorn app.main:app --host ${ATLASVM_HOST} --port ${ATLASVM_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable atlasvm

echo "AtlasVM installed. Edit $ENV_FILE, then run: systemctl start atlasvm"
