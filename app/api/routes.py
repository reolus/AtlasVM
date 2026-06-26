from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.models import VMCreate, DeleteVMOptions
from app.core.auth import require_user
from app.core.database import EventLog, get_db
from app.core.logging import log_event
from app.services.host_service import get_host_summary
from app.services.libvirt_service import LibvirtService, VMCreateRequest

router = APIRouter(prefix="/api/v1")


def libvirt_or_500() -> LibvirtService:
    try:
        return LibvirtService()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"libvirt connection failed: {exc}") from exc


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/host")
def host_summary(user: str = Depends(require_user)) -> dict:
    return get_host_summary()


@router.get("/storage-pools")
def storage_pools(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_storage_pools()
    finally:
        lv.close()


@router.get("/networks")
def networks(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_networks()
    finally:
        lv.close()


@router.get("/vms")
def list_vms(user: str = Depends(require_user)) -> list[dict]:
    lv = libvirt_or_500()
    try:
        return lv.list_vms()
    finally:
        lv.close()


@router.post("/vms")
def create_vm(payload: VMCreate, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        req = VMCreateRequest(**payload.model_dump())
        vm = lv.create_vm(req)
        log_event(db, user, "create_vm", payload.name, f"Created VM with {payload.vcpus} vCPU, {payload.memory_mb} MB RAM, {payload.disk_gb} GB disk")
        return vm
    except Exception as exc:
        log_event(db, user, "create_vm_failed", payload.name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get("/vms/{name}")
def get_vm(name: str, user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        return lv.get_vm(name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post("/vms/{name}/start")
def start_vm(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        lv.start_vm(name)
        log_event(db, user, "start_vm", name, "Started VM")
        return {"status": "ok", "action": "start", "name": name}
    except Exception as exc:
        log_event(db, user, "start_vm_failed", name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post("/vms/{name}/shutdown")
def shutdown_vm(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        lv.shutdown_vm(name)
        log_event(db, user, "shutdown_vm", name, "Requested graceful shutdown")
        return {"status": "ok", "action": "shutdown", "name": name}
    except Exception as exc:
        log_event(db, user, "shutdown_vm_failed", name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post("/vms/{name}/force-stop")
def force_stop_vm(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        lv.force_stop_vm(name)
        log_event(db, user, "force_stop_vm", name, "Forced VM power off")
        return {"status": "ok", "action": "force-stop", "name": name}
    except Exception as exc:
        log_event(db, user, "force_stop_vm_failed", name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.post("/vms/{name}/reboot")
def reboot_vm(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        lv.reboot_vm(name)
        log_event(db, user, "reboot_vm", name, "Requested reboot")
        return {"status": "ok", "action": "reboot", "name": name}
    except Exception as exc:
        log_event(db, user, "reboot_vm_failed", name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.delete("/vms/{name}")
def delete_vm(name: str, options: DeleteVMOptions = DeleteVMOptions(), db: Session = Depends(get_db), user: str = Depends(require_user)) -> dict:
    lv = libvirt_or_500()
    try:
        lv.delete_vm(name, delete_disks=options.delete_disks)
        log_event(db, user, "delete_vm", name, f"Deleted VM. delete_disks={options.delete_disks}")
        return {"status": "ok", "action": "delete", "name": name, "delete_disks": options.delete_disks}
    except Exception as exc:
        log_event(db, user, "delete_vm_failed", name, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        lv.close()


@router.get("/events")
def events(db: Session = Depends(get_db), user: str = Depends(require_user)) -> list[dict]:
    rows = db.query(EventLog).order_by(EventLog.id.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "actor": row.actor,
            "action": row.action,
            "target": row.target,
            "message": row.message,
        }
        for row in rows
    ]
