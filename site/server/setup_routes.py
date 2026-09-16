"""Administrator-only hosted setup endpoints."""

from __future__ import annotations

import asyncio
import csv
import os
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

import db
import setup_cloud
import setup_config
import setup_github
import catalog_processing
import setup_auth
import github_device
import distribution
from identity import CurrentUser, require_admin, require_user

_SETUP_LOCK = asyncio.Lock()


def setup_origin(request: Request) -> None:
    origin = request.headers.get("origin", "")
    if not origin or urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="same-origin setup request required")


class SubscriptionInput(BaseModel):
    subscriptions: list[setup_config.SubscriptionSelection] = Field(min_length=1, max_length=200)


class CSVInput(BaseModel):
    csv: str = Field(max_length=131072)


class IdentityRole(BaseModel):
    oid: str = Field(min_length=1, max_length=150)
    role: str = Field(pattern=r"^(admin|guest)$")


class GitHubFlowInput(BaseModel):
    flowId: str = Field(min_length=20, max_length=100)


class GitHubOAuthClient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    confirm: bool = False


class WorkflowConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class BrandingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brandName: str = Field(min_length=1, max_length=80)
    footerText: str = Field(max_length=500)

    @field_validator("brandName", "footerText")
    @classmethod
    def plain_text(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Use plain text without control characters")
        return value.strip()

    @field_validator("brandName")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Enter a site name")
        return value


async def resolve_subscriptions(selections: list[setup_config.SubscriptionSelection]) -> list[dict[str, str]]:
    items = setup_config.validate_subscriptions(selections)
    semaphore = asyncio.Semaphore(4)

    async def resolve(item: dict[str, str]) -> dict[str, str]:
        async with semaphore:
            return await setup_cloud.verify_subscription(item)

    return await asyncio.wait_for(
        asyncio.gather(*(resolve(item) for item in items)), timeout=120
    )


def lookup_failure() -> HTTPException:
    return HTTPException(status_code=502, detail="Subscription lookup failed; check the app managed identity's Reader access. No changes were saved.")


class ProcessingInput(catalog_processing.ProcessingSettings):
    confirm: bool = False


def build_setup_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/subscriptions")
    async def available_subscriptions(user: CurrentUser = Depends(require_user)) -> dict:
        return {"subscriptions": setup_config.subscriptions()}

    @router.get("/admin/setup")
    async def status(user: CurrentUser = Depends(require_admin)) -> dict:
        return {
            "branding": distribution.branding(),
            "azureTenantId": os.environ.get("GALLERY_ENTRA_TENANT_ID", ""),
            "primaryAuthentication": {
                "clientId": os.environ.get("GALLERY_ENTRA_CLIENT_ID", ""),
                "verified": bool(db.get_setup_setting("installation_verified")),
            },
            "subscriptions": setup_config.subscriptions(),
            "authentication": db.get_setup_setting("authentication", {}),
            "createdRegistration": db.get_setup_setting("created_entra_registration", {}),
            "github": db.get_setup_setting("github", {"enabled": False}),
            "githubCredentialStorage": db.get_setup_setting("github_credential", {"secretStorage": "app_service"}),
            "githubCredentialConfigured": bool(os.environ.get("GALLERY_GITHUB_TOKEN", "")) and not os.environ.get("GALLERY_GITHUB_TOKEN", "").startswith("@Microsoft.KeyVault("),
            "githubDeviceConfigured": bool(github_device.client_id()),
            "githubOAuthClientId": github_device.client_id(),
            "githubConnected": bool(github_device.access_token()),
            "githubConnection": db.get_setup_setting("github_connection", {}),
            "catalogVersion": setup_github.catalog_snapshot().get("version"),
            "syncError": db.get_setup_setting("github_sync_error"),
            "processing": catalog_processing.settings(),
            "processingEffective": db.get_setup_setting("processing_effective", {"mode": "container"}),
            "processingJob": db.get_setup_setting("catalog_job", {"status": "idle"}),
            "runnerProbe": db.get_setup_setting("runner_probe", {}),
        }

    @router.put("/admin/setup/branding")
    async def save_branding(body: BrandingInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            db.set_setup_setting("branding", body.model_dump())
            db.record_event(type="setup_branding_saved", user_oid=user.oid)
        return distribution.branding()

    @router.put("/admin/setup/user-role")
    async def user_role(body: IdentityRole, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        if user.oid == body.oid and body.role != "admin":
            raise HTTPException(status_code=400, detail="cannot demote yourself")
        try:
            db.set_user_role_by_oid(body.oid, body.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        db.record_event(type="admin_role_change", user_oid=user.oid, meta={"targetOid": body.oid, "role": body.role})
        return {"saved": True}

    @router.put("/admin/setup/github")
    async def github(body: setup_github.GitHubSettings, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try:
            async with _SETUP_LOCK:
                result = await setup_github.configure(body)
            db.record_event(type="setup_github_configured", user_oid=user.oid)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=setup_github.repository_failure(exc, body.create)) from None

    @router.put("/admin/setup/github/oauth-client")
    async def github_oauth_client(body: GitHubOAuthClient, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        if not body.confirm:
            raise HTTPException(400, "Confirm the organization-owned GitHub OAuth App")
        async with _SETUP_LOCK:
            if body.clientId != github_device.client_id() and (
                github_device.access_token() or github_device._flows
                or db.get_setup_setting("github", {}).get("enabled")
            ):
                raise HTTPException(409, "An active GitHub connection cannot switch OAuth applications; revoke and remove the existing connection first")
            db.set_setup_setting("github_oauth_client", body.clientId)
            db.record_event(type="setup_github_oauth_client_saved", user_oid=user.oid)
        return {"clientId": body.clientId, "configured": True}

    @router.post("/admin/setup/github/device/start")
    async def github_start(body: setup_auth.GitHubCredential, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            if db.get_setup_setting("github", {}).get("enabled"):
                raise HTTPException(status_code=409, detail="Disable the current GitHub repository connection before signing in again")
            try:
                if not body.confirm:
                    raise ValueError("Confirm GitHub authorization and server-side token storage")
                setup_auth.auth_path()
                return await github_device.start(user.oid, body.model_dump(exclude={"confirm"}))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    detail = "GitHub rate-limited device sign-in (HTTP 429). Wait before selecting Connect GitHub again."
                elif status >= 500:
                    detail = f"GitHub device authorization is unavailable (HTTP {status}). Retry later; no connection was saved."
                else:
                    detail = f"GitHub rejected device sign-in (HTTP {status}). Verify the saved OAuth App Client ID, Enable Device Flow, and organization access policy. No connection was saved."
                raise HTTPException(status_code=502, detail=detail) from None
            except httpx.TimeoutException:
                raise HTTPException(status_code=502, detail="The portal timed out contacting GitHub. Check the App Service's outbound HTTPS access to github.com, then retry Connect GitHub.") from None
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="The portal could not reach GitHub device authorization. Check the host's DNS, outbound HTTPS and proxy/TLS configuration for github.com; do not disable certificate validation.") from None

    @router.post("/admin/setup/github/device/poll")
    async def github_poll(body: GitHubFlowInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try:
            result = await github_device.poll(user.oid, body.flowId)
            if result["status"] != "authorized": return result
            async with _SETUP_LOCK:
                flow = github_device.get_flow(user.oid, body.flowId)
                async with flow.lock:
                    if db.get_setup_setting("github", {}).get("enabled"):
                        raise ValueError("Disable the current repository connection before saving a new GitHub sign-in")
                    account = await setup_github.github_account(token=flow.token)
                    async with setup_github._SYNC_LOCK:
                        await setup_auth.store_github_device_token(flow.token, flow.token_expires_at, account, flow.storage)
                    github_device._flows.pop(body.flowId, None)
                    db.record_event(type="setup_github_device_connected", user_oid=user.oid)
                    return {"status": "connected", "account": account, "expiresAt": flow.token_expires_at or None}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="GitHub sign-in or server-side token storage failed; check the configured host/Key Vault permissions and retry. No token is exposed.") from None

    @router.post("/admin/setup/github/device/cancel")
    async def github_cancel(body: GitHubFlowInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try: github_device.cancel(user.oid, body.flowId)
        except ValueError: pass
        return {"cancelled": True}

    @router.get("/admin/setup/github/discover")
    async def github_discover(user: CurrentUser = Depends(require_admin)) -> dict:
        try:
            return await setup_github.discover_repositories()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Could not list GitHub repositories. Reconnect if authorization expired; organization policy or SSO may restrict access.") from None

    @router.post("/admin/setup/authentication/register")
    async def register(body: setup_auth.EntraRegistration, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            try:
                result = await setup_auth.create_entra_registration(body)
                db.record_event(type="setup_entra_registration_created", user_oid=user.oid)
                return result
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="Entra registration or credential storage failed. Check Graph application permissions, host write access and, if selected, Key Vault secret access. The chosen storage was not replaced with a fallback; inspect setup state before retrying.") from None

    @router.put("/admin/setup/github/credential")
    async def github_credential(body: setup_auth.GitHubCredential, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            try:
                result = await setup_auth.configure_github_credential(body)
                db.record_event(type="setup_github_credential_configured", user_oid=user.oid)
                return result
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="Credential configuration failed. Check host settings access and Key Vault permissions/network access. The selected storage was not replaced with a fallback.") from None

    @router.put("/admin/setup/authentication")
    async def authentication(body: setup_auth.AuthenticationSettings, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            try:
                result = await setup_auth.apply_provider(body)
                db.record_event(type="setup_auth_provider_added", user_oid=user.oid)
                return result
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="Authentication or credential reference configuration failed. Check host write access and, if selected, Key Vault secret access. The bootstrap provider is not intentionally replaced.") from None

    @router.post("/admin/setup/github/workflows")
    async def workflow_pr(body: WorkflowConfirmation, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        if body.confirm:
            now = time.time()
            if now - db.get_setup_setting("workflow_pr_requested_at", 0) < 60:
                raise HTTPException(status_code=429, detail="Wait one minute before requesting another workflow PR.", headers={"Retry-After": "60"})
            db.set_setup_setting("workflow_pr_requested_at", now)
        try:
            result = await asyncio.wait_for(setup_github.create_workflow_pr(body.confirm), timeout=120)
            db.record_event(type="setup_workflow_pr", user_oid=user.oid, meta={"status": result["status"]})
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except (httpx.HTTPError, KeyError, OSError, TimeoutError):
            raise HTTPException(status_code=502, detail="Workflow PR could not be completed. Check repository write/workflow permission and branch policy. Inspect existing portal/workflows branches and PRs before retrying; nothing was merged or deleted.") from None

    @router.get("/admin/setup/github/processing")
    async def processing_status(user: CurrentUser = Depends(require_admin)) -> dict:
        return {"settings": catalog_processing.settings(), "effective": db.get_setup_setting("processing_effective", {"mode": "container"}),
                "job": db.get_setup_setting("catalog_job", {"status": "idle"}), "probe": db.get_setup_setting("runner_probe", {})}

    @router.post("/admin/setup/github/runners/probe")
    async def runner_probe(request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        now = time.time()
        if now - db.get_setup_setting("runner_probe_requested_at", 0) < 60:
            raise HTTPException(429, "Wait one minute before checking runners again.")
        db.set_setup_setting("runner_probe_requested_at", now)
        return await setup_github.processing_probe(force=True)

    @router.put("/admin/setup/github/processing")
    async def save_processing(body: ProcessingInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        if not body.confirm:
            raise HTTPException(400, "Confirm the processing location and capacity regions.")
        configuration = body.model_dump(exclude={"confirm"})
        if body.mode != "container":
            probe = await setup_github.processing_probe(force=True)
            if not catalog_processing.runner_available(configuration, probe):
                raise HTTPException(400, "Runner availability could not be verified for this repository and selection. Choose Portal container.")
        async with _SETUP_LOCK:
            async with setup_github._SYNC_LOCK:
                db.set_setup_setting("catalog_processing", configuration)
                db.record_event(type="setup_processing_saved", user_oid=user.oid, meta={"mode": body.mode})
        if db.get_setup_setting("github", {}).get("enabled"):
            setup_github.queue_catalog_refresh()
        return configuration

    @router.post("/admin/setup/github/sync")
    async def sync(request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try:
            return setup_github.queue_catalog_refresh()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @router.post("/admin/setup/subscriptions/parse")
    async def parse_csv(body: CSVInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try:
            selections = setup_config.parse_subscriptions_csv(body.csv.encode())
        except (ValueError, csv.Error, UnicodeError):
            raise HTTPException(status_code=400, detail="Invalid CSV: use subscriptionId and optional pricingMode; subscription IDs must be unique GUIDs") from None
        try:
            return {"subscriptions": await resolve_subscriptions(selections)}
        except ValueError:
            raise HTTPException(status_code=400, detail="A subscription could not be verified as enabled in the organization Azure tenant; no changes were saved") from None
        except (httpx.HTTPError, TimeoutError):
            raise lookup_failure() from None

    @router.post("/admin/setup/subscriptions/resolve")
    async def resolve_subscription(body: setup_config.SubscriptionSelection, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        try:
            return {"subscription": (await resolve_subscriptions([body]))[0]}
        except ValueError:
            raise HTTPException(status_code=400, detail="Subscription could not be verified as enabled in the organization Azure tenant") from None
        except (httpx.HTTPError, TimeoutError):
            raise lookup_failure() from None

    @router.put("/admin/setup/subscriptions")
    async def save_subscriptions(body: SubscriptionInput, request: Request, user: CurrentUser = Depends(require_admin)) -> dict:
        setup_origin(request)
        async with _SETUP_LOCK:
            try:
                items = setup_config.validate_subscriptions(body.subscriptions)
                new_ids = {item["subscriptionId"] for item in items}
                removed = {item["subscriptionId"] for item in setup_config.subscriptions()} - new_ids
                if removed:
                    raise ValueError("Existing subscriptions cannot be removed while cleanup records may refer to them")
                items = await resolve_subscriptions(body.subscriptions)
                db.set_setup_setting("subscriptions", items)
                db.record_event(type="setup_subscriptions_saved", user_oid=user.oid)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            except (httpx.HTTPError, TimeoutError):
                raise lookup_failure() from None
        return {"subscriptions": items}

    @router.get("/subscriptions/{subscription_id}/locations")
    async def locations(subscription_id: str, user: CurrentUser = Depends(require_user)) -> dict:
        try:
            item = setup_config.subscription_for(subscription_id)
        except ValueError:
            raise HTTPException(status_code=403, detail="subscription is not configured") from None
        try:
            return {"locations": await setup_cloud.subscription_locations(item["subscriptionId"])}
        except (ValueError, httpx.HTTPError):
            raise HTTPException(status_code=502, detail="Region lookup unavailable; check managed identity Reader access") from None

    @router.get("/subscriptions/{subscription_id}/capacity")
    async def capacity(subscription_id: str, location: str = Query(pattern=r"^[a-z][a-z0-9]{2,30}$"), user: CurrentUser = Depends(require_user)) -> dict:
        try:
            item = setup_config.subscription_for(subscription_id)
        except ValueError:
            raise HTTPException(status_code=403, detail="subscription is not configured") from None
        try:
            return await setup_cloud.compute_capacity(item["subscriptionId"], location)
        except (ValueError, httpx.HTTPError):
            return {"subscriptionId": subscription_id, "location": location, "quotas": [], "physicalCapacity": "unknown", "detail": "Quota lookup unavailable. Check managed identity Reader access; this does not mean unlimited capacity."}

    return router