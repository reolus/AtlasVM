# Upgrade Existing Phase 1 Install to Phase 2

From your existing AtlasVM host:

```bash
cd /opt/atlasvm
git pull
apt update
apt install -y novnc websockify python3-libvirt
rm -rf .venv
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
```

Move environment config to `/etc/atlasvm/atlasvm.env` if desired:

```bash
mkdir -p /etc/atlasvm
cp /opt/atlasvm/.env /etc/atlasvm/atlasvm.env
chmod 600 /etc/atlasvm/atlasvm.env
```

Install the new service:

```bash
cp systemd/atlasvm.service /etc/systemd/system/atlasvm.service
systemctl daemon-reload
systemctl restart atlasvm
systemctl status atlasvm --no-pager
```

If you stay with `/opt/atlasvm/.env`, edit the service file and point `EnvironmentFile` back to that path.

## Required New Environment Values

```env
ATLASVM_ISO_POOL=atlasvm-iso
ATLASVM_TEMPLATE_PATH=/atlasvm-vmdata/templates
ATLASVM_BACKUP_PATH=/atlasvm-vmdata/backups
ATLASVM_CONSOLE_BIND_HOST=0.0.0.0
ATLASVM_CONSOLE_PUBLIC_HOST=
ATLASVM_CONSOLE_PORT_BASE=6080
ATLASVM_CONSOLE_PORT_MAX=6099
```
