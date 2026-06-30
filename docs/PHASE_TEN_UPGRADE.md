# AtlasVM Phase 10 Upgrade Notes

## Files added

- `app/services/vm_storage_utils.py`
- Updated `app/services/backup_service.py`
- Updated `app/services/doctor_service.py`
- Updated `app/templates/backups.html`

## Files changed

- `app/main.py`
- `app/services/libvirt_service.py`
- `app/templates/vm_detail.html`

## New UI behavior

- `/backups` now shows backup targets, retention, restore options, and backup metadata.
- VM detail backup form allows selecting a backup target.
- Restore as new VM can select storage pool, target network, and start-after-restore.

## New metadata file

```text
/opt/atlasvm/atlasvm_backup_targets.json
```

## Verify after upgrade

```bash
cd /opt/atlasvm
source .venv/bin/activate
python -m py_compile app/main.py app/services/*.py
systemctl restart atlasvm
python scripts/atlasvm_doctor.py
```

Then open:

```text
https://<atlasvm-host>/backups
https://<atlasvm-host>/doctor
```

## Backup testing checklist

1. Back up a stopped VM using the default target.
2. Restore it as a new VM to `atlasvm-default`.
3. Back up a VM stored on LVM-thin.
4. Restore it as a new VM to `atlasvm-lvm-virtpool`.
5. Apply retention with keep-last set to a small number.
6. Confirm Doctor shows backup targets as writable.

## Notes

Running-VM backups are crash-consistent unless guest/application quiescing is added later. That is not a bug. That is reality, skulking around in a storage robe.
