from pathlib import Path
from shutil import copyfileobj

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.models import VMCreate
from app.api.routes import router
from app.core.auth import require_user
from app.core.config import get_settings
from app.core.database import EventLog, TaskLog, get_db, init_db
from app.core.logging import log_event
from app.core.tasks import finish_task, start_task
from app.services.console_service import ConsoleService
from app.services.host_service import get_host_summary
from app.services.libvirt_service import LibvirtService, VMCreateRequest

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.include_router(router)
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')


@app.on_event('startup')
def startup() -> None:
    init_db()


def _lv_data() -> tuple[list[dict], list[dict], list[dict], list[dict], str | None]:
    vms: list[dict] = []
    pools: list[dict] = []
    networks: list[dict] = []
    isos: list[dict] = []
    error = None
    try:
        lv = LibvirtService()
        vms = lv.list_vms()
        pools = lv.list_storage_pools()
        networks = lv.list_networks()
        isos = lv.list_isos()
        lv.close()
    except Exception as exc:
        error = str(exc)
    return vms, pools, networks, isos, error


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    host = get_host_summary()
    vms, pools, networks, isos, error = _lv_data()
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(10).all()
    tasks = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(10).all()
    return templates.TemplateResponse('dashboard.html', {'request': request, 'app_name': settings.app_name, 'host': host, 'vms': vms, 'pools': pools, 'networks': networks, 'isos': isos, 'events': events, 'tasks': tasks, 'error': error})


@app.get('/vms/new', response_class=HTMLResponse)
def new_vm_form(request: Request, user: str = Depends(require_user)):
    _, pools, networks, isos, error = _lv_data()
    return templates.TemplateResponse('new_vm.html', {'request': request, 'app_name': settings.app_name, 'pools': pools, 'networks': networks, 'isos': isos, 'default_pool': settings.default_storage_pool, 'default_network': settings.default_network, 'error': error})


