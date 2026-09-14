"""Request identity + role resolution for the gallery API.

The ``/api/*`` routes do not pass through the static-file auth gate in app.py,
so each protected route resolves the caller's identity here and fails closed.

Identity comes from one of two trusted sources, mirroring app.py:
  * App Service Authentication ("Easy Auth") headers, when hosted behind it.
  * The in-memory OAuth session keyed by the HttpOnly cookie (self-hosted mode).

Roles ('admin' | 'guest') are persisted in SQLite (see db.py). The first person
to ever sign in is bootstrapped as the admin; thereafter admins manage roles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException, Request

import db
from entra_auth import COOKIE_NAME, identity_from_easy_auth

_PLACEHOLDER_TENANTS = {"organizations", "common", "consumers", ""}


def _expected_tenant() -> str:
    tid = os.environ.get("GALLERY_ENTRA_TENANT_ID", "organizations").strip()
    return "" if tid.lower() in _PLACEHOLDER_TENANTS else tid


@dataclass
class CurrentUser:
    oid: str
    upn: str
    name: str
    tenant_id: str
    role: str = "guest"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _resolve_identity(request: Request) -> CurrentUser | None:
    """Return the authenticated caller, or None when unauthenticated."""
    ea = identity_from_easy_auth(request)
    if ea is not None and (ea.oid or ea.upn):
        ident = ea
    else:
        if os.environ.get("GALLERY_EASY_AUTH", "false").strip().lower() in (
            "1", "true", "yes"
        ):
            return None
        store = getattr(request.app.state, "session_store", None)
        sid = request.cookies.get(COOKIE_NAME)
        rec = store.get(sid) if store is not None else None
        if rec is None or not rec.signed_in or rec.identity is None:
            return None
        ident = rec.identity

    expected = _expected_tenant()
    if expected and ident.tenant_id.lower() != expected.lower():
        return None

    # Persist sign-in / refresh last_seen and read the effective role.
    role = db.upsert_user(ident.oid, ident.upn, ident.name)
    return CurrentUser(
        oid=ident.oid,
        upn=ident.upn,
        name=ident.name,
        tenant_id=ident.tenant_id,
        role=role,
    )


def current_user_optional(request: Request) -> CurrentUser | None:
    return _resolve_identity(request)


def require_user(request: Request) -> CurrentUser:
    user = _resolve_identity(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_admin(request: Request) -> CurrentUser:
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="administrator access required")
    return user


def verify_same_origin(request: Request) -> None:
    """CSRF defence for state-changing requests.

    The session cookie is SameSite=Lax, but we add an Origin/Referer check as
    defence in depth. Requests whose Origin (or Referer) host does not match the
    request host (or the configured public URL) are rejected.
    """
    host = request.headers.get("host", "")
    allowed_hosts = {host}
    public = os.environ.get("GALLERY_PUBLIC_URL", "").strip()
    if public:
        allowed_hosts.add(urlparse(public).netloc)

    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    candidate = origin or referer
    if not candidate:
        # No browser-supplied origin (e.g. server-to-server). The protected
        # routes still require a valid session/identity, so allow.
        return
    netloc = urlparse(candidate).netloc
    if netloc and netloc not in allowed_hosts:
        raise HTTPException(status_code=403, detail="cross-origin request rejected")
