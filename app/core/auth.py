import base64
import hashlib
import hmac
import secrets
import time
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.core.config import get_settings
from app.core.database import SessionLocal, UserAccount
from app.core.security import verify_password

security = HTTPBasic(auto_error=False)
ROLE_RANK = {'viewer': 1, 'operator': 2, 'admin': 3}
SESSION_COOKIE = 'atlasvm_session'
SESSION_TTL_SECONDS = 60 * 60 * 12


def _signing_secret() -> bytes:
    settings = get_settings()
    secret = settings.session_secret or settings.password or 'atlasvm-development-secret'
    return secret.encode('utf-8')


def _signature(payload: str) -> str:
    digest = hmac.new(_signing_secret(), payload.encode('utf-8'), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def create_session_token(username: str) -> str:
    issued = str(int(time.time()))
    payload = f'{username}|{issued}'
    return f'{payload}|{_signature(payload)}'


def verify_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        username, issued_text, supplied_sig = token.split('|', 2)
        payload = f'{username}|{issued_text}'
        expected_sig = _signature(payload)
        if not hmac.compare_digest(supplied_sig, expected_sig):
            return None
        issued = int(issued_text)
        if time.time() - issued > SESSION_TTL_SECONDS:
            return None
        user = _db_user(username)
        if user and user.is_active:
            return user.username
        settings = get_settings()
        if username == settings.username:
            return username
        return None
    except Exception:
        return None


def _env_auth(credentials: HTTPBasicCredentials) -> str | None:
    settings = get_settings()
    username_ok = secrets.compare_digest(credentials.username.encode('utf8'), settings.username.encode('utf8'))
    password_ok = secrets.compare_digest(credentials.password.encode('utf8'), settings.password.encode('utf8'))
    if username_ok and password_ok:
        return credentials.username
    return None


def _db_user(username: str) -> UserAccount | None:
    db = SessionLocal()
    try:
        return db.query(UserAccount).filter(UserAccount.username == username).first()
    finally:
        db.close()


def authenticate_username_password(username: str, password: str) -> str | None:
    user = _db_user(username)
    if user and user.is_active and verify_password(password, user.password_hash):
        return user.username

    credentials = HTTPBasicCredentials(username=username, password=password)
    return _env_auth(credentials)


def get_user_role(username: str | None) -> str:
    if not username:
        return 'viewer'
    user = _db_user(username)
    if user and user.is_active:
        return user.role
    settings = get_settings()
    if username == settings.username:
        return 'admin'
    return 'viewer'


def require_user(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    session_user = verify_session_token(request.cookies.get(SESSION_COOKIE))
    if session_user:
        return session_user

    if credentials is not None:
        user = _db_user(credentials.username)
        if user and user.is_active and verify_password(credentials.password, user.password_hash):
            return user.username

        # Safety fallback for upgrades where the user table could not be read yet.
        fallback = _env_auth(credentials)
        if fallback:
            return fallback

    accept = request.headers.get('accept', '')
    if 'text/html' in accept or '*/*' in accept:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={'Location': '/login'})

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Invalid AtlasVM credentials',
        headers={'WWW-Authenticate': 'Basic'},
    )


def require_role(required_role: str, username: str) -> str:
    actual_role = get_user_role(username)
    if ROLE_RANK.get(actual_role, 0) >= ROLE_RANK.get(required_role, 99):
        return username
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'AtlasVM {required_role} rights are required')


def require_viewer(username: str = Depends(require_user)) -> str:
    return require_role('viewer', username)


def require_operator(username: str = Depends(require_user)) -> str:
    return require_role('operator', username)


def require_admin(username: str = Depends(require_user)) -> str:
    return require_role('admin', username)