@app.post('/vms/new')
def create_vm_form(name: str = Form(...), memory_mb: int = Form(...), vcpus: int = Form(...), disk_gb: int = Form(...), storage_pool: str = Form(...), network: str = Form(...), iso_path: str = Form(''), description: str = Form(''), firmware: str = Form('bios'), start_after_create: bool = Form(False), autostart: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    task = start_task(db, user, 'create_vm', name)
    try:
        payload = VMCreate(name=name, memory_mb=memory_mb, vcpus=vcpus, disk_gb=disk_gb, storage_pool=storage_pool, network=network, iso_path=iso_path or None, description=description or None, firmware=firmware, start_after_create=start_after_create, autostart=autostart)
        lv.create_vm(VMCreateRequest(**payload.model_dump()))
        log_event(db, user, 'create_vm', name, 'Created VM from web form')
        finish_task(db, task, 'success', 'VM created')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        log_event(db, user, 'create_vm_failed', name, str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.get('/vms/{name}', response_class=HTMLResponse)
def vm_detail(name: str, request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    error = None
    try:
        vm = lv.get_vm(name)
    except Exception as exc:
        vm = None
        error = str(exc)
    finally:
        lv.close()
    return templates.TemplateResponse('vm_detail.html', {'request': request, 'app_name': settings.app_name, 'vm': vm, 'error': error})


@app.post('/ui/vms/{name}/{action}')
def vm_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
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
        elif action == 'delete':
            lv.delete_vm(name, delete_disks=False)
            finish_task(db, task, 'success', 'VM deleted')
            log_event(db, user, action, name, 'Deleted VM without disks')
            return RedirectResponse(url='/', status_code=303)
        elif action == 'delete-with-disks':
            lv.delete_vm(name, delete_disks=True)
            finish_task(db, task, 'success', 'VM and disks deleted')
            log_event(db, user, action, name, 'Deleted VM and disks')
            return RedirectResponse(url='/', status_code=303)
        else:
            raise ValueError(f'Unsupported action: {action}')
        log_event(db, user, action, name, f'UI action: {action}')
        finish_task(db, task, 'success', f'Action completed: {action}')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        log_event(db, user, f'{action}_failed', name, str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/console')
def vm_console_start(name: str, request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        display = lv.vnc_display(name)
        if not display:
            raise RuntimeError('VM does not expose a VNC console')
        host = request.url.hostname
        session = ConsoleService().start_novnc(name, display, request_host=host)
        log_event(db, user, 'start_console', name, session.url)
        return RedirectResponse(url=f'/vms/{name}/console?url={session.url}', status_code=303)
    finally:
        lv.close()


@app.get('/vms/{name}/console', response_class=HTMLResponse)
def vm_console_page(name: str, request: Request, url: str = '', user: str = Depends(require_user)):
    return templates.TemplateResponse('console.html', {'request': request, 'app_name': settings.app_name, 'name': name, 'console_url': url})


@app.post('/ui/vms/{name}/snapshots')
def create_snapshot_form(name: str, snapshot_name: str = Form(...), description: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    task = start_task(db, user, 'create_snapshot', name)
    try:
        lv.create_snapshot(name, snapshot_name, description or None)
        log_event(db, user, 'create_snapshot', name, snapshot_name)
        finish_task(db, task, 'success', f'Created snapshot {snapshot_name}')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/snapshots/{snapshot}/{action}')
def snapshot_action(name: str, snapshot: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    task = start_task(db, user, f'{action}_snapshot', name)
    try:
        if action == 'revert':
            lv.revert_snapshot(name, snapshot)
        elif action == 'delete':
            lv.delete_snapshot(name, snapshot)
        else:
            raise ValueError(f'Unsupported snapshot action: {action}')
        log_event(db, user, f'{action}_snapshot', name, snapshot)
        finish_task(db, task, 'success', f'{action} snapshot {snapshot}')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.get('/isos', response_class=HTMLResponse)
def iso_library(request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    error = None
    try:
        isos = lv.list_isos()
    except Exception as exc:
        isos = []
        error = str(exc)
    finally:
        lv.close()
    return templates.TemplateResponse('isos.html', {'request': request, 'app_name': settings.app_name, 'isos': isos, 'iso_path': settings.iso_path, 'error': error})


@app.post('/isos/upload')
def upload_iso(file: UploadFile = File(...), db: Session = Depends(get_db), user: str = Depends(require_user)):
    filename = Path(file.filename or '').name
    if not filename.lower().endswith(('.iso', '.img')):
        raise ValueError('Only .iso and .img files are allowed')
    Path(settings.iso_path).mkdir(parents=True, exist_ok=True)
    dest = Path(settings.iso_path) / filename
    with dest.open('wb') as fh:
        copyfileobj(file.file, fh)
    lv = LibvirtService()
    try:
        lv.refresh_storage_pool(settings.iso_pool)
    except Exception:
        pass
    finally:
        lv.close()
    log_event(db, user, 'upload_iso', filename, str(dest))
    return RedirectResponse(url='/isos', status_code=303)


@app.post('/isos/{filename}/delete')
def delete_iso(filename: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        lv.delete_iso(filename)
        log_event(db, user, 'delete_iso', filename, 'Deleted ISO')
    finally:
        lv.close()
    return RedirectResponse(url='/isos', status_code=303)


@app.get('/storage', response_class=HTMLResponse)
def storage_page(request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    error = None
    try:
        pools = lv.list_storage_pools()
    except Exception as exc:
        pools = []
        error = str(exc)
    finally:
        lv.close()
    return templates.TemplateResponse('storage.html', {'request': request, 'app_name': settings.app_name, 'pools': pools, 'error': error})


@app.get('/storage/{name}', response_class=HTMLResponse)
def storage_detail(name: str, request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    error = None
    try:
        pool = lv.get_storage_pool(name)
    except Exception as exc:
        pool = None
        error = str(exc)
    finally:
        lv.close()
    return templates.TemplateResponse('storage_detail.html', {'request': request, 'app_name': settings.app_name, 'pool': pool, 'error': error})


@app.post('/storage/{name}/refresh')
def storage_refresh(name: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        lv.refresh_storage_pool(name)
        log_event(db, user, 'refresh_storage_pool', name, 'Refreshed storage pool')
    finally:
        lv.close()
    return RedirectResponse(url=f'/storage/{name}', status_code=303)


@app.get('/networks', response_class=HTMLResponse)
def networks_page(request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    error = None
    try:
        networks = lv.list_networks()
    except Exception as exc:
        networks = []
        error = str(exc)
    finally:
        lv.close()
    return templates.TemplateResponse('networks.html', {'request': request, 'app_name': settings.app_name, 'networks': networks, 'error': error})


@app.post('/networks/{name}/{action}')
def networks_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        lv.network_action(name, action)
        log_event(db, user, f'network_{action}', name, 'Network action completed')
    finally:
        lv.close()
    return RedirectResponse(url='/networks', status_code=303)


@app.get('/events', response_class=HTMLResponse)
def events_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(250).all()
    return templates.TemplateResponse('events.html', {'request': request, 'app_name': settings.app_name, 'events': events})


@app.get('/tasks', response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    tasks = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(250).all()
    return templates.TemplateResponse('tasks.html', {'request': request, 'app_name': settings.app_name, 'tasks': tasks})
