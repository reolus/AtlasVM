from pathlib import Path
from shutil import copyfileobj

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.models import VMCreate
from app.api.routes import router
from app.core.auth import SESSION_COOKIE, authenticate_username_password, create_session_token, get_user_role, require_admin, require_operator, require_user, require_viewer
from app.core.config import get_settings
from app.core.database import EventLog, TaskLog, UserAccount, get_db, init_db
from app.core.logging import log_event
from app.core.tasks import finish_task, start_task
from app.core.job_queue import enqueue_task
from app.core.security import hash_password
from app.services.console_service import ConsoleService
from app.services.host_service import get_host_summary
from app.services.backup_service import BackupService
from app.services.doctor_service import run_doctor
from app.services import zfs_service
from app.services.libvirt_service import LibvirtService, VMCreateRequest
from app.services.network_phase8 import NetworkPhase8Service
from app.services.dashboard_overview import dashboard_overview
from app.services.vm_inventory import list_vm_inventory
from app.services.vm_disk_management import (
    add_disk_to_vm,
    get_vm_disks,
    is_vm_running,
    list_storage_pools_for_disks,
    remove_disk_from_vm,
)

from app.services.storage_phase9 import (
    apply_storage_network,
    delete_storage_network,
    list_host_links as storage_host_links,
    reconcile_storage_networks,
    save_storage_network,
    storage_overview,
    apply_nfs_target,
    delete_nfs_target,
    list_nfs_targets,
    save_nfs_target,
    test_nfs_target,
    unmount_nfs_target,
    apply_smb_target,
    delete_smb_target,
    save_smb_target,
    test_smb_target,
    unmount_smb_target,
    delete_iscsi_target,
    iscsi_discover,
    login_iscsi_target,
    logout_iscsi_target,
    save_iscsi_target,
    test_iscsi_target,
    iscsi_lvm_candidate_devices,
    initialize_iscsi_lvm_thin,
)
from app.services.host_mgmt_network import (
    apply_plan,
    cancel_rollback,
    detect_stack,
    list_links,
    load_plan,
    rollback_now,
    routes as host_routes,
    save_plan,
)
from app.services.network_reconcile import reconcile_all

settings = get_settings()
app = FastAPI(title=settings.app_name)

settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=getattr(settings, 'session_secret', None) or settings.password,
    same_site='lax',
    https_only=False,
)
app.include_router(router)
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')



def _atlasvm_public_host(request: Request) -> str:
    """
    Return the host clients should use for browser-facing links.

    Prefer the current browser Host header so console links keep working after
    DHCP/static management IP changes, DNS aliases, reverse proxies, and other
    human networking choices.
    """
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""

    if host:
        return host

    # Fallback to saved management IP if available.
    try:
        import json
        from pathlib import Path

        plan_file = Path("/opt/atlasvm/atlasvm_host_network.json")
        if plan_file.exists():
            plan = json.loads(plan_file.read_text())
            static_ip = str(plan.get("static_ip") or "").strip()
            if static_ip:
                return static_ip
    except Exception:
        pass

    return "127.0.0.1"


def _atlasvm_public_scheme(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip()

    return request.url.scheme or "https"


def _atlasvm_console_base_url(request: Request) -> str:
    return f"{_atlasvm_public_scheme(request)}://{_atlasvm_public_host(request)}"


def _atlasvm_get_network_vlan_tag(network_name: str) -> str:
    """
    Best-effort VLAN lookup for AtlasVM-managed networks.
    Returns an empty string when no VLAN tag is found.
    """
    from pathlib import Path
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    network_name = (network_name or "").strip()
    if not network_name:
        return ""

    metadata_paths = [
        Path("/opt/atlasvm/atlasvm_networks.json"),
        Path("/opt/atlasvm/atlasvm_storage_networks.json"),
    ]

    for metadata_path in metadata_paths:
        try:
            if not metadata_path.exists():
                continue

            data = json.loads(metadata_path.read_text() or "{}")
            item = data.get(network_name) or {}

            for key in ("vlan_tag", "vlan", "tag"):
                value = item.get(key)
                if value not in (None, ""):
                    return str(value)
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["virsh", "net-dumpxml", network_name],
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode != 0:
            return ""

        root = ET.fromstring(result.stdout)

        tag = root.find("./vlan/tag")
        if tag is not None:
            value = tag.get("id")
            if value:
                return str(value)

        bridge = root.find("bridge")
        if bridge is not None:
            bridge_name = bridge.get("name", "")
            match = re.search(r"(\d{1,4})$", bridge_name)
            if match:
                return match.group(1)

    except Exception:
        return ""

    return ""


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


def _redirect(url: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    from urllib.parse import quote
    if message:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}message={quote(message)}"
    if error:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}error={quote(error)}"
    return RedirectResponse(url=url, status_code=303)


def _view_context(request: Request, user: str | None = None) -> dict:
    return {
        'request': request,
        'app_name': settings.app_name,
        'current_user': user,
        'current_role': get_user_role(user) if user else None,
        'message': request.query_params.get('message'),
        'error': request.query_params.get('error'),
    }






@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next_url: str = Query('/')):
    session_user = request.cookies.get(SESSION_COOKIE)
    # Do not trust the cookie here for authorization; require_user validates it on protected routes.
    return templates.TemplateResponse(
        'login.html',
        {
            'request': request,
            'app_name': settings.app_name,
            'next_url': next_url or '/',
            'message': request.query_params.get('message'),
            'error': request.query_params.get('error'),
        },
    )


@app.post('/login')
def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form('/'),
):
    from urllib.parse import quote

    username = username.strip()
    authenticated_user = authenticate_username_password(username, password)
    if not authenticated_user:
        return RedirectResponse(url=f'/login?error={quote("Invalid username or password")}', status_code=303)

    if not next_url or not next_url.startswith('/') or next_url.startswith('//'):
        next_url = '/'

    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(authenticated_user),
        httponly=True,
        samesite='lax',
        secure=False,
        max_age=60 * 60 * 12,
    )
    return response


@app.post('/logout')
def logout():
    response = RedirectResponse(url='/login?message=Signed out', status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


def _job_backup_vm(name: str, compress: bool, require_shutdown: bool, target_name: str | None = None) -> str:
    result = BackupService().create_backup(name, compress=compress, require_shutdown=require_shutdown, target_name=target_name)
    return result.archive_path or result.backup_dir


def _job_clone_vm(source_name: str, new_name: str, storage_pool: str | None = None) -> str:
    lv = LibvirtService()
    try:
        vm = lv.clone_vm(source_name, new_name, storage_pool)
        return f"Cloned {source_name} to {vm['name']}"
    finally:
        lv.close()


def _job_clone_template(template_name: str, new_name: str, storage_pool: str | None = None, start_after_create: bool = False) -> str:
    lv = LibvirtService()
    try:
        vm = lv.clone_template(template_name, new_name, storage_pool, start_after_create=start_after_create)
        return f"Cloned template {template_name} to {vm['name']}"
    finally:
        lv.close()


def _job_restore_backup_as_new(backup_dir: str, new_name: str, storage_pool: str | None = None, network_name: str | None = None, start_after_restore: bool = False) -> str:
    result = BackupService().restore_as_new_vm(backup_dir, new_name, storage_pool, network_name=network_name, start_after_restore=start_after_restore)
    return f"Restored backup as {result['name']} with disks: {result.get('disks', '')}"



def _job_zfs_send(snapshot: str, destination_dir: str | None = None, recursive: bool = False, compress: bool = True) -> str:
    result = zfs_service.send_snapshot(snapshot, destination_dir=destination_dir or None, recursive=recursive, compress=compress)
    return f"ZFS send export created: {result['path']}"


def _write_env_values(updates: dict[str, str]) -> None:
    env_path = Path('.env')
    existing_lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
    normalized = {f'ATLASVM_{k.upper()}': str(v) for k, v in updates.items()}
    found: set[str] = set()
    output: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            output.append(line)
            continue
        key = line.split('=', 1)[0].strip()
        if key in normalized:
            output.append(f'{key}={normalized[key]}')
            found.add(key)
        else:
            output.append(line)
    for key, value in normalized.items():
        if key not in found:
            output.append(f'{key}={value}')
    env_path.write_text('\n'.join(output).rstrip() + '\n', encoding='utf-8')


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    host = get_host_summary()
    vms, pools, networks, isos, error = _lv_data()
    zfs = zfs_service.pool_status()
    backups = BackupService().list_backups()[:5]
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(10).all()
    tasks = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(10).all()
    return templates.TemplateResponse('dashboard.html', {
            'dashboard': dashboard_overview(),'request': request, 'app_name': settings.app_name, 'host': host, 'vms': vms, 'pools': pools, 'networks': networks, 'isos': isos, 'zfs': zfs, 'backups': backups, 'events': events, 'tasks': tasks, 'error': error})


@app.get('/vms/new', response_class=HTMLResponse)
def new_vm_form(request: Request, user: str = Depends(require_user)):
    _, pools, networks, isos, error = _lv_data()
    return templates.TemplateResponse('new_vm.html', {'request': request, 'app_name': settings.app_name, 'pools': pools, 'networks': networks, 'isos': isos, 'default_pool': settings.default_storage_pool, 'default_network': settings.default_network, 'error': error,
            'user': user,
        })


@app.post('/vms/new')
def create_vm_form(name: str = Form(...), memory_mb: int = Form(...), vcpus: int = Form(...), disk_gb: int = Form(...), storage_pool: str = Form(...), network: str = Form(...), iso_path: str = Form(''), description: str = Form(''), firmware: str = Form('bios'), start_after_create: bool = Form(False), autostart: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_operator)):
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
def vm_detail(name: str, request: Request, error_msg: str | None = Query(None, alias='error'), success: str | None = Query(None), user: str = Depends(require_user)):
    lv = LibvirtService()
    error = error_msg
    current_iso = None
    try:
        vm = lv.get_vm(name)
        metrics = lv.vm_metrics(name)
        current_iso = lv.current_iso(name) if hasattr(lv, 'current_iso') else None
        isos = lv.list_isos()
        pools = lv.list_storage_pools()
    except Exception as exc:
        vm = None
        metrics = None
        isos = []
        pools = []
        error = str(exc)
    finally:
        lv.close()
    backup_service = BackupService()
    backups = backup_service.list_backups(name)
    backup_targets = backup_service.list_targets()
    return templates.TemplateResponse('vm_detail.html', {'request': request, 'app_name': settings.app_name, 'vm': vm, 'isos': isos, 'pools': pools, 'backups': backups, 'backup_targets': backup_targets, 'error': error, 'success': success, 'current_iso': current_iso, 'metrics': metrics,
            'user': user,
        })


