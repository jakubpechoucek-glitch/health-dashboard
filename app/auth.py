import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, get_settings

_basic = HTTPBasic(auto_error=False)


def require_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
) -> None:
    """Jednouživatelské přihlášení. Bez nastaveného hesla se nepustí nikdo."""
    if not settings.app_password:
        raise HTTPException(status_code=503, detail="APP_PASSWORD není nastaveno")
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username.encode(), settings.app_user.encode())
        and secrets.compare_digest(credentials.password.encode(), settings.app_password.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Nutné přihlášení",
            headers={"WWW-Authenticate": 'Basic realm="Health"'},
        )
