from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.models import DeleteVMOptions, SnapshotCreate, VMBackupRequest, VMCloneRequest, VMCreate, VMEdit
from app.core.auth import require_user
from app.core.database import EventLog, TaskLog, get_db
from app.core.logging import log_event
from app.core.tasks import finish_task, start_task
from app.services.console_service import ConsoleService
from app.services.host_service import get_host_summary
from app.services.backup_service import BackupService
from app.services.doctor_service import run_doctor
from app.services import zfs_service
from app.services.libvirt_service import LibvirtService, VMCreateRequest

router = APIRouter(prefix='/api/v1')


def libvirt_or_500() -> LibvirtService:
    try:
        return LibvirtService()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'libvirt connection failed: {exc}') from exc


@router.get('/health')
def health() -> dict:
    return {'status': 'ok', 'product': 'AtlasVM', 'phase': 3}


@router.get('/host')
def host_summary(user: str = Depends(require_user)) -> dict:
    return get_host_summary()


@router.get('/doctor')
def doctor(user: str = Depends(require_user)) -> list[dict]:
    return run_doctor()


@router.get('/zfs')
def zfs(user: str = Depends(require_user)) -> dict:
    return {'pools': zfs_service.pool_status(), 'datasets': zfs_service.datasets(), 'snapshots': zfs_service.snapshots()}


@router.get('/backups')
def backups(user: str = Depends(require_user)) -> list[dict]:
    return BackupService().list_backups()


