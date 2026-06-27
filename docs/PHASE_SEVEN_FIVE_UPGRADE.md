# AtlasVM Phase 7.5 Upgrade

```bash
cd /opt/atlasvm
systemctl stop atlasvm

cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)

# copy Phase 7.5 files over /opt/atlasvm

cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py scripts/*.py
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

Visit:

```text
http://SERVER-IP:8443/login
```

AtlasVM still supports HTTP Basic auth for scripts and curl.
