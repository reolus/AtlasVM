# AtlasVM Phase 6 Upgrade

## 1. Back up the existing install

```bash
cd /opt/atlasvm
systemctl stop atlasvm
cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)
```

## 2. Copy Phase 6 files

Unzip the Phase 6 package and copy it over `/opt/atlasvm`.

Example:

```bash
cd /tmp
unzip AtlasVM_phase6.zip -d AtlasVM_phase6
rsync -a AtlasVM_phase6/ /opt/atlasvm/
```

## 3. Install dependencies

```bash
apt install -y zfsutils-linux zstd
cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Initialize database and compile

```bash
cd /opt/atlasvm
source .venv/bin/activate
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py scripts/*.py
```

## 5. Start AtlasVM

```bash
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

## 6. Verify Phase 6 pages

```bash
curl -k -I https://127.0.0.1:8443/zfs
curl -k -I https://127.0.0.1:8443/backups
curl -k -I https://127.0.0.1:8443/tasks
```

A `401 Unauthorized` response is acceptable when curl is not passing credentials. A `404` means the Phase 6 files did not land in the running app.

## 7. Test from UI

- Open `/zfs`.
- Confirm pool health displays.
- Create a test snapshot.
- Queue a ZFS send export.
- Confirm the task appears in `/tasks`.
- Confirm the export appears in the ZFS send exports table.
- Open `/backups` and apply retention without overriding the configured keep count.

## Notes

ZFS send exports may be large. They are queued as background tasks so the browser does not wait for the entire export.
