# AtlasVM Phase 5 Upgrade

1. Stop AtlasVM and make a backup:

```bash
cd /opt/atlasvm
systemctl stop atlasvm
cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)
```

2. Copy the Phase 5 files over `/opt/atlasvm`.

3. Install/update dependencies and initialize the database:

```bash
cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py scripts/*.py
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

4. Verify routes:

```bash
curl -k -I https://127.0.0.1:8443/tasks
curl -k -I https://127.0.0.1:8443/backups
curl -k -I https://127.0.0.1:8443/templates
```

A `401 Unauthorized` is acceptable from curl. It means the route exists and auth is active.

5. Test in order:

- Queue a backup from a stopped VM.
- Restore that backup as a new VM.
- Convert a stopped VM to a template.
- Clone from the template.
- Open VM metrics.