@app.post('/ui/vms/{name}/{action}')
def vm_action(request: Request, name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    task = start_task(db, user, action, name)
    try:
        if action in {'delete', 'delete-with-disks'} and get_user_role(user) != 'admin':
            raise PermissionError('AtlasVM administrator rights are required for VM deletion')
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
        elif action == 'console':
            display = lv.vnc_display(name)
            if not display:
                raise ValueError(f'VM {name} does not have an active VNC display. Make sure it is running and has VNC graphics enabled.')
            host = request.headers.get('host', '').split(':')[0]
            session = ConsoleService().start_novnc(name, display, request_host=host)
            finish_task(db, task, 'success', f'Console opened: {session.url}')
            log_event(db, user, 'open_console', name, session.url)
            return RedirectResponse(url=session.url, status_code=303)
        elif action == 'delete-confirm':
            return RedirectResponse(url=f'/vms/{name}/delete-confirm', status_code=303)

        elif action == 'clone':
            return RedirectResponse(url=f'/vms/{name}/clone', status_code=303)

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


@app.post('/ui/vms/{name}/edit')
def vm_edit_basic(name: str, memory_mb: int = Form(...), vcpus: int = Form(...), description: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    task = start_task(db, user, 'edit_vm', name)
    try:
        lv.update_vm_basic(name, memory_mb, vcpus, description)
        log_event(db, user, 'edit_vm', name, f'Updated VM basics: {vcpus} vCPU, {memory_mb} MB')
        finish_task(db, task, 'success', 'VM updated')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        log_event(db, user, 'edit_vm_failed', name, str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/iso/attach')
def vm_attach_iso(name: str, iso_path: str = Form(...), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    task = start_task(db, user, 'attach_iso', name)
    try:
        lv.attach_iso(name, iso_path)
        log_event(db, user, 'attach_iso', name, iso_path)
        finish_task(db, task, 'success', 'ISO attached')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/iso/eject')
def vm_eject_iso(name: str, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    task = start_task(db, user, 'eject_iso', name)
    try:
        lv.eject_iso(name)
        log_event(db, user, 'eject_iso', name, 'Ejected CD-ROM media')
        finish_task(db, task, 'success', 'ISO ejected')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/disks/add')
def vm_add_disk(name: str, size_gb: int = Form(...), storage_pool: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    task = start_task(db, user, 'add_disk', name)
    try:
        disk = lv.add_disk(name, size_gb, storage_pool or None)
        log_event(db, user, 'add_disk', name, disk)
        finish_task(db, task, 'success', f'Added disk {disk}')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url=f'/vms/{name}', status_code=303)


@app.post('/ui/vms/{name}/clone')
def vm_clone(name: str, new_name: str = Form(...), storage_pool: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        task_id = enqueue_task(user, 'clone_vm', name, _job_clone_vm, name, new_name, storage_pool or None)
        log_event(db, user, 'queue_clone_vm', name, f'Queued clone to {new_name}; task={task_id}')
        return _redirect('/tasks', message=f'Clone queued as task {task_id}')
    except Exception as exc:
        return _redirect(f'/vms/{name}', error=str(exc))


@app.post('/ui/vms/{name}/backup')
def vm_backup(name: str, compress: bool = Form(False), require_shutdown: bool = Form(True), target_name: str = Form('default'), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        target = target_name or 'default'
        task_id = enqueue_task(user, 'backup_vm', name, _job_backup_vm, name, compress, require_shutdown, target)
        log_event(db, user, 'queue_backup_vm', name, f'Queued backup to {target}; task={task_id}')
        return RedirectResponse(url=f'/vms/{name}', status_code=303)
    except Exception as exc:
        log_event(db, user, 'queue_backup_vm_failed', name, str(exc))
        return RedirectResponse(url=f'/vms/{name}?error={exc}', status_code=303)



@app.post('/ui/vms/{name}/delete-confirm')
def vm_delete_confirm(name: str, confirm_name: str = Form(...), delete_disks: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_admin)):
    if confirm_name != name:
        raise ValueError('Confirmation name did not match VM name')
    lv = LibvirtService()
    task = start_task(db, user, 'delete_vm', name)
    try:
        lv.delete_vm(name, delete_disks=delete_disks)
        log_event(db, user, 'delete_vm', name, f'delete_disks={delete_disks}')
        finish_task(db, task, 'success', f'Deleted VM. delete_disks={delete_disks}')
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        raise
    finally:
        lv.close()
    return RedirectResponse(url='/', status_code=303)


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
    return templates.TemplateResponse('console.html', {'request': request,
            'public_host': _atlasvm_public_host(request),
            'public_scheme': _atlasvm_public_scheme(request),
            'console_base_url': _atlasvm_console_base_url(request), 'app_name': settings.app_name, 'name': name,
        'vlan_tag': _atlasvm_get_network_vlan_tag(name), 'console_url': url,
            'user': user,
        })


@app.post('/ui/vms/{name}/snapshots')
def create_snapshot_form(name: str, snapshot_name: str = Form(...), description: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
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
def snapshot_action(name: str, snapshot: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_operator)):
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



@app.get('/templates', response_class=HTMLResponse)
def templates_page(request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        templates_list = lv.list_templates()
        pools = lv.list_storage_pools()
        vms = lv.list_vms()
    finally:
        lv.close()
    context = _view_context(request, user)
    context.update({'templates': templates_list, 'pools': pools, 'vms': vms})
    return templates.TemplateResponse('templates.html', context)


@app.post('/ui/vms/{name}/template')
def vm_set_template(name: str, enabled: str = Form('true'), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    try:
        flag = str(enabled).lower() in {'true', '1', 'yes', 'on'}
        lv.set_template(name, flag)
        log_event(db, user, 'set_template', name, f'enabled={flag}')
        return _redirect(f'/vms/{name}', message=('VM marked as template' if flag else 'VM converted back to normal VM'))
    except Exception as exc:
        return _redirect(f'/vms/{name}', error=str(exc))
    finally:
        lv.close()


@app.post('/templates/{name}/clone')
def template_clone(name: str, new_name: str = Form(...), storage_pool: str = Form(''), start_after_create: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        task_id = enqueue_task(user, 'clone_template', name, _job_clone_template, name, new_name, storage_pool or None, start_after_create)
        log_event(db, user, 'queue_clone_template', name, f'Queued clone to {new_name}; task={task_id}')
        return _redirect('/tasks', message=f'Template clone queued as task {task_id}')
    except Exception as exc:
        return _redirect('/templates', error=str(exc))


@app.get('/vms/{name}/metrics', response_class=HTMLResponse)
def vm_metrics_page(name: str, request: Request, user: str = Depends(require_user)):
    lv = LibvirtService()
    try:
        metrics = lv.vm_metrics(name)
    finally:
        lv.close()
    context = _view_context(request, user)
    context.update({'metrics': metrics})
    return templates.TemplateResponse('vm_metrics.html', context)


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
    return templates.TemplateResponse('isos.html', {'request': request, 'app_name': settings.app_name, 'isos': isos, 'iso_path': settings.iso_path, 'error': error,
            'user': user,
        })


@app.post('/isos/upload')
def upload_iso(file: UploadFile = File(...), db: Session = Depends(get_db), user: str = Depends(require_operator)):
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
def delete_iso(filename: str, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    lv = LibvirtService()
    try:
        lv.delete_iso(filename)
        log_event(db, user, 'delete_iso', filename, 'Deleted ISO')
    finally:
        lv.close()
    return RedirectResponse(url='/isos', status_code=303)





@app.post('/networks/reconcile')
def networks_reconcile(user: str = Depends(require_admin)):
    try:
        reconcile_all()
        return _redirect('/networks', message='Network reconciliation completed.')
    except Exception as exc:
        return _redirect('/networks', error=str(exc))

@app.get('/networks', response_class=HTMLResponse)
def networks_page(request: Request, user: str = Depends(require_viewer)):
    svc = NetworkPhase8Service(settings.libvirt_uri)
    try:
        networks = svc.list_networks()
        return templates.TemplateResponse(
            'networks.html',
            {**_view_context(request, user), 'networks': networks},
        )
    except Exception as exc:
        return templates.TemplateResponse(
            'networks.html',
            {**_view_context(request, user), 'networks': [], 'error': str(exc)},
        )


@app.get('/networks/new', response_class=HTMLResponse)
def network_new_page(request: Request, user: str = Depends(require_admin)):
    svc = NetworkPhase8Service(settings.libvirt_uri)
    return templates.TemplateResponse(
        'network_form.html',
        {
            **_view_context(request, user),
            'network': None,
            'host_links': svc.list_host_links(),
            'action': '/networks/new',
            'mode': 'create',
        },
    )


@app.post('/networks/new')
def network_create(
    request: Request,
    name: str = Form(...),
    network_type: str = Form('nat'),
    parent_interface: str = Form(''),
    bridge_name: str = Form(''),
    vlan_tag: str = Form(''),
    cidr: str = Form(''),
    dhcp_start: str = Form(''),
    dhcp_end: str = Form(''),
    domain_name: str = Form(''),
    autostart: bool = Form(False),
    start_network: bool = Form(False),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    svc = NetworkPhase8Service(settings.libvirt_uri)
    clean_name = name.strip()

    try:
        svc.create_network(
            name=clean_name,
            network_type=network_type,
            parent_interface=parent_interface,
            bridge_name=bridge_name,
            vlan_tag=vlan_tag,
            cidr=cidr,
            dhcp_start=dhcp_start,
            dhcp_end=dhcp_end,
            domain_name=domain_name,
            autostart=autostart,
            start=start_network,
        )
        log_event(db, user, 'network_create', clean_name, f'Created {network_type} network')
        return _redirect(f'/networks/{clean_name}', message='Network created.')
    except Exception as exc:
        return _redirect('/networks/new', error=str(exc))


@app.get('/networks/{name}/edit', response_class=HTMLResponse)
def network_edit_page(name: str, request: Request, user: str = Depends(require_admin)):
    svc = NetworkPhase8Service(settings.libvirt_uri)

    try:
        network = svc.get_network(name)
        return templates.TemplateResponse(
            'network_form.html',
            {
                **_view_context(request, user),
                'network': network,
                'host_links': svc.list_host_links(),
                'action': f'/networks/{name}/edit',
                'mode': 'edit',
            },
        )
    except Exception as exc:
        return _redirect('/networks', error=str(exc))


@app.post('/networks/{name}/edit')
def network_edit_save(
    name: str,
    vlan_tag: str = Form(''),
    description: str = Form(''),
    parent_interface: str = Form(''),
    bridge_name: str = Form(''),
    autostart: bool = Form(False),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    svc = NetworkPhase8Service(settings.libvirt_uri)

    try:
        svc.update_network_meta(
            name=name,
            vlan_tag=vlan_tag,
            description=description,
            parent_interface=parent_interface,
            bridge_name=bridge_name,
        )

        try:
            svc.action(name, 'autostart-enable' if autostart else 'autostart-disable')
        except Exception:
            pass

        log_event(db, user, 'network_edit', name, 'Updated network metadata')
        return _redirect(f'/networks/{name}', message='Network settings saved.')
    except Exception as exc:
        return _redirect(f'/networks/{name}/edit', error=str(exc))


@app.get('/networks/{name}', response_class=HTMLResponse)
def network_detail_page(name: str, request: Request, user: str = Depends(require_viewer)):
    svc = NetworkPhase8Service(settings.libvirt_uri)

    try:
        network = svc.get_network(name)
        attached_vms = svc.attached_vms(name)
        return templates.TemplateResponse(
            'network_detail.html',
            {
                **_view_context(request, user),
                'network': network,
                'attached_vms': attached_vms,
            },
        )
    except Exception as exc:
        return _redirect('/networks', error=str(exc))


@app.post('/networks/{name}/delete')
def network_delete(name: str, db: Session = Depends(get_db), user: str = Depends(require_admin)):
    svc = NetworkPhase8Service(settings.libvirt_uri)

    try:
        svc.delete_network(name)
        log_event(db, user, 'network_delete', name, 'Deleted network')
        return _redirect('/networks', message=f'Network deleted: {name}')
    except Exception as exc:
        return _redirect(f'/networks/{name}', error=str(exc))


@app.post('/networks/{name}/{action}')
def networks_action(name: str, action: str, db: Session = Depends(get_db), user: str = Depends(require_admin)):
    svc = NetworkPhase8Service(settings.libvirt_uri)

    try:
        svc.action(name, action)
        log_event(db, user, f'network_{action}', name, 'Network action completed')
        return _redirect(f'/networks/{name}', message=f'Network action completed: {action}')
    except Exception as exc:
        return _redirect(f'/networks/{name}', error=str(exc))





@app.get('/host/network', response_class=HTMLResponse)
def host_network_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        'host_network.html',
        {
            **_view_context(request, user),
            'host_links': list_links(),
            'routes': host_routes(),
            'host_meta': load_plan(),
            'stack': detect_stack(),
        },
    )


@app.post('/host/network')
def host_network_save(
    management_interface: str = Form(''),
    vlan_tag: str = Form(''),
    static_ip: str = Form(''),
    subnet: str = Form(''),
    gateway: str = Form(''),
    dns_servers: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        save_plan(
            management_interface=management_interface,
            vlan_tag=vlan_tag,
            static_ip=static_ip,
            subnet=subnet,
            gateway=gateway,
            dns_servers=dns_servers,
        )
        return _redirect('/host/network', message='Management network plan saved.')
    except Exception as exc:
        return _redirect('/host/network', error=str(exc))


@app.post('/host/network/apply')
def host_network_apply(
    timeout_seconds: int = Form(120),
    user: str = Depends(require_admin),
):
    try:
        apply_plan(timeout_seconds=timeout_seconds)
        return _redirect('/host/network', message='Management network applied. Confirm within the rollback window or it will revert.')
    except Exception as exc:
        return _redirect('/host/network', error=str(exc))


@app.post('/host/network/confirm')
def host_network_confirm(user: str = Depends(require_admin)):
    try:
        cancel_rollback()
        return _redirect('/host/network', message='Management network confirmed. Rollback cancelled.')
    except Exception as exc:
        return _redirect('/host/network', error=str(exc))


@app.post('/host/network/rollback')
def host_network_rollback(user: str = Depends(require_admin)):
    try:
        rollback_now()
        return _redirect('/host/network', message='Rollback executed.')
    except Exception as exc:
        return _redirect('/host/network', error=str(exc))



@app.get('/storage', response_class=HTMLResponse)
def storage_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        'storage.html',
        {
            **_view_context(request, user),
            'overview': storage_overview(),
        },
    )


@app.get('/storage/networks/new', response_class=HTMLResponse)
def storage_network_new_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        'storage_network_form.html',
        {
            **_view_context(request, user),
            'host_links': storage_host_links(),
        },
    )


@app.post('/storage/networks/new')
def storage_network_new(
    name: str = Form(''),
    mode: str = Form(''),
    parent_interface: str = Form(''),
    vlan_tag: str = Form(''),
    ip_cidr: str = Form(''),
    gateway: str = Form(''),
    dns_servers: str = Form(''),
    mtu: str = Form(''),
    purpose: str = Form(''),
    notes: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        save_storage_network(
            name=name,
            mode=mode,
            parent_interface=parent_interface,
            vlan_tag=vlan_tag,
            ip_cidr=ip_cidr,
            gateway=gateway,
            dns_servers=dns_servers,
            mtu=mtu,
            purpose=purpose,
            notes=notes,
        )
        return _redirect('/storage', message='Storage network profile saved.')
    except Exception as exc:
        return _redirect('/storage/networks/new', error=str(exc))


@app.post('/storage/networks/{name}/apply')
def storage_network_apply(name: str, user: str = Depends(require_admin)):
    try:
        apply_storage_network(name)
        return _redirect('/storage', message=f'Storage network {name} applied.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/networks/{name}/delete')
def storage_network_delete(name: str, user: str = Depends(require_admin)):
    try:
        delete_storage_network(name)
        return _redirect('/storage', message=f'Storage network {name} deleted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/networks/reconcile')
def storage_network_reconcile(user: str = Depends(require_admin)):
    try:
        reconcile_storage_networks()
        return _redirect('/storage', message='Storage networks reconciled.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))



@app.get('/storage/nfs/new', response_class=HTMLResponse)
def nfs_new_page(request: Request, user: str = Depends(require_admin)):
    overview = storage_overview()
    return templates.TemplateResponse(
        'nfs_form.html',
        {
            **_view_context(request, user),
            'storage_networks': overview.get('storage_networks', {}),
        },
    )


@app.post('/storage/nfs/new')
def nfs_new(
    name: str = Form(''),
    storage_network: str = Form(''),
    server: str = Form(''),
    export_path: str = Form(''),
    mount_path: str = Form(''),
    nfs_version: str = Form('4'),
    mount_options: str = Form('rw,_netdev,noatime'),
    roles: str = Form(''),
    create_libvirt_pool: str = Form(''),
    libvirt_pool_name: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        save_nfs_target(
            name=name,
            storage_network=storage_network,
            server=server,
            export_path=export_path,
            mount_path=mount_path,
            nfs_version=nfs_version,
            mount_options=mount_options,
            roles=roles,
            create_libvirt_pool=create_libvirt_pool,
            libvirt_pool_name=libvirt_pool_name,
        )
        return _redirect('/storage', message='NFS target saved.')
    except Exception as exc:
        return _redirect('/storage/nfs/new', error=str(exc))


@app.post('/storage/nfs/{name}/test')
def nfs_test(name: str, user: str = Depends(require_admin)):
    try:
        result = test_nfs_target(name)
        if result.get('showmount_returncode') == 0:
            return _redirect('/storage', message=f'NFS target {name} responded to showmount.')
        return _redirect('/storage', error=f'NFS test returned error: {result.get("showmount_stderr") or result.get("showmount_stdout")}')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/nfs/{name}/apply')
def nfs_apply(name: str, user: str = Depends(require_admin)):
    try:
        apply_nfs_target(name)
        return _redirect('/storage', message=f'NFS target {name} applied.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/nfs/{name}/unmount')
def nfs_unmount(name: str, user: str = Depends(require_admin)):
    try:
        unmount_nfs_target(name)
        return _redirect('/storage', message=f'NFS target {name} unmounted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/nfs/{name}/delete')
def nfs_delete(name: str, user: str = Depends(require_admin)):
    try:
        delete_nfs_target(name)
        return _redirect('/storage', message=f'NFS target {name} deleted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))



@app.get('/storage/smb/new', response_class=HTMLResponse)
def smb_new_page(request: Request, user: str = Depends(require_admin)):
    overview = storage_overview()
    return templates.TemplateResponse(
        'smb_form.html',
        {
            **_view_context(request, user),
            'storage_networks': overview.get('storage_networks', {}),
        },
    )


@app.post('/storage/smb/new')
def smb_new(
    name: str = Form(''),
    storage_network: str = Form(''),
    server: str = Form(''),
    share_name: str = Form(''),
    mount_path: str = Form(''),
    username: str = Form(''),
    password: str = Form(''),
    domain: str = Form(''),
    smb_version: str = Form('3.1.1'),
    mount_options: str = Form('rw,_netdev,noserverino,iocharset=utf8'),
    roles: str = Form(''),
    create_libvirt_pool: str = Form(''),
    libvirt_pool_name: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        save_smb_target(
            name=name,
            storage_network=storage_network,
            server=server,
            share_name=share_name,
            mount_path=mount_path,
            username=username,
            password=password,
            domain=domain,
            smb_version=smb_version,
            mount_options=mount_options,
            roles=roles,
            create_libvirt_pool=create_libvirt_pool,
            libvirt_pool_name=libvirt_pool_name,
        )
        return _redirect('/storage', message='SMB/CIFS target saved.')
    except Exception as exc:
        return _redirect('/storage/smb/new', error=str(exc))


@app.post('/storage/smb/{name}/test')
def smb_test(name: str, user: str = Depends(require_admin)):
    try:
        result = test_smb_target(name)

        if not result.get('test_available'):
            return _redirect('/storage', message=result.get('message', 'SMB test skipped.'))

        if result.get('returncode') == 0:
            return _redirect('/storage', message=f'SMB target {name} responded.')

        return _redirect('/storage', error=f'SMB test returned error: {result.get("stderr") or result.get("stdout")}')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/smb/{name}/apply')
def smb_apply(name: str, user: str = Depends(require_admin)):
    try:
        apply_smb_target(name)
        return _redirect('/storage', message=f'SMB/CIFS target {name} applied.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/smb/{name}/unmount')
def smb_unmount(name: str, user: str = Depends(require_admin)):
    try:
        unmount_smb_target(name)
        return _redirect('/storage', message=f'SMB/CIFS target {name} unmounted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/smb/{name}/delete')
def smb_delete(name: str, user: str = Depends(require_admin)):
    try:
        delete_smb_target(name)
        return _redirect('/storage', message=f'SMB/CIFS target {name} deleted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))



@app.get('/storage/iscsi/new', response_class=HTMLResponse)
def iscsi_new_page(request: Request, portal: str = '', user: str = Depends(require_admin)):
    overview = storage_overview()
    discovery = None
    if portal:
        try:
            discovery = iscsi_discover(portal)
        except Exception as exc:
            discovery = {'error': str(exc), 'portal': portal}

    return templates.TemplateResponse(
        'iscsi_form.html',
        {
            **_view_context(request, user),
            'storage_networks': overview.get('storage_networks', {}),
            'portal': portal,
            'discovery': discovery,
        },
    )


@app.post('/storage/iscsi/discover')
def iscsi_discover_post(portal: str = Form(''), user: str = Depends(require_admin)):
    if not portal:
        return _redirect('/storage/iscsi/new', error='Portal is required.')
    return RedirectResponse(url=f'/storage/iscsi/new?portal={portal}', status_code=303)


@app.post('/storage/iscsi/new')
def iscsi_new(
    name: str = Form(''),
    storage_network: str = Form(''),
    portal: str = Form(''),
    target_iqn: str = Form(''),
    username: str = Form(''),
    password: str = Form(''),
    mutual_username: str = Form(''),
    mutual_password: str = Form(''),
    roles: str = Form(''),
    create_libvirt_pool: str = Form(''),
    libvirt_pool_name: str = Form(''),
    notes: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        save_iscsi_target(
            name=name,
            storage_network=storage_network,
            portal=portal,
            target_iqn=target_iqn,
            username=username,
            password=password,
            mutual_username=mutual_username,
            mutual_password=mutual_password,
            roles=roles,
            create_libvirt_pool=create_libvirt_pool,
            libvirt_pool_name=libvirt_pool_name,
            notes=notes,
        )
        return _redirect('/storage', message='iSCSI target saved.')
    except Exception as exc:
        return _redirect('/storage/iscsi/new', error=str(exc))


@app.post('/storage/iscsi/{name}/test')
def iscsi_test(name: str, user: str = Depends(require_admin)):
    try:
        result = test_iscsi_target(name)
        if result.get('found'):
            return _redirect('/storage', message=f'iSCSI target {name} found during discovery.')
        return _redirect('/storage', error=f'iSCSI target {name} was not found during discovery.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/iscsi/{name}/login')
def iscsi_login(name: str, user: str = Depends(require_admin)):
    try:
        login_iscsi_target(name)
        return _redirect('/storage', message=f'iSCSI target {name} login attempted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/iscsi/{name}/logout')
def iscsi_logout(name: str, user: str = Depends(require_admin)):
    try:
        logout_iscsi_target(name)
        return _redirect('/storage', message=f'iSCSI target {name} logout attempted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/iscsi/{name}/delete')
def iscsi_delete(name: str, user: str = Depends(require_admin)):
    try:
        delete_iscsi_target(name)
        return _redirect('/storage', message=f'iSCSI target {name} deleted.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))



@app.get('/storage/iscsi/{name}/lvm-thin', response_class=HTMLResponse)
def iscsi_lvm_thin_page(name: str, request: Request, user: str = Depends(require_admin)):
    try:
        candidates = iscsi_lvm_candidate_devices(name)
        targets = storage_overview().get('iscsi_targets', {})
        target = targets.get(name, {})
        return templates.TemplateResponse(
            'iscsi_lvm_form.html',
            {
                **_view_context(request, user),
                'name': name,
                'target': target,
                'candidates': candidates,
            },
        )
    except Exception as exc:
        return _redirect('/storage', error=str(exc))


@app.post('/storage/iscsi/{name}/lvm-thin')
def iscsi_lvm_thin_apply(
    name: str,
    by_path: str = Form(''),
    vg_name: str = Form(''),
    thinpool_name: str = Form(''),
    thinpool_percent: str = Form('95'),
    create_libvirt_pool: str = Form(''),
    libvirt_pool_name: str = Form(''),
    confirm_text: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        initialize_iscsi_lvm_thin(
            name=name,
            by_path=by_path,
            vg_name=vg_name,
            thinpool_name=thinpool_name,
            thinpool_percent=thinpool_percent,
            create_libvirt_pool=create_libvirt_pool,
            libvirt_pool_name=libvirt_pool_name,
            confirm_text=confirm_text,
        )
        return _redirect('/storage', message=f'iSCSI target {name} initialized as LVM-thin.')
    except Exception as exc:
        return _redirect('/storage', error=str(exc))



@app.get('/vms', response_class=HTMLResponse)
def vms_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        'vms.html',
        {
            **_view_context(request, user),
            'inventory': list_vm_inventory(),
        },
    )



@app.get('/vms/{vm_name}/disks', response_class=HTMLResponse)
def vm_disks_page(vm_name: str, request: Request, user: str = Depends(require_admin)):
    try:
        return templates.TemplateResponse(
            'vm_disks.html',
            {
                **_view_context(request, user),
                'vm_name': vm_name,
                'running': is_vm_running(vm_name),
                'disks': get_vm_disks(vm_name),
                'pools': list_storage_pools_for_disks(),
            },
        )
    except Exception as exc:
        return _redirect('/vms', error=str(exc))


@app.post('/vms/{vm_name}/disks/add')
def vm_disk_add(
    vm_name: str,
    pool_name: str = Form(''),
    disk_name: str = Form(''),
    size_gb: int = Form(0),
    fmt: str = Form('qcow2'),
    user: str = Depends(require_admin),
):
    try:
        result = add_disk_to_vm(
            vm_name=vm_name,
            pool_name=pool_name,
            disk_name=disk_name,
            size_gb=size_gb,
            fmt=fmt,
        )
        target = result.get('attach', {}).get('target', '')
        mode = result.get('attach', {}).get('attach_mode', '')
        return _redirect(f'/vms/{vm_name}/disks', message=f'Disk added as {target} using {mode}.')
    except Exception as exc:
        return _redirect(f'/vms/{vm_name}/disks', error=str(exc))


@app.post('/vms/{vm_name}/disks/{target_dev}/remove')
def vm_disk_remove(
    vm_name: str,
    target_dev: str,
    delete_storage: str = Form(''),
    user: str = Depends(require_admin),
):
    try:
        should_delete = str(delete_storage or '').lower() in {'1', 'true', 'yes', 'on'}
        result = remove_disk_from_vm(
            vm_name=vm_name,
            target_dev=target_dev,
            delete_storage=should_delete,
        )
        delete_msg = result.get('delete', {}).get('message', '')
        return _redirect(f'/vms/{vm_name}/disks', message=f'Disk {target_dev} removed. {delete_msg}')
    except Exception as exc:
        return _redirect(f'/vms/{vm_name}/disks', error=str(exc))


@app.get('/events', response_class=HTMLResponse)
def events_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(250).all()
    return templates.TemplateResponse('events.html', {'request': request, 'app_name': settings.app_name, 'events': events,
            'user': user,
        })


@app.get('/tasks', response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    tasks = db.query(TaskLog).order_by(TaskLog.id.desc()).limit(250).all()
    return templates.TemplateResponse('tasks.html', {'request': request, 'app_name': settings.app_name, 'tasks': tasks,
            'user': user,
        })


@app.get('/backups', response_class=HTMLResponse)
def backups_page(request: Request, user: str = Depends(require_user)):
    svc = BackupService()
    backups = svc.list_backups()
    targets = svc.list_targets()
    try:
        pools = list_storage_pools_for_disks()
    except Exception:
        pools = []
    try:
        _, _, networks, _, _ = _lv_data()
    except Exception:
        networks = []
    return templates.TemplateResponse(
        'backups.html',
        {
            **_view_context(request, user),
            'backups': backups,
            'backup_path': settings.backup_path,
            'retention': svc.retention_policy(),
            'targets': targets,
            'pools': pools,
            'networks': networks,
        },
    )


@app.post('/backups/targets')
def backups_save_target(
    name: str = Form(...),
    path: str = Form(...),
    kind: str = Form('custom'),
    label: str = Form(''),
    enabled: bool = Form(True),
    db: Session = Depends(get_db),
    user: str = Depends(require_operator),
):
    try:
        BackupService().save_target(name=name, path=path, kind=kind, label=label, enabled=enabled)
        log_event(db, user, 'backup_target_save', name, path)
        return _redirect('/backups', message=f'Saved backup target {name}')
    except Exception as exc:
        log_event(db, user, 'backup_target_save_failed', name, str(exc))
        return _redirect('/backups', error=str(exc))


@app.post('/backups/targets/delete')
def backups_delete_target(
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_operator),
):
    try:
        BackupService().delete_target(name)
        log_event(db, user, 'backup_target_delete', name, 'Deleted backup target metadata')
        return _redirect('/backups', message=f'Deleted backup target {name}')
    except Exception as exc:
        log_event(db, user, 'backup_target_delete_failed', name, str(exc))
        return _redirect('/backups', error=str(exc))


@app.post('/backups/restore-definition')
def restore_backup_definition(backup_dir: str = Form(...), new_name: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    task = start_task(db, user, 'restore_definition', backup_dir)
    try:
        result = BackupService().restore_definition(backup_dir, new_name or None)
        log_event(db, user, 'restore_definition', result['name'], backup_dir)
        finish_task(db, task, 'success', f"Restored definition as {result['name']}")
        return RedirectResponse(url=f"/vms/{result['name']}", status_code=303)
    except Exception as exc:
        finish_task(db, task, 'failed', str(exc))
        return _redirect('/backups', error=str(exc))


@app.post('/backups/restore-as-new')
def restore_backup_as_new(
    backup_dir: str = Form(...),
    new_name: str = Form(...),
    storage_pool: str = Form(''),
    network_name: str = Form(''),
    start_after_restore: bool = Form(False),
    db: Session = Depends(get_db),
    user: str = Depends(require_operator),
):
    try:
        task_id = enqueue_task(
            user,
            'restore_backup_as_new',
            backup_dir,
            _job_restore_backup_as_new,
            backup_dir,
            new_name,
            storage_pool or None,
            network_name or None,
            start_after_restore,
        )
        log_event(db, user, 'queue_restore_backup_as_new', new_name, f'Queued restore from {backup_dir}; task={task_id}')
        return _redirect('/tasks', message=f'Restore queued as task {task_id}')
    except Exception as exc:
        return _redirect('/backups', error=str(exc))



@app.get('/zfs', response_class=HTMLResponse)
def zfs_page(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse('zfs.html', {
            'request': request,
            'app_name': settings.app_name,
            'zfs': zfs_service.pool_status(),
            'datasets': zfs_service.datasets(),
            'snapshots': zfs_service.snapshots(),
            'exports': zfs_service.exports(),
            'backup_path': settings.backup_path,
            'message': request.query_params.get('message'),
            'error': request.query_params.get('error'),
        },
    )


@app.post('/zfs/pools/{pool}/scrub')
def zfs_scrub(pool: str, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    result = zfs_service.scrub(pool)
    log_event(db, user, 'zfs_scrub', pool, str(result))
    return RedirectResponse(url='/zfs', status_code=303)


@app.post('/zfs/snapshots')
def zfs_snapshot(dataset: str = Form(...), snapshot_name: str = Form(...), recursive: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        result = zfs_service.create_snapshot(dataset, snapshot_name, recursive=recursive)
        log_event(db, user, 'zfs_snapshot', dataset, result['snapshot'])
        return _redirect('/zfs', message=f"Created ZFS snapshot {result['snapshot']}")
    except Exception as exc:
        log_event(db, user, 'zfs_snapshot_failed', dataset, str(exc))
        return _redirect('/zfs', error=str(exc))

@app.post('/zfs/snapshots/delete')
def zfs_destroy_snapshot(snapshot: str = Form(...), recursive: bool = Form(False), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        result = zfs_service.destroy_snapshot(snapshot, recursive=recursive)
        log_event(db, user, 'zfs_destroy_snapshot', snapshot, str(result))
        return _redirect('/zfs', message=f'Destroyed ZFS snapshot {snapshot}')
    except Exception as exc:
        log_event(db, user, 'zfs_destroy_snapshot_failed', snapshot, str(exc))
        return _redirect('/zfs', error=str(exc))


@app.post('/zfs/snapshots/send')
def zfs_send_snapshot(snapshot: str = Form(...), destination_dir: str = Form(''), recursive: bool = Form(False), compress: bool = Form(True), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        task_id = enqueue_task(user, 'zfs_send', snapshot, _job_zfs_send, snapshot, destination_dir or None, recursive, compress)
        log_event(db, user, 'queue_zfs_send', snapshot, f'Queued ZFS send task {task_id}')
        return _redirect('/tasks', message=f'ZFS send queued as task {task_id}')
    except Exception as exc:
        return _redirect('/zfs', error=str(exc))


@app.post('/zfs/exports/delete')
def zfs_delete_export(path: str = Form(...), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        zfs_service.delete_export(path)
        log_event(db, user, 'zfs_delete_export', path, 'Deleted ZFS send export')
        return _redirect('/zfs', message='ZFS export deleted')
    except Exception as exc:
        log_event(db, user, 'zfs_delete_export_failed', path, str(exc))
        return _redirect('/zfs', error=str(exc))


@app.post('/backups/prune')
def backups_prune(vm_name: str = Form(''), keep_last: int | None = Form(None), target_name: str = Form(''), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        result = BackupService().prune_backups(vm_name.strip() or None, keep_last=keep_last, target_name=target_name or None)
        log_event(db, user, 'backup_prune', vm_name or 'all', str(result))
        return _redirect('/backups', message=f"Backup retention applied; deleted {result['deleted_count']} item(s)")
    except Exception as exc:
        log_event(db, user, 'backup_prune_failed', vm_name or 'all', str(exc))
        return _redirect('/backups', error=str(exc))


@app.get('/doctor', response_class=HTMLResponse)
def doctor_page(request: Request, user: str = Depends(require_user)):
    checks = run_doctor()
    return templates.TemplateResponse('doctor.html', {'request': request, 'app_name': settings.app_name, 'checks': checks,
            'user': user,
        })


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request, user: str = Depends(require_admin)):
    cfg = get_settings()
    editable = [
        ('app_name', cfg.app_name),
        ('default_storage_pool', cfg.default_storage_pool),
        ('iso_pool', cfg.iso_pool),
        ('default_network', cfg.default_network),
        ('iso_path', cfg.iso_path),
        ('template_path', cfg.template_path),
        ('backup_path', cfg.backup_path),
        ('console_public_host', cfg.console_public_host),
        ('console_port_base', cfg.console_port_base),
        ('console_port_max', cfg.console_port_max),
        ('backup_require_shutdown', str(cfg.backup_require_shutdown).lower()),
        ('backup_keep_last', cfg.backup_keep_last),
    ]
    readonly = [
        ('host', cfg.host),
        ('port', cfg.port),
        ('libvirt_uri', cfg.libvirt_uri),
        ('vm_disk_path', cfg.vm_disk_path),
        ('database_url', cfg.database_url),
    ]
    context = _view_context(request, user)
    context.update({'settings_items': editable, 'readonly_items': readonly, 'restart_required': request.query_params.get('restart_required')})
    return templates.TemplateResponse('settings.html', context)


@app.post('/settings')
def settings_update(
    request: Request,
    app_name: str = Form(...),
    default_storage_pool: str = Form(...),
    iso_pool: str = Form(...),
    default_network: str = Form(...),
    iso_path: str = Form(...),
    template_path: str = Form(...),
    backup_path: str = Form(...),
    console_public_host: str = Form(''),
    console_port_base: int = Form(...),
    console_port_max: int = Form(...),
    backup_require_shutdown: str = Form('false'),
    backup_keep_last: int = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    try:
        updates = {
            'app_name': app_name.strip() or 'AtlasVM',
            'default_storage_pool': default_storage_pool.strip(),
            'iso_pool': iso_pool.strip(),
            'default_network': default_network.strip(),
            'iso_path': iso_path.strip(),
            'template_path': template_path.strip(),
            'backup_path': backup_path.strip(),
            'console_public_host': console_public_host.strip(),
            'console_port_base': str(console_port_base),
            'console_port_max': str(console_port_max),
            'backup_require_shutdown': 'true' if str(backup_require_shutdown).lower() in {'true','on','1','yes'} else 'false',
            'backup_keep_last': str(max(0, backup_keep_last)),
        }
        _write_env_values(updates)
        log_event(db, user, 'update_settings', 'platform', 'Updated editable AtlasVM settings in .env')
        return _redirect('/settings?restart_required=1', message='Settings saved to .env. Restart AtlasVM for all changes to take effect.')
    except Exception as exc:
        return _redirect('/settings', error=str(exc))


@app.get('/users', response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_admin)):
    users = db.query(UserAccount).order_by(UserAccount.username.asc()).all()
    context = _view_context(request, user)
    context.update({'users': users})
    return templates.TemplateResponse('users.html', context)


@app.post('/users/create')
def users_create(username: str = Form(...), password: str = Form(...), role: str = Form('operator'), db: Session = Depends(get_db), user: str = Depends(require_admin)):
    username = username.strip()
    role = role if role in {'admin', 'operator', 'viewer'} else 'operator'
    try:
        if not username:
            raise ValueError('Username is required')
        if len(password) < 10:
            raise ValueError('Password must be at least 10 characters')
        existing = db.query(UserAccount).filter(UserAccount.username == username).first()
        if existing:
            raise ValueError('A user with that username already exists')
        account = UserAccount(username=username, password_hash=hash_password(password), role=role, is_active=True)
        db.add(account)
        db.commit()
        log_event(db, user, 'create_user', username, f'role={role}')
        return _redirect('/users', message=f'User created: {username}')
    except Exception as exc:
        db.rollback()
        return _redirect('/users', error=str(exc))


@app.post('/users/{account_id}/role')
def users_change_role(account_id: int, role: str = Form(...), db: Session = Depends(get_db), user: str = Depends(require_admin)):
    account = db.query(UserAccount).filter(UserAccount.id == account_id).first()
    if not account:
        return _redirect('/users', error='User not found')
    if role not in {'admin', 'operator', 'viewer'}:
        return _redirect('/users', error='Invalid role')
    active_admin_count = db.query(UserAccount).filter(UserAccount.role == 'admin', UserAccount.is_active == True).count()
    if account.role == 'admin' and role != 'admin' and account.is_active and active_admin_count <= 1:
        return _redirect('/users', error='Cannot remove the last active administrator')
    old_role = account.role
    account.role = role
    db.commit()
    log_event(db, user, 'change_user_role', account.username, f'{old_role} -> {role}')
    return _redirect('/users', message=f'Role updated for {account.username}')


@app.post('/users/{account_id}/password')
def users_reset_password(account_id: int, password: str = Form(...), db: Session = Depends(get_db), user: str = Depends(require_admin)):
    account = db.query(UserAccount).filter(UserAccount.id == account_id).first()
    if not account:
        return _redirect('/users', error='User not found')
    if len(password) < 10:
        return _redirect('/users', error='Password must be at least 10 characters')
    account.password_hash = hash_password(password)
    db.commit()
    log_event(db, user, 'reset_user_password', account.username, 'Password reset')
    return _redirect('/users', message=f'Password reset for {account.username}')


@app.post('/users/{account_id}/toggle')
def users_toggle(account_id: int, db: Session = Depends(get_db), user: str = Depends(require_admin)):
    account = db.query(UserAccount).filter(UserAccount.id == account_id).first()
    if not account:
        return _redirect('/users', error='User not found')
    if account.username == user:
        return _redirect('/users', error='You cannot disable your own account while using it')
    active_admin_count = db.query(UserAccount).filter(UserAccount.role == 'admin', UserAccount.is_active == True).count()
    if account.role == 'admin' and account.is_active and active_admin_count <= 1:
        return _redirect('/users', error='Cannot disable the last active administrator')
    account.is_active = not account.is_active
    db.commit()
    log_event(db, user, 'toggle_user', account.username, f'is_active={account.is_active}')
    return _redirect('/users', message=f"{'Enabled' if account.is_active else 'Disabled'} {account.username}")


@app.post('/users/{account_id}/delete')
def users_delete(account_id: int, db: Session = Depends(get_db), user: str = Depends(require_admin)):
    account = db.query(UserAccount).filter(UserAccount.id == account_id).first()
    if not account:
        return _redirect('/users', error='User not found')
    if account.username == user:
        return _redirect('/users', error='You cannot delete the account currently holding the keyboard')
    active_admin_count = db.query(UserAccount).filter(UserAccount.role == 'admin', UserAccount.is_active == True).count()
    if account.role == 'admin' and account.is_active and active_admin_count <= 1:
        return _redirect('/users', error='Cannot delete the last active administrator')
    username = account.username
    db.delete(account)
    db.commit()
    log_event(db, user, 'delete_user', username, 'Deleted local AtlasVM user')
    return _redirect('/users', message=f'Deleted {username}')


@app.post('/backups/delete')
def backups_delete(backup_dir: str = Form(...), db: Session = Depends(get_db), user: str = Depends(require_operator)):
    try:
        BackupService().delete_backup(backup_dir)
        log_event(db, user, 'delete_backup', backup_dir, 'Deleted backup directory and archive')
        return _redirect('/backups', message='Backup deleted')
    except Exception as exc:
        return _redirect('/backups', error=str(exc))


@app.get('/logout')
def logout_get(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login?message=Logged out', status_code=303)

@app.get('/audit')
def audit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    from sqlalchemy import text

    settings = get_settings()
    events = []
    error = request.query_params.get('error')

    try:
        # Find likely audit table names. SQLite gets to be interrogated directly,
        # because guessing Python model names has betrayed us like a tiny ORM goblin.
        tables = db.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )).fetchall()

        table_names = [row[0] for row in tables]
        audit_table = None

        for candidate in ["event_log", "audit_logs", "audit_log", "audit", "events"]:
            if candidate in table_names:
                audit_table = candidate
                break

        if audit_table is None:
            error = "No audit table found. Existing tables: " + ", ".join(table_names)
        else:
            rows = db.execute(text(
                f"SELECT * FROM {audit_table} ORDER BY id DESC LIMIT 200"
            )).mappings().all()

            events = [dict(row) for row in rows]

    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(
        'audit.html',
        {
            'request': request,
            'app_name': settings.app_name,
            'events': events,
            'user': user,
            'error': error,
            'message': request.query_params.get('message'),
        },
    )

@app.get('/vms/{name}/delete-confirm')
def vm_delete_confirm(
    name: str,
    request: Request,
    user: str = Depends(require_admin),
):
    settings = get_settings()

    return templates.TemplateResponse(
        'vm_delete_confirm.html',
        {
            'request': request,
            'app_name': settings.app_name,
            'name': name,
        'vlan_tag': _atlasvm_get_network_vlan_tag(name),
            'user': user,
            'message': request.query_params.get('message'),
            'error': request.query_params.get('error'),
        },
    )

@app.post('/vms/{name}/delete')
def vm_delete(
    name: str,
    confirm_name: str = Form(...),
    delete_disks: bool = Form(False),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    from urllib.parse import quote

    if confirm_name.strip() != name:
        return RedirectResponse(
            url=f'/vms/{name}/delete-confirm?error={quote("Confirmation name did not match VM name.")}',
            status_code=303,
        )

    try:
        libvirt_service = LibvirtService()
        # Try the known libvirt service delete method names across AtlasVM phases.
        if hasattr(libvirt_service, 'delete_vm'):
            libvirt_service.delete_vm(name, delete_disks=delete_disks)
        elif hasattr(libvirt_service, 'delete_domain'):
            libvirt_service.delete_domain(name, remove_disks=delete_disks)
        elif hasattr(libvirt_service, 'undefine_vm'):
            libvirt_service.undefine_vm(name, delete_storage=delete_disks)
        else:
            raise RuntimeError('No VM delete method found on LibvirtService.')

        # Best-effort event logging. Do not let logging failure break deletion.
        try:
            if 'log_event' in globals():
                log_event(db, user, 'delete_vm', name, f'delete_disks={delete_disks}')
        except Exception:
            pass

        return RedirectResponse(
            url=f'/?message={quote("Deleted VM: " + name)}',
            status_code=303,
        )

    except Exception as exc:
        try:
            if 'log_event' in globals():
                log_event(db, user, 'delete_vm_failed', name, str(exc))
        except Exception:
            pass

        return RedirectResponse(
            url=f'/vms/{name}/delete-confirm?error={quote(str(exc))}',
            status_code=303,
        )

@app.get('/vms/{name}/clone')
def vm_clone_page(
    name: str,
    request: Request,
    user: str = Depends(require_operator),
):
    settings = get_settings()

    return templates.TemplateResponse(
        'vm_clone.html',
        {
            'request': request,
            'app_name': settings.app_name,
            'name': name,
        'vlan_tag': _atlasvm_get_network_vlan_tag(name),
            'user': user,
            'message': request.query_params.get('message'),
            'error': request.query_params.get('error'),
        },
    )


@app.post('/vms/{name}/clone')
def vm_clone_submit(
    name: str,
    new_name: str = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_operator),
):
    from urllib.parse import quote
    from app.services.libvirt_service import LibvirtService

    new_name = new_name.strip()

    if not new_name:
        return RedirectResponse(
            url=f'/vms/{name}/clone?error={quote("New VM name is required.")}',
            status_code=303,
        )

    try:
        libvirt_service = LibvirtService()

        if hasattr(libvirt_service, 'clone_vm'):
            libvirt_service.clone_vm(name, new_name)
        elif hasattr(libvirt_service, 'clone_domain'):
            libvirt_service.clone_domain(name, new_name)
        elif hasattr(libvirt_service, 'clone'):
            libvirt_service.clone(name, new_name)
        else:
            raise RuntimeError('No clone method found on LibvirtService.')

        try:
            if 'log_event' in globals():
                log_event(db, user, 'clone_vm', name, f'new_name={new_name}')
        except Exception:
            pass

        return RedirectResponse(
            url=f'/?message={quote("Cloned VM " + name + " to " + new_name)}',
            status_code=303,
        )

    except Exception as exc:
        try:
            if 'log_event' in globals():
                log_event(db, user, 'clone_vm_failed', name, str(exc))
        except Exception:
            pass

        return RedirectResponse(
            url=f'/vms/{name}/clone?error={quote(str(exc))}',
            status_code=303,
        )

@app.post('/vms/{name}/edit')
def vm_edit_submit(
    name: str,
    memory_mb: int = Form(...),
    vcpus: int = Form(...),
    description: str = Form(''),
    db: Session = Depends(get_db),
    user: str = Depends(require_operator),
):
    from urllib.parse import quote
    import libvirt

    settings = get_settings()

    try:
        if memory_mb < 128:
            raise RuntimeError('Memory must be at least 128 MB.')

        if vcpus < 1:
            raise RuntimeError('vCPU count must be at least 1.')

        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt.')

        try:
            dom = conn.lookupByName(name)

            if dom.isActive():
                raise RuntimeError('Shut down the VM before changing memory or vCPU settings.')

            mem_kib = int(memory_mb) * 1024
            cpu_count = int(vcpus)

            # Update persistent/inactive VM config only.
            # Maximums first, then current values, because libvirt enjoys making order matter.
            dom.setMemoryFlags(
                mem_kib,
                libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_MEM_MAXIMUM,
            )
            dom.setMemoryFlags(
                mem_kib,
                libvirt.VIR_DOMAIN_AFFECT_CONFIG,
            )

            dom.setVcpusFlags(
                cpu_count,
                libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_VCPU_MAXIMUM,
            )
            dom.setVcpusFlags(
                cpu_count,
                libvirt.VIR_DOMAIN_AFFECT_CONFIG,
            )

        finally:
            conn.close()

        try:
            if 'log_event' in globals():
                log_event(db, user, 'edit_vm', name, f'memory_mb={memory_mb}, vcpus={vcpus}')
        except Exception:
            pass

        return RedirectResponse(
            url=f'/vms/{name}?message={quote("Updated VM settings. Start the VM for the new values to take effect.")}',
            status_code=303,
        )

    except Exception as exc:
        try:
            if 'log_event' in globals():
                log_event(db, user, 'edit_vm_failed', name, str(exc))
        except Exception:
            pass

        return RedirectResponse(
            url=f'/vms/{name}?error={quote(str(exc))}',
            status_code=303,
        )

@app.get('/vms/{name}/network')


def vm_network_page(
    name: str,
    request: Request,
    user: str = Depends(require_operator),
):
    import libvirt
    import xml.etree.ElementTree as ET

    settings = get_settings()
    networks = []
    interfaces = []
    error = request.query_params.get('error')

    try:
        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt.')

        try:
            for net_name in conn.listNetworks():
                networks.append({'name': net_name,
                    'active': True, 'vlan_tag': NetworkPhase8Service(settings.libvirt_uri).get_vlan_tag(net_name)})

            for net_name in conn.listDefinedNetworks():
                networks.append({'name': net_name,
                    'active': False, 'vlan_tag': NetworkPhase8Service(settings.libvirt_uri).get_vlan_tag(net_name)})

            networks = sorted(networks, key=lambda n: n['name'])

            dom = conn.lookupByName(name)
            is_active = bool(dom.isActive())

            xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
            xml = dom.XMLDesc(xml_flags)
            root = ET.fromstring(xml)

            index = 0
            for iface in root.findall("./devices/interface"):
                index += 1
                iface_type = iface.attrib.get("type", "")

                mac_el = iface.find("mac")
                source_el = iface.find("source")
                model_el = iface.find("model")
                vlan_el = iface.find("vlan")
                vlan_el = iface.find("vlan")

                mac = mac_el.attrib.get("address", "") if mac_el is not None else ""
                model = model_el.attrib.get("type", "") if model_el is not None else ""

                source = ""
                if source_el is not None:
                    source = (
                        source_el.attrib.get("network")
                        or source_el.attrib.get("bridge")
                        or source_el.attrib.get("dev")
                        or ""
                    )

                vlan_tag = ""
                if vlan_el is not None:
                    tag_el = vlan_el.find("tag")
                    if tag_el is not None:
                        vlan_tag = tag_el.attrib.get("id", "")

                vlan_tag = ""
                if vlan_el is not None:
                    tag_el = vlan_el.find("tag")
                    if tag_el is not None:
                        vlan_tag = tag_el.attrib.get("id", "")

                interfaces.append({
                    'index': index,
                    'type': iface_type,
                    'mac': mac,
                    'source': source,
                    'model': model,
                    'vlan_tag': vlan_tag,
                    'vlan_tag': vlan_tag,
                })

        finally:
            conn.close()

    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(
        'vm_network.html',
        {
            'request': request,
            'app_name': settings.app_name,
            'name': name,
        'vlan_tag': _atlasvm_get_network_vlan_tag(name),
            'networks': networks,
            'interfaces': interfaces,
            'user': user,
            'message': request.query_params.get('message'),
            'error': error,
        },
    )







def _atlasvm_build_interface_xml(network_name: str, mac: str = '', model_type: str = 'virtio', vlan_tag: str = '') -> str:
    import xml.etree.ElementTree as ET

    iface = ET.Element("interface", {"type": "network"})

    if mac:
        ET.SubElement(iface, "mac", {"address": mac})

    ET.SubElement(iface, "source", {"network": network_name})

    vlan_tag = str(vlan_tag or '').strip()
    if not vlan_tag:
        vlan_tag = NetworkPhase8Service(settings.libvirt_uri).get_vlan_tag(network_name)

    if vlan_tag:
        vlan_id = int(vlan_tag)
        if vlan_id < 1 or vlan_id > 4094:
            raise RuntimeError('VLAN tag must be between 1 and 4094.')

        vlan_el = ET.SubElement(iface, "vlan")
        ET.SubElement(vlan_el, "tag", {"id": str(vlan_id)})

    ET.SubElement(iface, "model", {"type": model_type or "virtio"})

    return ET.tostring(iface, encoding="unicode")


def _atlasvm_find_interface_by_mac(root, mac_address: str):
    mac_address = (mac_address or '').strip().lower()

    interfaces = root.findall("./devices/interface")
    if not interfaces:
        raise RuntimeError('VM has no network interfaces.')

    if not mac_address:
        return interfaces[0]

    for iface in interfaces:
        mac_el = iface.find("mac")
        if mac_el is not None and mac_el.attrib.get("address", "").lower() == mac_address:
            return iface

    raise RuntimeError(f'No interface found with MAC address {mac_address}.')


@app.post('/vms/{name}/network')
def vm_network_update(
    name: str,
    network_name: str = Form(...),
    mac_address: str = Form(''),
    vlan_tag: str = Form(''),
    live_switch: bool = Form(False),
    user: str = Depends(require_operator),
):
    from urllib.parse import quote
    import libvirt
    import xml.etree.ElementTree as ET

    settings = get_settings()
    network_name = network_name.strip()
    mac_address = mac_address.strip().lower()
    vlan_tag = str(vlan_tag or '').strip()

    try:
        if not network_name:
            raise RuntimeError('Network name is required.')

        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt.')

        try:
            dom = conn.lookupByName(name)
            is_active = bool(dom.isActive())

            try:
                conn.networkLookupByName(network_name)
            except Exception:
                raise RuntimeError(f'Network does not exist: {network_name}')

            if is_active and not live_switch:
                raise RuntimeError('VM is running. Check "Apply live" to change this NIC while the VM is running, or shut down the VM first.')

            xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
            xml = dom.XMLDesc(xml_flags)
            root = ET.fromstring(xml)

            selected = _atlasvm_find_interface_by_mac(root, mac_address)

            mac_el = selected.find("mac")
            if mac_el is None or not mac_el.attrib.get("address"):
                raise RuntimeError('Selected interface does not have a MAC address.')

            mac = mac_el.attrib.get("address").lower()

            model_el = selected.find("model")
            model_type = model_el.attrib.get("type") if model_el is not None else "virtio"
            if not model_type:
                model_type = "virtio"

            old_iface_xml = ET.tostring(selected, encoding="unicode")
            new_iface_xml = _atlasvm_build_interface_xml(
                network_name=network_name,
                mac=mac,
                model_type=model_type,
                vlan_tag=vlan_tag,
            )

            if is_active:
                flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG

                try:
                    dom.detachDeviceFlags(old_iface_xml, flags)
                except Exception as exc:
                    raise RuntimeError(f'Failed to detach existing NIC: {exc}')

                try:
                    dom.attachDeviceFlags(new_iface_xml, flags)
                except Exception as exc:
                    try:
                        dom.attachDeviceFlags(old_iface_xml, flags)
                    except Exception:
                        pass

                    raise RuntimeError(f'Failed to attach updated NIC: {exc}')
            else:
                parent = root.find("./devices")
                if parent is None:
                    raise RuntimeError('VM XML does not contain devices section.')

                insert_at = list(parent).index(selected)
                parent.remove(selected)
                parent.insert(insert_at, ET.fromstring(new_iface_xml))

                new_xml = ET.tostring(root, encoding="unicode")
                conn.defineXML(new_xml)

        finally:
            conn.close()

        vlan_text = f' with VLAN {vlan_tag}' if vlan_tag else ''
        if is_active:
            message = f'Updated live NIC to {network_name}{vlan_text}. The guest may need DHCP renewal.'
        else:
            message = f'Updated NIC to {network_name}{vlan_text}. Start the VM for the change to take effect.'

        return RedirectResponse(
            url=f'/vms/{name}?message={quote(message)}',
            status_code=303,
        )

    except Exception as exc:
        return RedirectResponse(
            url=f'/vms/{name}/network?error={quote(str(exc))}',
            status_code=303,
        )


@app.post('/vms/{name}/network/add')
def vm_network_add(
    name: str,
    network_name: str = Form(...),
    model_type: str = Form('virtio'),
    vlan_tag: str = Form(''),
    apply_live: bool = Form(False),
    user: str = Depends(require_operator),
):
    from urllib.parse import quote
    import libvirt
    import xml.etree.ElementTree as ET

    settings = get_settings()
    network_name = network_name.strip()
    model_type = (model_type or 'virtio').strip()
    vlan_tag = str(vlan_tag or '').strip()

    try:
        if not network_name:
            raise RuntimeError('Network name is required.')

        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt.')

        try:
            dom = conn.lookupByName(name)
            is_active = bool(dom.isActive())

            try:
                conn.networkLookupByName(network_name)
            except Exception:
                raise RuntimeError(f'Network does not exist: {network_name}')

            if is_active and not apply_live:
                raise RuntimeError('VM is running. Check "Apply live" to add this NIC while the VM is running, or shut down the VM first.')

            new_iface_xml = _atlasvm_build_interface_xml(
                network_name=network_name,
                mac='',
                model_type=model_type,
                vlan_tag=vlan_tag,
            )

            if is_active:
                flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
                dom.attachDeviceFlags(new_iface_xml, flags)
            else:
                xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
                root = ET.fromstring(xml)
                devices = root.find("./devices")
                if devices is None:
                    raise RuntimeError('VM XML does not contain devices section.')

                devices.append(ET.fromstring(new_iface_xml))
                new_xml = ET.tostring(root, encoding="unicode")
                conn.defineXML(new_xml)

        finally:
            conn.close()

        vlan_text = f' with VLAN {vlan_tag}' if vlan_tag else ''
        if is_active:
            message = f'Added live NIC on {network_name}{vlan_text}. The guest may need DHCP renewal.'
        else:
            message = f'Added NIC on {network_name}{vlan_text}. Start the VM for the change to take effect.'

        return RedirectResponse(
            url=f'/vms/{name}?message={quote(message)}',
            status_code=303,
        )

    except Exception as exc:
        return RedirectResponse(
            url=f'/vms/{name}/network?error={quote(str(exc))}',
            status_code=303,
        )


@app.post('/vms/{name}/network/remove')
def vm_network_remove(
    name: str,
    mac_address: str = Form(...),
    apply_live: bool = Form(False),
    user: str = Depends(require_operator),
):
    from urllib.parse import quote
    import libvirt
    import xml.etree.ElementTree as ET

    settings = get_settings()
    mac_address = mac_address.strip().lower()

    try:
        if not mac_address:
            raise RuntimeError('MAC address is required.')

        conn = libvirt.open(settings.libvirt_uri)
        if conn is None:
            raise RuntimeError('Could not connect to libvirt.')

        try:
            dom = conn.lookupByName(name)
            is_active = bool(dom.isActive())

            if is_active and not apply_live:
                raise RuntimeError('VM is running. Check "Apply live" to remove this NIC while the VM is running, or shut down the VM first.')

            xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
            xml = dom.XMLDesc(xml_flags)
            root = ET.fromstring(xml)

            selected = _atlasvm_find_interface_by_mac(root, mac_address)
            old_iface_xml = ET.tostring(selected, encoding="unicode")

            if is_active:
                flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
                dom.detachDeviceFlags(old_iface_xml, flags)
            else:
                devices = root.find("./devices")
                if devices is None:
                    raise RuntimeError('VM XML does not contain devices section.')

                devices.remove(selected)
                new_xml = ET.tostring(root, encoding="unicode")
                conn.defineXML(new_xml)

        finally:
            conn.close()

        if is_active:
            message = f'Removed live NIC {mac_address}.'
        else:
            message = f'Removed NIC {mac_address}. Start the VM for the change to take effect.'

        return RedirectResponse(
            url=f'/vms/{name}?message={quote(message)}',
            status_code=303,
        )

    except Exception as exc:
        return RedirectResponse(
            url=f'/vms/{name}/network?error={quote(str(exc))}',
            status_code=303,
        )


