"""Microsoft Entra ID sign-in for the Techlabs gallery.

Server-side OAuth Authorization-Code + PKCE flow, modeled on the
BusinessAgent web app (web/src/auth + agent/src/agent/auth). Stripped
down to *just authentication*: identity-only scope, no CRM/Graph calls,
no Key Vault, no token persistence.

Like BusinessAgent it reuses the **Azure CLI public client**
(`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) — every user in the tenant has
already consented to it, so no app registration is required. The public
client only accepts loopback redirect URIs (`http://localhost[:port]/`),
which suits this locally-run internal tool.

Sessions live in process memory keyed by an opaque `HttpOnly` cookie.
A restart wipes them and forces re-sign-in — intentional: no tokens at
rest, no encryption-at-rest requirement, smaller leak surface.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

# Azure CLI's well-known public client ID — the same one `az login` uses.
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
AUTHORITY = "https://login.microsoftonline.com"
USER_AGENT = "TechlabsGallery/1.0"

ARM_SCOPE = "https://management.azure.com/user_impersonation"
SCOPE = "openid profile offline_access"
GRAPH_PHOTO_SCOPE = "https://graph.microsoft.com/.default offline_access openid profile"

COOKIE_NAME = "techlabs_session"
COOKIE_MAX_AGE = 60 * 60 * 8  # 8h working session
PKCE_TTL_SECONDS = 10 * 60
PHOTO_TTL_SECONDS = 60 * 60 * 6  # photos rarely change


# --------------------------------------------------------------------------- #
# PKCE + state helpers (mirror agent/auth/pkce.py)
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce_verifier(byte_len: int = 64) -> str:
    return _b64url(secrets.token_bytes(byte_len))


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def make_state(byte_len: int = 32) -> str:
    return _b64url(secrets.token_bytes(byte_len))


def normalize_loopback_redirect(raw: str) -> str:
    """Rewrite 127.0.0.1 -> localhost so both auth legs byte-match."""
    raw = raw.strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.hostname and parsed.hostname.lower() == "127.0.0.1":
        port = f":{parsed.port}" if parsed.port else ""
        parsed = parsed._replace(netloc=f"localhost{port}")
    return urlunparse(parsed)


def _identity_from_id_token(id_token: str) -> "Identity | None":
    """Decode identity claims from an id_token (no signature check needed:
    the token came directly from the token endpoint over TLS)."""
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return Identity(
        oid=str(claims.get("oid", "")),
        upn=str(claims.get("preferred_username") or claims.get("upn") or ""),
        tenant_id=str(claims.get("tid", "")),
        name=str(claims.get("name", "")),
    )


# Claim type URIs used by App Service Easy Auth in the X-MS-CLIENT-PRINCIPAL blob.
_EA_OID_CLAIMS = (
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "oid",
)
_EA_UPN_CLAIMS = (
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "upn",
)
_EA_NAME_CLAIMS = (
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)
_EA_TID_CLAIMS = (
    "http://schemas.microsoft.com/identity/claims/tenantid",
    "tid",
)


def identity_from_easy_auth(request: Request) -> "Identity | None":
    """Build an Identity from App Service Authentication (Easy Auth) headers.

    When hosted behind App Service Authentication, the platform authenticates
    the user at the edge and forwards identity in request headers. App Service
    strips any client-supplied copies of these headers, so they are trusted.
    Returns None when the headers are absent (e.g. local dev), letting the
    caller fall back to the self-hosted OAuth session.
    """
    def _first(claims: dict[str, str], keys: tuple[str, ...]) -> str:
        for k in keys:
            if claims.get(k):
                return str(claims[k])
        return ""

    principal = request.headers.get("x-ms-client-principal")
    if principal:
        try:
            data = json.loads(base64.b64decode(principal).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            claims = {
                str(c.get("typ")): str(c.get("val"))
                for c in data.get("claims", [])
                if isinstance(c, dict)
            }
            oid = _first(claims, _EA_OID_CLAIMS)
            upn = _first(claims, _EA_UPN_CLAIMS)
            name = _first(claims, _EA_NAME_CLAIMS) or upn
            tid = _first(claims, _EA_TID_CLAIMS)
            if oid or upn:
                return Identity(oid=oid, upn=upn, tenant_id=tid, name=name)

    # Simple header fallback (set alongside the principal blob).
    name_hdr = request.headers.get("x-ms-client-principal-name")
    id_hdr = request.headers.get("x-ms-client-principal-id")
    if name_hdr or id_hdr:
        return Identity(
            oid=str(id_hdr or ""),
            upn=str(name_hdr or ""),
            tenant_id="",
            name=str(name_hdr or ""),
        )
    return None



# --------------------------------------------------------------------------- #
# Identity + session store (mirror agent/auth/session_store.py, identity-only)
# --------------------------------------------------------------------------- #
@dataclass
class Identity:
    oid: str
    upn: str
    tenant_id: str
    name: str = ""


@dataclass
class Session:
    session_id: str
    identity: Identity | None = None
    # Kept only in process memory to read the Graph profile photo on demand.
    refresh_token: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def signed_in(self) -> bool:
        return self.identity is not None and bool(self.identity.oid)


class SessionStore:
    """In-memory session store. One per process; restart = re-auth."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    @staticmethod
    def new_session_id() -> str:
        return secrets.token_hex(32)

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        return self._sessions.get(sid)

    def set_identity(
        self, sid: str, identity: Identity, refresh_token: str = ""
    ) -> None:
        rec = self._sessions.setdefault(sid, Session(session_id=sid))
        rec.identity = identity
        if refresh_token:
            rec.refresh_token = refresh_token

    def set_refresh_token(self, sid: str, refresh_token: str) -> None:
        rec = self._sessions.get(sid)
        if rec is not None and refresh_token:
            rec.refresh_token = refresh_token

    def clear(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)


