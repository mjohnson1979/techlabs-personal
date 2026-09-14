"""One-time, owner-authorized installation of the primary Entra provider."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

import db
import distribution
import setup_auth
import setup_cloud
from setup_config import canonical_guid

COOKIE_NAME = "__Host-portal-install"
INSTALL_PATHS = {"/install", "/assets/install.js", "/assets/install.css", "/assets/styles.css"}
API_PATHS = {"/api/install/unlock", "/api/install/status", "/api/install/entra", "/api/install/finish"}
_LOCK = asyncio.Lock()
_SITE = Path(__file__).resolve().parent.parent


class Unlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=32, max_length=128)


class EntraConnection(setup_auth.SecretStorage):
    tenantId: str
    clientId: str
    secretSettingName: str = Field(default="PORTAL_ENTRA_CLIENT_SECRET", pattern=r"^PORTAL_ENTRA_CLIENT_SECRET$")
    secretStorage: str = Field(default="app_service", pattern=r"^(app_service|key_vault)$")
    clientSecret: SecretStr = Field(default=SecretStr(""), max_length=4096)

    @field_validator("tenantId", "clientId")
    @classmethod
    def guid(cls, value: str) -> str:
        return canonical_guid(value)


def available() -> bool:
    return (distribution.installer_requested()
            and not distribution.installer_configuration_errors()
            and not db.get_setup_setting("installation_complete", False))


def require_installation(request: Request) -> None:
    if not available():
        raise HTTPException(410, "Installation is disabled, expired or already complete")
    if request.headers.get("host", "").lower() != urlsplit(os.environ["GALLERY_PUBLIC_URL"]).netloc.lower():
        raise HTTPException(403, "Installation host does not match")
    if request.method != "GET" and request.headers.get("origin") != os.environ["GALLERY_PUBLIC_URL"]:
        db.record_event(type="installation_origin_rejected")
        raise HTTPException(403, "Same-origin installation request required")


def require_session(request: Request) -> dict:
    require_installation(request)
    session = db.get_setup_setting("installation_session", {})
    cookie = request.cookies.get(COOKIE_NAME, "")
    if (not cookie or len(cookie) > 128 or session.get("expires", 0) <= time.time()
            or session.get("generation") != os.environ["GALLERY_SETUP_CODE_SHA256"]
            or not hmac.compare_digest(session.get("digest", ""), hashlib.sha256(cookie.encode()).hexdigest())):
        raise HTTPException(401, "Unlock installation with a new setup code")
    return session


async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    async def bounded_content() -> bytearray:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 16384:
                raise HTTPException(413, "Installation request exceeds the size limit")
        return content

    try:
        content = await asyncio.wait_for(bounded_content(), timeout=10)
    except TimeoutError:
        raise HTTPException(408, "Installation request timed out") from None
    try:
        return model.model_validate_json(content)
    except ValidationError:
        raise HTTPException(422, "Invalid installation fields; check IDs, credential storage and required values") from None


def provider_document(current: dict, connection: dict, *, enforce: bool) -> dict:
    properties = copy.deepcopy(current.get("properties", {}))
    if not properties.get("platform", {}).get("enabled"):
        raise ValueError("Enable App Service Authentication before opening installation")
    properties["identityProviders"] = {"azureActiveDirectory": {
        "enabled": True,
        "registration": {
            "clientId": connection["clientId"],
            "clientSecretSettingName": connection["secretSettingName"],
            "openIdIssuer": f"https://login.microsoftonline.com/{connection['tenantId']}/v2.0",
        },
        "validation": {"allowedAudiences": [connection["clientId"]]},
    }}
    properties["globalValidation"] = {
        "requireAuthentication": enforce,
        "unauthenticatedClientAction": "RedirectToLoginPage" if enforce else "AllowAnonymous",
        "redirectToProvider": "azureActiveDirectory",
        "excludedPaths": [],
    }
    properties["httpSettings"] = {"requireHttps": True}
    properties["login"] = {"tokenStore": {"enabled": False}}
    return {"properties": properties}


async def configure(connection: EntraConnection) -> dict:
    if db.list_users() or db.get_setup_setting("installation_verified"):
        raise ValueError("An existing installation cannot be replaced")
    path = setup_auth.auth_path()
    site_path = path.split("/config/", 1)[0]
    host = await setup_cloud.arm_request(site_path + "?api-version=2024-11-01")
    if str(host.get("id", "")).lower() != site_path.lower():
        raise ValueError("Host resource could not be verified")
    if urlsplit(os.environ["GALLERY_PUBLIC_URL"]).hostname not in host.get("properties", {}).get("hostNames", []):
        raise ValueError("Installation origin is not a hostname on this App Service")
    subscription_id = canonical_guid(site_path.split("/")[2])
    subscription = await setup_cloud.arm_request(f"/subscriptions/{subscription_id}?api-version=2022-12-01")
    if (str(subscription.get("subscriptionId", "")).lower() != subscription_id
            or str(subscription.get("tenantId", "")).lower() != connection.tenantId):
        raise ValueError("Entra tenant must own the hosting subscription")
    current = await setup_cloud.arm_request(path)
    previous = db.get_setup_setting("installation_pending", {})
    providers = current.get("properties", {}).get("identityProviders", {})
    for name, provider in providers.items():
        if name == "customOpenIdConnectProviders":
            if provider:
                raise ValueError("An existing identity provider cannot be replaced by installation")
        elif any(provider.get("registration", {}).values()):
            if name != "azureActiveDirectory" or provider["registration"].get("clientId") != previous.get("clientId"):
                raise ValueError("An existing identity provider cannot be replaced by installation")
    saved = connection.model_dump(exclude={"clientSecret"})
    document = provider_document(current, saved, enforce=False)
    app_path = site_path + "/config/appsettings"
    settings = await setup_cloud.arm_request(app_path + "/list?api-version=2024-11-01", method="POST")
    values = settings.get("properties", {})
    secret = connection.clientSecret.get_secret_value()
    if secret:
        if values.get(connection.secretSettingName):
            raise ValueError("Credential setting already exists; leave the secret blank to use it")
        if connection.secretStorage == "key_vault":
            uri = await setup_auth.key_vault_target(connection, host, tenant_id=connection.tenantId)
            if await setup_auth.key_vault_secret(uri) is not None:
                raise ValueError("Key Vault secret already exists; leave the secret blank to use it")
            metadata = await setup_auth.key_vault_secret(uri, value=secret, tags={"portalPurpose": "entra-install"})
            if metadata is None:
                raise ValueError("Key Vault did not confirm credential storage")
            values[connection.secretSettingName] = f"@Microsoft.KeyVault(SecretUri={metadata['id']})"
        else:
            values[connection.secretSettingName] = secret
        await setup_cloud.arm_request(app_path + "?api-version=2024-11-01", method="PUT", body={"properties": values}, etag=settings.get("etag"))
    else:
        await setup_auth.bind_existing_secret(connection, site_path, host, tenant_id=connection.tenantId)
    db.set_setup_setting("installation_pending", saved)
    await setup_cloud.arm_request(path, method="PUT", body=document, etag=current.get("etag"))
    db.record_event(type="installation_entra_configured")
    return saved


def verified_oid(request: Request, pending: dict) -> str:
    try:
        encoded = request.headers.get("x-ms-client-principal", "")
        if not encoded or len(encoded) > 32768:
            raise ValueError
        principal = json.loads(base64.b64decode(encoded, validate=True))
        if principal.get("auth_typ") != "aad":
            raise ValueError
        claims = {entry["typ"]: entry["val"] for entry in principal["claims"]}
        tenant = claims.get("tid") or claims.get("http://schemas.microsoft.com/identity/claims/tenantid")
        oid = claims.get("oid") or claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier", "")
        if (canonical_guid(tenant) != pending["tenantId"] or claims.get("aud") != pending["clientId"]
                or claims.get("iss") != f"https://login.microsoftonline.com/{pending['tenantId']}/v2.0"):
            raise ValueError
        return canonical_guid(oid)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(403, "Sign in to the configured Entra application from this setup browser first") from None


async def finish(request: Request) -> dict:
    pending = db.get_setup_setting("installation_pending", {})
    if not pending:
        raise HTTPException(409, "Connect Entra before finishing installation")
    path = setup_auth.auth_path()
    current = await setup_cloud.arm_request(path)
    properties = current.get("properties", {})
    provider = properties.get("identityProviders", {}).get("azureActiveDirectory", {})
    expected = provider_document(current, pending, enforce=False)["properties"]["identityProviders"]["azureActiveDirectory"]
    if (provider.get("enabled") is not True or provider.get("registration", {}).get("clientId") != expected["registration"]["clientId"]
            or provider.get("registration", {}).get("openIdIssuer") != expected["registration"]["openIdIssuer"]
            or provider.get("registration", {}).get("clientSecretSettingName") != pending["secretSettingName"]
            or provider.get("validation", {}).get("allowedAudiences") != [pending["clientId"]]
            or not properties.get("httpSettings", {}).get("requireHttps")):
        raise ValueError("App Service Authentication does not match the pending connection")
    oid = verified_oid(request, pending)
    verified = db.get_setup_setting("installation_verified", {})
    if verified and verified.get("adminOid") != oid:
        raise HTTPException(403, "Only the verified installation owner may finish")
    db.set_setup_setting("installation_verified", {**pending, "adminOid": oid})
    await setup_cloud.arm_request(path, method="PUT", body=provider_document(current, pending, enforce=True), etag=current.get("etag"))
    app_path = path.split("/config/", 1)[0] + "/config/appsettings"
    settings = await setup_cloud.arm_request(app_path + "/list?api-version=2024-11-01", method="POST")
    values = settings.get("properties", {})
    values.update({
        "GALLERY_ENTRA_TENANT_ID": pending["tenantId"], "GALLERY_ENTRA_CLIENT_ID": pending["clientId"],
        "GALLERY_ADMIN_OIDS": oid, "GALLERY_EASY_AUTH": "true", "GALLERY_HTTPS": "true",
        "GALLERY_INSTALLER_ENABLED": "false",
        "GALLERY_MANAGED_IDENTITY": os.environ.get("GALLERY_MANAGED_IDENTITY", "user"),
    })
    values.pop("GALLERY_SETUP_SECRET", None)
    values.pop("GALLERY_SETUP_CODE_SHA256", None)
    values.pop("GALLERY_SETUP_EXPIRES_AT", None)
    await setup_cloud.arm_request(app_path + "?api-version=2024-11-01", method="PUT", body={"properties": values}, etag=settings.get("etag"))
    db.set_setup_setting("installation_complete", True)
    db.set_setup_setting("installation_session", {})
    db.record_event(type="installation_completed", user_oid=oid)
    return {"status": "complete", "redirectUrl": "/"}


def build_installer_router() -> APIRouter:
    router = APIRouter()

    @router.get("/install")
    @router.get("/assets/install.js")
    @router.get("/assets/install.css")
    async def page(request: Request):
        require_installation(request)
        relative = "install.html" if request.url.path == "/install" else request.url.path.lstrip("/")
        return FileResponse(_SITE / relative)

    @router.post("/api/install/unlock")
    async def unlock(request: Request):
        require_installation(request)
        async with _LOCK:
            now = time.time()
            attempts = [stamp for stamp in db.get_setup_setting("installation_attempts", []) if stamp > now - 60]
            if len(attempts) >= 10:
                db.record_event(type="installation_rate_limited")
                raise HTTPException(429, "Too many attempts; retry in one minute", headers={"Retry-After": "60"})
            db.set_setup_setting("installation_attempts", [*attempts, now])
            body = await read_body(request, Unlock)
            digest = hashlib.sha256(body.code.get_secret_value().encode()).hexdigest()
            if not hmac.compare_digest(digest, os.environ["GALLERY_SETUP_CODE_SHA256"]):
                db.record_event(type="installation_unlock_denied")
                raise HTTPException(401, "Invalid setup code")
            previous = db.get_setup_setting("installation_session", {})
            if previous.get("generation") == digest:
                raise HTTPException(409, "Setup code already used; continue in the original browser or rotate the code in host settings")
            if db.list_users():
                raise HTTPException(409, "An existing installation cannot be claimed")
            token = secrets.token_urlsafe(32)
            expires = min(now + 1800, datetime.fromisoformat(os.environ["GALLERY_SETUP_EXPIRES_AT"]).timestamp())
            db.set_setup_setting("installation_session", {
                "digest": hashlib.sha256(token.encode()).hexdigest(), "generation": digest, "expires": expires,
            })
            db.record_event(type="installation_unlocked")
            response = JSONResponse({"unlocked": True})
            response.set_cookie(COOKIE_NAME, token, secure=True, httponly=True, samesite="lax", max_age=int(expires - now), path="/")
            return response

    @router.get("/api/install/status")
    async def status(request: Request):
        require_session(request)
        pending = db.get_setup_setting("installation_pending", {})
        return {
            "connection": pending, "callbackUrl": os.environ["GALLERY_PUBLIC_URL"] + "/.auth/login/aad/callback",
            "loginUrl": "/.auth/login/aad?post_login_redirect_uri=/install",
        }

    @router.post("/api/install/entra")
    @router.post("/api/install/finish")
    async def change(request: Request):
        async with _LOCK:
            require_session(request)
            try:
                if request.url.path.endswith("/finish"):
                    return await finish(request)
                body = await read_body(request, EntraConnection)
                return {"connection": await configure(body)}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            except httpx.HTTPError:
                db.record_event(type="installation_cloud_operation_failed")
                raise HTTPException(502, "Azure operation failed. Check host, Entra and credential storage permissions; reconnect with the secret blank if it was already stored. Installation remains locked.") from None

    return router