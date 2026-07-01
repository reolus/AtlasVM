# AtlasVM Phase 3 Upgrade

From an existing AtlasVM install:

```bash
cd /opt/atlasvm
git pull origin main
apt update
apt install -y qemu-utils novnc websockify zstd
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
systemctl restart atlasvm
```

Recommended `.env` additions:

```env
ATLASVM_CONSOLE_BIND_HOST=0.0.0.0
ATLASVM_CONSOLE_PUBLIC_HOST=10.21.50.34
ATLASVM_CONSOLE_PORT_BASE=6080
ATLASVM_CONSOLE_PORT_MAX=6099
ATLASVM_BACKUP_COMPRESSION=zstd
ATLASVM_BACKUP_REQUIRE_SHUTDOWN=true
ATLASVM_BACKUP_KEEP_LAST=5
```

Run the doctor check:

```bash
cd /opt/atlasvm
source .venv/bin/activate
python scripts/atlasvm_doctor.py
```

Open the UI and check:

- `/doctor`
- `/zfs`
- `/backups`
- a VM detail page

If noVNC loads but does not connect, kill stale proxies and retry:

```bash
pkill -f websockify || true
rm -f /run/atlasvm/console-*.pid
systemctl restart atlasvm
```