# --------------------------------------------------------------------------- #
# Entra OAuth client (mirror agent/auth/entra_device.py, code+PKCE only)
# --------------------------------------------------------------------------- #
class EntraAuthError(Exception):
    pass


class EntraClient:
    def __init__(self, tenant_id: str, client_id: str = AZURE_CLI_CLIENT_ID) -> None:
        # Use the tenant GUID to restrict sign-in to your org. "organizations"
        # allows any work/school account; "common" also allows personal.
        self._tenant = tenant_id.strip() or "organizations"
        self._client_id = client_id

    @property
    def tenant_id(self) -> str:
        return self._tenant

    def _endpoint(self, path: str) -> str:
        return f"{AUTHORITY}/{self._tenant}/oauth2/v2.0/{path}"

    def build_authorize_url(
        self, redirect_uri: str, state: str, challenge: str, arm: bool = False
    ) -> str:
        scope = f"{SCOPE} {ARM_SCOPE}" if arm else SCOPE
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._endpoint('authorize')}?{urlencode(params)}"

    async def exchange_code(
        self, code: str, verifier: str, redirect_uri: str, arm: bool = False
    ) -> tuple[Identity, str]:
        scope = f"{SCOPE} {ARM_SCOPE}" if arm else SCOPE
        data = {
            "client_id": self._client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": scope,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as http:
            resp = await http.post(self._endpoint("token"), data=data)
        body = resp.json() if resp.content else {}
        err = body.get("error", "")
        if err:
            raise EntraAuthError(
                f"{err}: {body.get('error_description', '').strip()}".strip(": ")
            )
        identity = _identity_from_id_token(body.get("id_token", ""))
        if identity is None or not identity.oid:
            raise EntraAuthError("token exchange returned no identity")
        return identity, str(body.get("refresh_token", ""))

    async def acquire_graph_token(self, refresh_token: str) -> tuple[str, str]:
        """Mint a short-lived Graph access token from the stored refresh token.

        Azure CLI public-client refresh tokens are multi-audience, so this
        works without any app registration or extra consent. Returns
        (access_token, rotated_refresh_token); the rotated refresh token (if
        any) should replace the stored one.
        """
        data = {
            "client_id": self._client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": GRAPH_PHOTO_SCOPE,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as http:
            resp = await http.post(self._endpoint("token"), data=data)
        body = resp.json() if resp.content else {}
        if body.get("error"):
            raise EntraAuthError(
                f"{body['error']}: {body.get('error_description', '').strip()}".strip(
                    ": "
                )
            )
        token = str(body.get("access_token", ""))
        if not token:
            raise EntraAuthError("refresh returned no access token")
        return token, str(body.get("refresh_token", ""))

    async def acquire_arm_token(self, refresh_token: str) -> tuple[str, str, int]:
        """Mint a short-lived ARM token from the in-memory refresh token."""
        data = {
            "client_id": self._client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": ARM_SCOPE,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as http:
            resp = await http.post(self._endpoint("token"), data=data)
        body = resp.json() if resp.content else {}
        if body.get("error"):
            raise EntraAuthError(
                f"{body['error']}: {body.get('error_description', '').strip()}".strip(": ")
            )
        token = str(body.get("access_token", ""))
        if not token:
            raise EntraAuthError("refresh returned no ARM access token")
        return token, str(body.get("refresh_token", "")), int(body.get("expires_in", 3600))

    async def begin_device_code(self) -> dict[str, object]:
        """Start the device authorization flow against the configured tenant.

        Returns the raw provider response (device_code, user_code,
        verification_uri, expires_in, interval, message). The device code flow
        needs no redirect URI, so it works for a hosted web app where the Azure
        CLI public client's loopback-only redirect cannot be used.
        """
        data = {"client_id": self._client_id, "scope": SCOPE}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as http:
            resp = await http.post(
                f"{AUTHORITY}/{self._tenant}/oauth2/v2.0/devicecode", data=data
            )
        body = resp.json() if resp.content else {}
        if body.get("error"):
            raise EntraAuthError(
                f"{body['error']}: {body.get('error_description', '').strip()}".strip(
                    ": "
                )
            )
        if not body.get("device_code") or not body.get("user_code"):
            raise EntraAuthError("device authorization returned no code")
        return body

    async def poll_device_code(self, device_code: str) -> "tuple[Identity, str] | None":
        """Poll the token endpoint for a completed device authorization.

        Returns (identity, refresh_token) once the user finishes signing in, or
        None while still pending (authorization_pending / slow_down). Raises
        EntraAuthError on a terminal error (expired_token, access_denied, ...).
        """
        data = {
            "client_id": self._client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as http:
            resp = await http.post(self._endpoint("token"), data=data)
        body = resp.json() if resp.content else {}
        err = str(body.get("error", ""))
        if err in ("authorization_pending", "slow_down"):
            return None
        if err:
            raise EntraAuthError(
                f"{err}: {body.get('error_description', '').strip()}".strip(": ")
            )
        identity = _identity_from_id_token(body.get("id_token", ""))
        if identity is None or not identity.oid:
            raise EntraAuthError("device code exchange returned no identity")
        return identity, str(body.get("refresh_token", ""))


# --------------------------------------------------------------------------- #
# Router: /api/auth/entra/*
# --------------------------------------------------------------------------- #
class StartResponse(BaseModel):
    auth_url: str


class DeviceStartResponse(BaseModel):
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str = ""


class DevicePollResponse(BaseModel):
    status: str  # "pending" | "complete" | "error"
    message: str = ""


class WhoamiResponse(BaseModel):
    signed_in: bool
    upn: str = ""
    name: str = ""
    oid: str = ""
    tenant_id: str = ""
    logout_url: str = ""

def _store(request: Request) -> SessionStore:
    return request.app.state.session_store


async def acquire_arm_token_for_session(
    request: Request, session_id: str | None
) -> tuple[str, int] | None:
    """Return a delegated ARM token for the self-hosted OAuth session."""
    if not session_id:
        return None
    rec = _store(request).get(session_id)
    if rec is None or not rec.signed_in or not rec.refresh_token:
        return None
    token, rotated, expires_in = await request.app.state.entra_client.acquire_arm_token(
        rec.refresh_token
    )
    if rotated:
        _store(request).set_refresh_token(rec.session_id, rotated)
    return token, expires_in


def _client(request: Request) -> EntraClient:
    return request.app.state.entra_client


def _pkce(request: Request) -> dict[str, dict[str, object]]:
    return request.app.state.entra_pkce


def _device(request: Request) -> dict[str, dict[str, object]]:
    return request.app.state.entra_device


def _photo_cache(request: Request) -> dict[str, dict[str, object]]:
    cache = getattr(request.app.state, "entra_photo_cache", None)
    if cache is None:
        cache = {}
        request.app.state.entra_photo_cache = cache
    return cache


def _redirect_uri(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    uri = normalize_loopback_redirect(f"{base}/")
    # Behind App Service's TLS-terminating proxy, request.base_url often reports
    # http:// even though the public URL is https://. The registered redirect
    # URI is https, and both auth legs must byte-match, so force https when the
    # app is configured for it (GALLERY_HTTPS=true).
    if uri.startswith("http://") and os.environ.get(
        "GALLERY_HTTPS", ""
    ).strip().lower() in ("1", "true", "yes"):
        uri = "https://" + uri[len("http://") :]
    return uri


def _set_cookie(response: Response, sid: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sid,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def build_router(secure_cookie: bool) -> APIRouter:
    router = APIRouter(prefix="/api/auth/entra", tags=["auth"])

    @router.get("/start", response_model=StartResponse)
    async def start(  # type: ignore[unused-ignore]
        request: Request,
        response: Response,
        redirect: int = 0,
        external: int = 0,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> StartResponse | RedirectResponse:
        sid = session or SessionStore.new_session_id()
        _set_cookie(response, sid, secure_cookie)
        redirect_uri = _redirect_uri(request)
        verifier = make_pkce_verifier()
        state = make_state()
        _pkce(request)[state] = {
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "session_id": sid,
            "arm": bool(external),
            "expires_at": time.time() + PKCE_TTL_SECONDS,
        }
        auth_url = _client(request).build_authorize_url(
            redirect_uri, state, pkce_challenge(verifier), arm=bool(external)
        )
        if redirect:
            r = RedirectResponse(auth_url, status_code=302)
            for c in response.headers.getlist("set-cookie"):
                r.headers.append("set-cookie", c)
            return r
        return StartResponse(auth_url=auth_url)

    @router.post("/device/start", response_model=DeviceStartResponse)
    async def device_start(  # type: ignore[unused-ignore]
        request: Request,
        response: Response,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> DeviceStartResponse:
        """Begin device-code sign-in: returns a user code + verification URL.

        Works for the hosted web app (no redirect URI needed). The browser shows
        the code; the user enters it at microsoft.com/devicelogin; meanwhile the
        page polls /device/poll until the session is established.
        """
        sid = session or SessionStore.new_session_id()
        _set_cookie(response, sid, secure_cookie)
        try:
            body = await _client(request).begin_device_code()
        except EntraAuthError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        interval = int(body.get("interval", 5) or 5)
        expires_in = int(body.get("expires_in", 900) or 900)
        dev = _device(request)
        now = time.time()
        for k in [k for k, v in dev.items() if float(v.get("expires_at", 0.0)) < now]:
            dev.pop(k, None)
        dev[sid] = {
            "device_code": str(body.get("device_code", "")),
            "interval": interval,
            "expires_at": now + expires_in,
        }
        return DeviceStartResponse(
            user_code=str(body.get("user_code", "")),
            verification_uri=str(
                body.get("verification_uri")
                or body.get("verification_url")
                or "https://microsoft.com/devicelogin"
            ),
            expires_in=expires_in,
            interval=interval,
            message=str(body.get("message", "")),
        )

    @router.post("/device/poll", response_model=DevicePollResponse)
    async def device_poll(  # type: ignore[unused-ignore]
        request: Request,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> DevicePollResponse:
        dev = _device(request)
        entry = dev.get(session) if session else None
        if entry is None:
            return DevicePollResponse(
                status="error", message="no sign-in in progress; start again"
            )
        if float(entry.get("expires_at", 0.0)) < time.time():
            dev.pop(session, None)
            return DevicePollResponse(
                status="error", message="sign-in code expired; start again"
            )
        try:
            result = await _client(request).poll_device_code(
                str(entry.get("device_code", ""))
            )
        except EntraAuthError as exc:
            dev.pop(session, None)
            return DevicePollResponse(status="error", message=str(exc))
        if result is None:
            return DevicePollResponse(status="pending")
        identity, refresh_token = result
        dev.pop(session, None)
        _store(request).set_identity(session, identity, refresh_token)
        return DevicePollResponse(status="complete")

    @router.post("/signout")
    async def signout(  # type: ignore[unused-ignore]
        request: Request,
        response: Response,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> dict[str, bool]:
        _store(request).clear(session)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"signed_out": True}

    @router.get("/whoami", response_model=WhoamiResponse)
    async def whoami(  # type: ignore[unused-ignore]
        request: Request,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> WhoamiResponse:
        # Prefer App Service Easy Auth identity when hosted behind it.
        ea = identity_from_easy_auth(request)
        if ea is not None:
            return WhoamiResponse(
                signed_in=True,
                upn=ea.upn,
                name=ea.name,
                oid=ea.oid,
                tenant_id=ea.tenant_id,
                # Sign-out must clear the platform session, not just app state.
                logout_url="/.auth/logout?post_logout_redirect_uri=/",
            )
        rec = _store(request).get(session)
        if rec is None or not rec.signed_in or rec.identity is None:
            return WhoamiResponse(signed_in=False)
        i = rec.identity
        return WhoamiResponse(
            signed_in=True, upn=i.upn, name=i.name, oid=i.oid, tenant_id=i.tenant_id
        )

    @router.get("/photo")
    async def photo(  # type: ignore[unused-ignore]
        request: Request,
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> Response:
        """Return the signed-in user's Microsoft profile photo.

        Uses the in-memory refresh token to mint a Graph-scoped access token,
        then fetches `/me/photo/$value`. Bytes are cached per oid for 6h.
        204 when the user has no photo; 401 when not signed in.
        """
        rec = _store(request).get(session)
        if rec is None or not rec.signed_in or rec.identity is None:
            return Response(status_code=401)

        oid = rec.identity.oid
        cache = _photo_cache(request)
        now = time.time()
        cached = cache.get(oid)
        if cached and float(cached.get("expires_at", 0.0)) > now:
            if cached.get("status") == 200:
                return Response(
                    content=cached["body"],  # type: ignore[arg-type]
                    media_type=str(cached.get("content_type", "image/jpeg")),
                    headers={"Cache-Control": "private, max-age=3600"},
                )
            return Response(status_code=204)

        if not rec.refresh_token:
            return Response(status_code=204)

        try:
            token, rotated = await _client(request).acquire_graph_token(
                rec.refresh_token
            )
        except EntraAuthError:
            return Response(status_code=204)
        if rotated:
            _store(request).set_refresh_token(rec.session_id, rotated)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
                graph = await http.get(
                    "https://graph.microsoft.com/v1.0/me/photo/$value",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            return Response(status_code=503)

        if graph.status_code == 404:
            cache[oid] = {"status": 204, "expires_at": now + PHOTO_TTL_SECONDS}
            return Response(status_code=204)
        if graph.status_code != 200:
            return Response(status_code=503)

        body = graph.content
        content_type = graph.headers.get("content-type", "image/jpeg")
        cache[oid] = {
            "status": 200,
            "body": body,
            "content_type": content_type,
            "expires_at": now + PHOTO_TTL_SECONDS,
        }
        return Response(
            content=body,
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return router


async def finalize_oauth_landing(
    request: Request,
    code: str,
    state: str,
    error: str,
    error_description: str,
    secure_cookie: bool,
) -> RedirectResponse:
    """Complete the PKCE flow when Entra redirects the browser back to `/`.

    The Azure CLI public client only allows the loopback root as redirect URI,
    so the browser lands on `/?code=...&state=...`. This is called from the
    root route to exchange the code server-side, establish the session, then
    302 to a clean `/`. On any failure it 302s back to the sign-in page with a
    `?message=` the page surfaces (mirrors the BusinessAgent SPA error UX).
    """
    if error:
        desc = (error_description or "").strip()
        msg = f"{error}: {desc}" if desc else error
        return _signin_error_redirect(msg)

    pkce = _pkce(request)
    now = time.time()
    for k in [k for k, v in pkce.items() if float(v.get("expires_at", 0.0)) < now]:
        pkce.pop(k, None)
    entry = pkce.pop(state, None)
    if entry is None or float(entry.get("expires_at", 0.0)) < now:
        return _signin_error_redirect("invalid or expired sign-in attempt; try again")

    try:
        identity, refresh_token = await _client(request).exchange_code(
            code,
            str(entry.get("code_verifier") or ""),
            str(entry.get("redirect_uri") or _redirect_uri(request)),
            arm=bool(entry.get("arm")),
        )
    except EntraAuthError as exc:
        return _signin_error_redirect(str(exc))

    sid = str(entry.get("session_id") or SessionStore.new_session_id())
    _store(request).set_identity(sid, identity, refresh_token)

    redirect = RedirectResponse("/", status_code=302)
    _set_cookie(redirect, sid, secure_cookie)
    return redirect


def _signin_error_redirect(message: str) -> RedirectResponse:
    """302 back to the sign-in page with a message the page displays. Uses a
    `message`-only query (no `code`/`error`) so it does not re-trigger finalize."""
    return RedirectResponse(f"/?message={quote(message)}", status_code=302)
