from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.models import VMCreate
from app.api.routes import router
from app.core.auth import require_user
from app.core.config import get_settings
from app.core.database import EventLog, get_db, init_db
from app.core.logging import log_event
from app.services.host_service import get_host_summary
from app.services.libvirt_service import LibvirtService, VMCreateRequest

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.include_router(router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    host = get_host_summary()
    vms = []
    pools = []
    networks = []
    error = None
    try:
        lv = LibvirtService()
        vms = lv.list_vms()
        pools = lv.list_storage_pools()
        networks = lv.list_networks()
        lv.close()
    except Exception as exc:
        error = str(exc)
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(20).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "host": host,
            "vms": vms,
            "pools": pools,
            "networks": networks,
            "events": events,
            "error": error,
        },
    )


@app.get("/vms/new", response_class=HTMLResponse)
def new_vm_form(request: Request, user: str = Depends(require_user)):
    pools = []
    networks = []
    error = None
    try:
        lv = LibvirtService()
        pools = lv.list_storage_pools()
        networks = lv.list_networks()
        lv.close()
    except Exception as exc:
        error = str(exc)
    return templates.TemplateResponse(
        "new_vm.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "pools": pools,
            "networks": networks,
            "default_pool": settings.default_storage_pool,
            "default_network": settings.default_network,
            "error": error,
        },
    )


@app.post("/vms/new")
def create_vm_form(
    name: str = Form(...),
    memory_mb: int = Form(...),
    vcpus: int = Form(...),
    disk_gb: int = Form(...),
    storage_pool: str = Form(...),
    network: str = Form(...),
    iso_path: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    lv = LibvirtService()
    try:
        payload = VMCreate(
            name=name,
            memory_mb=memory_mb,
            vcpus=vcpus,
            disk_gb=disk_gb,
            storage_pool=storage_pool,
            network=network,
            iso_path=iso_path or None,
        )
        lv.create_vm(VMCreateRequest(**payload.model_dump()))
        log_event(db, user, "create_vm", name, "Created VM from web form")
    finally:
        lv.close()
    return RedirectResponse(url="/", status_code=303)


@app.post("/ui/vms/{name}/{action}")
def vm_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        if action == "start":
            lv.start_vm(name)
        elif action == "shutdown":
            lv.shutdown_vm(name)
        elif action == "force-stop":
            lv.force_stop_vm(name)
        elif action == "reboot":
            lv.reboot_vm(name)
        elif action == "delete":
            lv.delete_vm(name, delete_disks=False)
        else:
            raise ValueError(f"Unsupported action: {action}")
        log_event(db, user, action, name, f"UI action: {action}")
    finally:
        lv.close()
    return RedirectResponse(url="/", status_code=303)
