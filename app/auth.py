"""Přihlášení přes Supabase (Google), stejné jako na Rozcestníku.

Prohlížeč pošle `Authorization: Bearer <access_token>`. Token ověříme u Supabase
(`/auth/v1/user`) a pustíme jen e-maily z `ALLOWED_EMAILS`. Členství v rodině
(`je_clen`) tu NESTAČÍ: zdravotní data jsou jen pro vlastníka.
"""
import hashlib
import time

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)
_CACHE: dict[str, tuple[float, str]] = {}   # sha256(token) -> (platné do, e-mail)
_CACHE_SECONDS = 60


def fetch_email(token: str, settings: Settings) -> str | None:
    """E-mail ověřeného uživatele podle Supabase, jinak None."""
    try:
        r = httpx.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={"apikey": settings.supabase_key, "Authorization": f"Bearer {token}"},
            timeout=5,
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    user = r.json()
    if not user.get("email_confirmed_at") and not user.get("confirmed_at"):
        return None
    return (user.get("email") or "").lower() or None


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    allowed = {e.strip().lower() for e in settings.allowed_emails.split(",") if e.strip()}
    if not allowed:
        raise HTTPException(status_code=503, detail="ALLOWED_EMAILS není nastaveno")
    if credentials is None:
        raise HTTPException(status_code=401, detail="Nutné přihlášení")

    token = credentials.credentials
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        email = cached[1]
    else:
        email = fetch_email(token, settings)
        if email:
            _CACHE[key] = (time.monotonic() + _CACHE_SECONDS, email)
    if email is None:
        raise HTTPException(status_code=401, detail="Neplatné přihlášení")
    if email not in allowed:
        raise HTTPException(status_code=403, detail="Tento účet nemá přístup")
    return email
