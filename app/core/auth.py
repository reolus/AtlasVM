import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.core.config import get_settings

security = HTTPBasic()


def require_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    username_ok = secrets.compare_digest(credentials.username.encode('utf8'), settings.username.encode('utf8'))
    password_ok = secrets.compare_digest(credentials.password.encode('utf8'), settings.password.encode('utf8'))
    if not username_ok or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid AtlasVM credentials',
            headers={'WWW-Authenticate': 'Basic'},
        )
    return credentials.username
