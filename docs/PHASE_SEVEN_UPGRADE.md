# AtlasVM Phase 7 Upgrade

## Backup the current install

```bash
cd /opt/atlasvm
systemctl stop atlasvm
cp -a /opt/atlasvm /opt/atlasvm.backup.$(date +%Y%m%d-%H%M%S)
```

## Copy Phase 7 files

Unzip or copy the Phase 7 package over `/opt/atlasvm`.

## Refresh dependencies and database

```bash
cd /opt/atlasvm
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=/opt/atlasvm python scripts/init_db.py
python -m py_compile app/main.py app/core/*.py app/services/*.py scripts/*.py
systemctl start atlasvm
systemctl status atlasvm --no-pager
```

## Test routes

AtlasVM currently serves HTTP unless you placed a TLS proxy in front of it.

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8443/networks
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8443/networks/new
```

Expected values are `200` with credentials/session or `401` when unauthenticated.

## Validate network operations

1. Open `/networks`.
2. Create a NAT test network with a unique CIDR, such as `192.168.120.1/24`.
3. Confirm it appears active.
4. Open the network detail page.
5. Stop it, edit it, then start it again.
6. Delete it only after no VMs are attached.

## Host bridge warning

Phase 7 can define a bridge-backed libvirt network, but it does not create host Linux bridges. Configure bridges with Debian networking, NetworkManager, systemd-networkd, or your preferred host networking method before using them in AtlasVM.
