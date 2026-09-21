"""Techlabs gallery — Entra-authenticated static host.

Serves the static gallery (site/index.html, assets/, catalog.json) only to
users who have signed in with Microsoft Entra ID. Unauthenticated requests are
redirected into the OAuth PKCE flow (see entra_auth.py).

Run locally:
    cd site/server
    pip install -r requirements.txt
    # set GALLERY_ENTRA_TENANT_ID (your tenant GUID) — see .env.example
    uvicorn app:app --host 127.0.0.1 --port 8000

Then browse http://localhost:8000/ and sign in.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, Cookie, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse

import db
import lifecycle
import installer
from distribution import brand_name, installer_requested, is_customer_distribution, validate_customer_configuration
from api import build_api_router
from setup_routes import build_setup_router
import setup_github
from scheduler import run_scheduler
from terraform_bootstrap import ensure_terraform
from entra_auth import (
    COOKIE_NAME,
    EntraClient,
    ExternalAzureClient,
    SessionStore,
    build_router,
    finalize_oauth_landing,
    identity_from_easy_auth,
)

# Static gallery lives one directory up from this server package.
SITE_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = Path(__file__).resolve().parent

# Tenant GUID restricts sign-in to your organization. "organizations" allows any
# work/school account; set your tenant GUID for true internal-only access.
TENANT_ID = os.environ.get("GALLERY_ENTRA_TENANT_ID", "organizations").strip()

# App registration (client) ID. Defaults to the Azure CLI public client, which
# only accepts loopback redirects (local dev). For a hosted deployment set this
# to your own public-client app registration that lists the site's https
# redirect URI, so the PKCE code exchange needs no client secret.
CLIENT_ID = os.environ.get("GALLERY_ENTRA_CLIENT_ID", "").strip()

# Send Secure cookies + HSTS only when served over HTTPS (e.g. behind a TLS
# terminating proxy). Defaults to off for local http://localhost.
HTTPS_ENABLED = os.environ.get("GALLERY_HTTPS", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# When hosted behind Azure App Service Authentication ("Easy Auth"), the
# platform authenticates every request at the edge and forwards a signed
# identity in X-MS-CLIENT-PRINCIPAL headers (client copies are stripped by the
# platform, so they are trustworthy). In this mode the app trusts that identity
# and the self-hosted OAuth flow is disabled — there is exactly one way in.
EASY_AUTH_ENABLED = os.environ.get("GALLERY_EASY_AUTH", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# A real tenant GUID (not the "organizations"/"common" placeholders) enables a
# defence-in-depth check that the signed-in user belongs to our tenant.
_PLACEHOLDER_TENANTS = {"organizations", "common", "consumers", ""}
EXPECTED_TENANT_ID = "" if TENANT_ID.lower() in _PLACEHOLDER_TENANTS else TENANT_ID

# Paths that never require authentication.
PUBLIC_PREFIXES = ("/api/auth/entra/",)
PUBLIC_PATHS = {"/favicon.ico", "/healthz"}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database and run the cleanup scheduler for the app's life."""
    validate_customer_configuration()
    db.init_db()
    if installer_requested():
        yield
        return
    # Best-effort, opt-in Terraform binary provisioning. Run off the event loop
    # and do not await it: boot must not block on a network download, and the UI
    # hides in-app Terraform deploys until the binary is present anyway.
    asyncio.create_task(asyncio.to_thread(ensure_terraform))
    stop = asyncio.Event()
    task = asyncio.create_task(run_scheduler(stop))
    sync_task = asyncio.create_task(setup_github.run_catalog_sync(stop)) if is_customer_distribution() else None
    from catalog_processing import run_regional_schedule
    regional_task = asyncio.create_task(run_regional_schedule(stop)) if is_customer_distribution() else None
    lifecycle_task = asyncio.create_task(lifecycle.run_schedule(stop)) if is_customer_distribution() else None
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        if lifecycle_task:
            lifecycle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lifecycle_task
        if regional_task:
            regional_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await regional_task
        if sync_task:
            sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sync_task
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=brand_name(),
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.state.session_store = SessionStore()
app.state.entra_client = (
    EntraClient(tenant_id=TENANT_ID, client_id=CLIENT_ID)
    if CLIENT_ID
    else EntraClient(tenant_id=TENANT_ID)
)
app.state.entra_pkce = {}
app.state.entra_device = {}
app.state.external_azure_client = ExternalAzureClient()
app.state.external_azure_device = {}
# Mount the /api/auth/entra/* router so the user badge can read whoami/photo and
# trigger sign-out. Behind Easy Auth the request gate ignores the self-hosted
# session cookie entirely (it trusts the platform identity headers instead), so
# the only *effective* way in remains App Service Authentication.
app.include_router(build_router(secure_cookie=HTTPS_ENABLED or EASY_AUTH_ENABLED))
# Control-plane API: identity, telemetry, deployments, admin. These routes do
# their own identity + role checks (they don't pass through the static gate).
app.include_router(build_api_router())
app.include_router(build_setup_router())
app.include_router(installer.build_installer_router())