@router.get('/storage-pools')
def storage_pools(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_storage_pools()
    finally:
        lv.close()


@router.get('/storage-pools/{name}')
def storage_pool(name: str, user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        return lv.get_storage_pool(name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post('/storage-pools/{name}/refresh')
def refresh_pool(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'refresh_storage_pool', name)
    try:
        lv.refresh_storage_pool(name)
        log_event(db, user, 'refresh_storage_pool', name, 'Refreshed storage pool')
        finish_task(db, task, 'success', 'Storage pool refreshed')
        return {'status': 'ok'}
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get('/isos')
def isos(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_isos()
    finally:
        lv.close()


@router.get('/networks')
def networks(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_networks()
    finally:
        lv.close()


@router.post('/networks/{name}/{action}')
def network_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, f'network_{action}', name)
    try:
        lv.network_action(name, action)
        log_event(db, user, f'network_{action}', name, 'Network action completed')
        finish_task(db, task, 'success', 'Network action completed')
        return {'status': 'ok'}
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get('/vms')
def list_vms(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_vms()
    finally:
        lv.close()


@router.post('/vms')
def create_vm(payload: VMCreate, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'create_vm', payload.name)
    try:
        vm = lv.create_vm(VMCreateRequest(**payload.model_dump()))
        log_event(db, user, 'create_vm', payload.name, f'Created VM with {payload.vcpus} vCPU, {payload.memory_mb} MB RAM, {payload.disk_gb} GB disk')
        finish_task(db, task, 'success', 'VM created')
        return vm
    except Exception as exc:
        log_event(db, user, 'create_vm_failed', payload.name, str(exc))
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get('/vms/{name}')
def get_vm(name: str, user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        return lv.get_vm(name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        lv.close()


@router.put('/vms/{name}')
def edit_vm(name: str, payload: VMEdit, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'edit_vm', name)
    try:
        vm = lv.update_vm_basic(name, payload.memory_mb, payload.vcpus, payload.description)
        log_event(db, user, 'edit_vm', name, 'Updated VM basics')
        finish_task(db, task, 'success', 'VM updated')
        return vm
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post('/vms/{name}/clone')
def clone_vm(name: str, payload: VMCloneRequest, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'clone_vm', name)
    try:
        vm = lv.clone_vm(name, payload.new_name, payload.storage_pool)
        log_event(db, user, 'clone_vm', name, f"Cloned to {payload.new_name}")
        finish_task(db, task, 'success', f"Cloned to {payload.new_name}")
        return vm
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post('/vms/{name}/backup')
def backup_vm(name: str, payload: VMBackupRequest = VMBackupRequest(), db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    task = start_task(db, user, 'backup_vm', name)
    try:
        result = BackupService().create_backup(name, payload.compress, payload.require_shutdown)
        log_event(db, user, 'backup_vm', name, result.archive_path or result.backup_dir)
        finish_task(db, task, 'success', result.archive_path or result.backup_dir)
        return result.__dict__
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/vms/{name}/{action}')
def vm_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, action, name)
    try:
        if action == 'start':
            lv.start_vm(name)
        elif action == 'shutdown':
            lv.shutdown_vm(name)
        elif action == 'force-stop':
            lv.force_stop_vm(name)
        elif action == 'reboot':
            lv.reboot_vm(name)
        elif action == 'autostart-on':
            lv.set_autostart(name, True)
        elif action == 'autostart-off':
            lv.set_autostart(name, False)
        else:
            raise ValueError(f'Unsupported action: {action}')
        log_event(db, user, action, name, f'Action completed: {action}')
        finish_task(db, task, 'success', f'Action completed: {action}')
        return {'status': 'ok', 'action': action, 'name': name}
    except Exception as exc:
        log_event(db, user, f'{action}_failed', name, str(exc))
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.delete('/vms/{name}')
def delete_vm(name: str, options: DeleteVMOptions = DeleteVMOptions(), db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'delete_vm', name)
    try:
        lv.delete_vm(name, delete_disks=options.delete_disks)
        log_event(db, user, 'delete_vm', name, f'Deleted VM. delete_disks={options.delete_disks}')
        finish_task(db, task, 'success', f'Deleted VM. delete_disks={options.delete_disks}')
        return {'status': 'ok', 'action': 'delete', 'name': name, 'delete_disks': options.delete_disks}
    except Exception as exc:
        log_event(db, user, 'delete_vm_failed', name, str(exc))
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post('/vms/{name}/snapshots')
def create_snapshot(name: str, payload: SnapshotCreate, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'create_snapshot', name)
    try:
        result = lv.create_snapshot(name, payload.name, payload.description)
        log_event(db, user, 'create_snapshot', name, payload.name)
        finish_task(db, task, 'success', f'Created snapshot {payload.name}')
        return result
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get('/vms/{name}/snapshots')
def list_snapshots(name: str, user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_snapshots(name)
    finally:
        lv.close()


@router.post('/vms/{name}/snapshots/{snapshot}/revert')
def revert_snapshot(name: str, snapshot: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'revert_snapshot', name)
    try:
        lv.revert_snapshot(name, snapshot)
        log_event(db, user, 'revert_snapshot', name, snapshot)
        finish_task(db, task, 'success', f'Reverted to snapshot {snapshot}')
        return {'status': 'ok'}
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.delete('/vms/{name}/snapshots/{snapshot}')
def delete_snapshot(name: str, snapshot: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    task = start_task(db, user, 'delete_snapshot', name)
    try:
        lv.delete_snapshot(name, snapshot)
        log_event(db, user, 'delete_snapshot', name, snapshot)
        finish_task(db, task, 'success', f'Deleted snapshot {snapshot}')
        return {'status': 'ok'}
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post('/vms/{name}/console')
def start_console(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        display = lv.vnc_display(name)
        if not display:
            raise ValueError('VM does not expose a VNC console')
        session = ConsoleService().start_novnc(name, display)
        log_event(db, user, 'start_console', name, session.url)
        return session.__dict__
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get('/events')
def events(db: Session = Depends(get_db), user: str = Depends(require_user)) -> list[dict]:
    rows = db.query(EventLog).order_by(EventLog.id.desc()).limit(100).all()
    return [{'id': r.id, 'created_at': r.created_at.isoformat(), 'actor': r.actor, 'action': r.action, 'target': r.target, 'message': r.message} for r in rows]


@router.get('/tasks')
def tasks(db: Session = Depends(get_db), user: str = Depends(require_user)) -> list[dict]:
    rows = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(100).all()
    return [{'id': r.id, 'created_at': r.created_at.isoformat(), 'finished_at': r.finished_at.isoformat() if r.finished_at else None, 'actor': r.actor, 'action': r.action, 'target': r.target, 'status': r.status, 'message': r.message} for r in rows]
