# AtlasVM Phase 4 Upgrade

From `/opt/atlasvm`:

```bash
systemctl stop atlasvm
cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)
# copy or unzip the Phase 4 files over /opt/atlasvm
cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

## First login

Phase 4 seeds the local user database from your existing environment credentials:

- `ATLASVM_USERNAME`
- `ATLASVM_PASSWORD`

After login, visit `/users` to create named accounts.

## Logo

The AtlasVM logo is installed at:

```text
app/static/atlasvm-logo.png
```

The base template references it automatically, so future UI pages inherit the branding.
```