@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Apply SFI-aligned security headers to every response."""
    response: Response
    if installer_requested() and request.url.path != "/healthz":
        if request.url.path == "/" and request.method == "GET":
            response = RedirectResponse("/install", status_code=303)
        elif request.url.path == "/assets/styles.css" and request.method == "GET":
            response = FileResponse(SITE_ROOT / "assets/styles.css")
        elif request.url.path in installer.INSTALL_PATHS | installer.API_PATHS:
            response = await call_next(request)
        else:
            response = JSONResponse({"detail": "installation must be completed first"}, status_code=503)
    elif is_customer_distribution() and request.url.path != "/healthz":
        if request.url.path.startswith("/api/auth/entra/") and request.url.path not in (
            "/api/auth/entra/whoami", "/api/auth/entra/signout"
        ):
            response = JSONResponse({"detail": "not available"}, status_code=404)
        elif identity_from_easy_auth(request) is None:
            response = JSONResponse({"detail": "authentication required"}, status_code=401)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), usb=(), payment=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    if HTTPS_ENABLED or EASY_AUTH_ENABLED:
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _resolve_static(url_path: str) -> Path | None:
    """Map a URL path to a file inside SITE_ROOT, blocking path traversal."""
    rel = url_path.lstrip("/") or "index.html"
    candidate = (SITE_ROOT / rel).resolve()
    # Never serve the server package itself.
    if candidate == (SITE_ROOT / "server").resolve() or str(candidate).startswith(
        str((SITE_ROOT / "server").resolve())
    ):
        return None
    try:
        candidate.relative_to(SITE_ROOT)
    except ValueError:
        return None  # escaped the site root
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate if candidate.is_file() else None


@app.get("/{full_path:path}")
async def serve_gallery(
    request: Request,
    full_path: str,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> Response:
    path = "/" + full_path
    if is_customer_distribution() and path == "/updates.json":
        from identity import require_user
        require_user(request)
        return JSONResponse(lifecycle.snapshot(), headers={"Cache-Control": "no-store"})
    if is_customer_distribution() and path == "/catalog.json":
        from identity import require_user
        require_user(request)
        snapshot = setup_github.visible_catalog_snapshot()
        return JSONResponse({key: value for key, value in snapshot.items() if key != "capacity"} if snapshot else {"items": []},
                            headers={"Cache-Control": "no-store"})
    if is_customer_distribution() and path == "/capacity.json":
        return JSONResponse(setup_github.capacity_snapshot())

    # 1. OAuth landing: Entra redirects back to "/?code=...&state=..." because the
    #    Azure CLI public client only allows the loopback root as redirect URI.
    #    (Self-hosted mode only — disabled behind Easy Auth.)
    if not EASY_AUTH_ENABLED and path == "/" and (code or error):
        return await finalize_oauth_landing(
            request, code, state, error, error_description, HTTPS_ENABLED
        )

    # 2. Public paths bypass auth.
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        target = _resolve_static(path)
        if target is not None:
            return FileResponse(target)
        return JSONResponse({"detail": "not found"}, status_code=404)

    # 3a. Behind App Service Easy Auth: trust the platform-verified identity in
    #     the request headers. The platform already blocks unauthenticated
    #     requests, so a missing identity here means a misconfiguration — fail
    #     closed with 401 rather than serving content.
    if EASY_AUTH_ENABLED:
        ident = identity_from_easy_auth(request)
        if ident is None or not ident.oid and not ident.upn:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        # Defence in depth: reject users outside our tenant when configured.
        if (
            EXPECTED_TENANT_ID
            and ident.tenant_id.lower() != EXPECTED_TENANT_ID.lower()
        ):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        target = _resolve_static(path)
        if target is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(target)

    # 3b. Self-hosted mode: require a signed-in Entra session. Unauthenticated
    #     users get a sign-in landing page with a "Sign in with Microsoft" button
    #     rather than an automatic
    #     redirect — the button does a top-level navigation in the real browser,
    #     which carries the Windows SSO / device-compliance claims Entra requires.
    rec = app.state.session_store.get(session)
    if rec is None or not rec.signed_in or rec.identity is None:
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    # Defence in depth: reject users outside our tenant when configured. The
    # device-code flow already runs against the tenant authority, so this is a
    # belt-and-braces check mirroring the Easy Auth path.
    if (
        EXPECTED_TENANT_ID
        and rec.identity.tenant_id.lower() != EXPECTED_TENANT_ID.lower()
    ):
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    target = _resolve_static(path)
    if target is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(target)
