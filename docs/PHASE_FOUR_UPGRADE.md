# AtlasVM Phase 4 Upgrade

1. Back up the current install.

```bash
cd /opt/atlasvm
systemctl stop atlasvm
cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)
```

2. Copy the Phase 4 files over `/opt/atlasvm`.

3. Reinstall requirements and initialize the database.

```bash
cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

4. Log in with the existing `.env` admin account. It is seeded into the local user table if no user exists.

5. Go to `/users` and create named admin/operator/viewer accounts.

6. Go to `/settings` to edit safe runtime settings. Restart AtlasVM after saving settings.
