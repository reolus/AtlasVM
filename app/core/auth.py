import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.core.config import get_settings
from app.core.database import SessionLocal, UserAccount
from app.core.security import verify_password

security = HTTPBasic()

ROLE_RANK = {'viewer': 1, 'operator': 2, 'admin': 3}


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


def get_user_role(username: str) -> str:
    user = _db_user(username)
    if user and user.is_active:
        return user.role
    settings = get_settings()
    if username == settings.username:
        return 'admin'
    return 'viewer'


def require_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user = _db_user(credentials.username)
    if user and user.is_active and verify_password(credentials.password, user.password_hash):
        return user.username

    # Safety fallback for upgrades where the user table could not be read yet.
    fallback = _env_auth(credentials)
    if fallback:
        return fallback

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
