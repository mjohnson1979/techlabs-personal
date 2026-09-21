"""Gallery control-plane API: identity, telemetry, and deployments.

The backend is the system of record AND the executor. It records who deployed
what and when, schedules teardown at the chosen end time, and runs the actual
Azure deployments in-process using the App Service managed identity (see
azure_deploy.py). No GitHub PAT, no stored Azure secret.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

import azure_deploy as az
import db
import catalog_processing
from distribution import branding, is_customer_distribution
from entra_auth import (
    COOKIE_NAME,
    EntraAuthError,
    acquire_external_arm_token_for_subscription,
)
import github_intake
import pricing
import setup_cloud
import setup_config
import submissions
from identity import (
    CurrentUser,
    require_admin,
    require_user,
    verify_same_origin,
)

SITE_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SITE_ROOT / "catalog.json"
REGISTRY_PATH = SITE_ROOT / "registry.json"

DEFAULT_SUBSCRIPTION = os.environ.get(
    "GALLERY_SUBSCRIPTION_ID", ""
).strip()
DEFAULT_MAX_LIFETIME_HOURS = int(
    os.environ.get("GALLERY_MAX_LIFETIME_HOURS", "96").strip() or "96"
)
MIN_LIFETIME_MINUTES = 15
_VALID_EVENT_TYPES = {"view", "search", "modal_open"}
_ALLOWED_ENVS = {"dev", "prod"}
_REGIONAL_CHECKS = catalog_processing.REGIONAL_CONCURRENCY

# --------------------------------------------------------------------------- #
# Catalog lookup (validates deploy requests against real use cases)
# --------------------------------------------------------------------------- #
_catalog_cache: dict[str, Any] = {"mtime": 0.0, "items": {}}


def _catalog_items() -> dict[str, dict[str, Any]]:
    """Return {path: item} from catalog.json, reloaded when the file changes."""
    if is_customer_distribution():
        import setup_github
        snapshot = setup_github.visible_catalog_snapshot()
        if snapshot:
            return {item["path"]: item for item in snapshot["items"]}
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _catalog_cache["mtime"]:
        try:
            data = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return _catalog_cache["items"]
        items = {it.get("path", ""): it for it in data.get("items", []) if it.get("path")}
        _catalog_cache["mtime"] = mtime
        _catalog_cache["items"] = items
    return _catalog_cache["items"]


_registry_cache: dict[str, Any] = {"mtime": 0.0, "data": {}}


def _registry() -> dict[str, Any]:
    """Return the model/SKU registry from registry.json, reloaded on change."""
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _registry_cache["mtime"]:
        try:
            _registry_cache["data"] = json.loads(REGISTRY_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return _registry_cache["data"]
        _registry_cache["mtime"] = mtime
    return _registry_cache["data"]


def _sanitize(part: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")


def _resource_group_name(use_case: str, environment: str, short_id: str) -> str:
    base = f"rg-techlabs-{_sanitize(use_case)}-{_sanitize(environment)}-{short_id}"
    return base[:88]  # Azure RG name max length is 90.


def _validate_parameter_values(
    raw: dict[str, str] | None, item: dict[str, Any]
) -> dict[str, str]:
    """Validate advanced deployment options against the use case's declared
    template parameters. Only known parameter names are accepted, and each value
    is checked against the parameter's allowed set / type / min-max bounds. Returns
    a clean name->string map (empty values dropped). Raises 400 on any violation."""
    if not raw:
        raw = {}
    declared = {
        p["name"]: p
        for p in (item.get("parameters") or [])
        if isinstance(p, dict) and p.get("name")
    }
    out: dict[str, str] = {}
    for name, value in raw.items():
        spec = declared.get(name)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"unknown parameter: {name}")
        sval = str(value).strip()
        if sval == "":
            continue
        if len(sval) > 256:
            raise HTTPException(status_code=400, detail=f"parameter {name} value too long")
        ptype = str(spec.get("type") or "string").lower()
        if ptype not in ("string", "int", "bool"):
            raise HTTPException(status_code=400, detail=f"parameter {name} is not overridable")
        allowed = spec.get("allowed")
        if allowed:
            if ptype == "bool":
                sval = sval.lower()
            if sval not in [str(a).lower() if type(a) is bool else str(a) for a in allowed]:
                raise HTTPException(status_code=400, detail=f"invalid value for {name}")
        elif ptype == "int":
            try:
                ival = int(sval)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{name} must be an integer")
            mn, mx = spec.get("min"), spec.get("max")
            if mn is not None and ival < mn:
                raise HTTPException(status_code=400, detail=f"{name} is below minimum {mn}")
            if mx is not None and ival > mx:
                raise HTTPException(status_code=400, detail=f"{name} is above maximum {mx}")
        elif ptype == "bool":
            if sval.lower() not in ("true", "false"):
                raise HTTPException(status_code=400, detail=f"{name} must be true or false")
            sval = sval.lower()
        if not allowed and ptype not in ("int", "bool"):
            fam_vals = _registry_sku_values(name)
            if fam_vals and sval not in fam_vals:
                raise HTTPException(status_code=400, detail=f"invalid value for {name}")
        out[name] = sval
    _validate_model_params(out, item)
    return out


# Model name/version/SKU params carry no template @allowed set (the registry is
# their source of truth). Validate them against registry.json so a stale or
# hand-crafted request can't submit a model the deployment can't create.
_CHAT_NAME_PARAMS = ("chatModelName", "chatModel")


def _openai_models(kind: str) -> list[dict[str, Any]]:
    reg = _registry()
    models = (reg.get("models") or {}) if isinstance(reg, dict) else {}
    return [m for m in (models.get(kind) or []) if m.get("provider") == "OpenAI"]


# The registry SKU family values for a non-model param. Matching is by convention
# (exact name, then the family name as a token, then aliases) so a new use case's
# SKU/tier/size param maps automatically. Returns the union of values, or None.
_SKU_FAMILY_ALIASES = (
    (r"vm.?size|nodesize|hostsize", "vmSize"),
    (r"k8sversion|aksversion|kubernetes", "kubernetesVersion"),
    (r"runtimestack|linuxfx", "linuxFxVersion"),
    (r"sql.*tier", "sqlTier"),
    (r"sql.*sku", "sqlSkuName"),
)


def _sku_family_key(name: str, skus: dict[str, Any]) -> str | None:
    if name in skus:
        return name
    low = name.lower()
    for key in sorted(skus.keys(), key=len, reverse=True):
        if key.lower() in low:
            return key
    for pat, key in _SKU_FAMILY_ALIASES:
        if key in skus and re.search(pat, low):
            return key
    return None


def _registry_sku_values(name: str) -> list[str] | None:
    reg = _registry()
    skus = (reg.get("skus") or {}) if isinstance(reg, dict) else {}
    key = _sku_family_key(name, skus)
    if not key:
        return None
    fam = skus.get(key) or {}
    vals = [str(v.get("value")) for v in (fam.get("values") or []) if v.get("value") is not None]
    return vals or None


def _check_model_triplet(
    models: list[dict[str, Any]], name_param: str, name: str | None,
    version: str | None, sku: str | None,
) -> None:
    if not models or not name:
        return
    if name not in {m.get("name") for m in models}:
        raise HTTPException(status_code=400, detail=f"{name_param}: unknown model '{name}'")
    versions = {m.get("version") for m in models if m.get("name") == name}
    if version and version not in versions:
        raise HTTPException(status_code=400, detail=f"invalid version '{version}' for {name}")
    if sku:
        skus: set[str] = set()
        for m in models:
            if m.get("name") == name and (not version or m.get("version") == version):
                skus.update(m.get("skus") or [])
        if skus and sku not in skus:
            raise HTTPException(status_code=400, detail=f"invalid SKU '{sku}' for {name}")


def _validate_model_params(out: dict[str, str], item: dict[str, Any]) -> None:
    defaults = {
        p["name"]: p.get("default")
        for p in (item.get("parameters") or [])
        if isinstance(p, dict) and p.get("name")
    }

    def eff(name: str) -> str | None:
        if name in out:
            return out[name]
        d = defaults.get(name)
        return None if d is None else str(d)

    chat_param = next((n for n in _CHAT_NAME_PARAMS if n in defaults), None)
    if chat_param and any(n in out for n in (chat_param, "chatModelVersion", "chatModelSku")):
        _check_model_triplet(
            _openai_models("chat"), chat_param,
            eff(chat_param), eff("chatModelVersion"), eff("chatModelSku"),
        )
    if "embeddingModel" in defaults and any(n in out for n in ("embeddingModel", "embeddingModelVersion")):
        _check_model_triplet(
            _openai_models("embedding"), "embeddingModel",
            eff("embeddingModel"), eff("embeddingModelVersion"), None,
        )



def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _deployment_view(d: dict[str, Any]) -> dict[str, Any]:
    cleanup_policy = d.get("cleanup_policy", "delete_resource_group")
    return {
        "id": d["id"],
        "useCase": d["use_case"],
        "environment": d["environment"],
        "userUpn": d["user_upn"],
        "userName": d["user_name"],
        "subscriptionId": d["subscription_id"],
        "resourceGroup": d["resource_group"],
        "status": d["status"],
        "createdAt": _iso(d["created_at"]),
        "endAt": None if cleanup_policy == "none" else _iso(d["end_at"]),
        "destroyedAt": _iso(d.get("destroyed_at")),
        "ghRunUrl": d["gh_run_url"],
        "detail": d["detail"],
        "engine": d.get("engine", "bicep"),
        "cleanupPolicy": cleanup_policy,
        "externallyManaged": cleanup_policy == "none",
        "preflight": (d.get("params") or {}).get("preflight"),
    }


def _submission_view(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s["id"],
        "slug": s["slug"],
        "displayName": s["display_name"],
        "userUpn": s["user_upn"],
        "userName": s["user_name"],
        "status": s["status"],
        "createdAt": _iso(s["created_at"]),
        "files": s.get("files", []),
        "branch": s.get("branch", ""),
        "compareUrl": s.get("compare_url", ""),
        "detail": s["detail"],
    }


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #
class EventIn(BaseModel):
    type: str
    useCase: str = ""
    deploymentId: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class DeployIn(BaseModel):
    useCase: str
    environment: str = "dev"
    # IaC engine to deploy with: "bicep" (ARM) or "terraform".
    engine: str = "bicep"
    # Azure region to deploy into (e.g. "centralus"). Empty falls back to the
    # server default (GALLERY_DEPLOY_LOCATION).
    location: str = ""
    # Either an explicit end time (ISO 8601) or a lifetime in hours.
    endAt: str = ""
    lifetimeHours: float | None = None
    # Optional advanced deployment options: template parameter name -> value
    # (as a string). Validated against the use case's declared parameters and
    # merged into the ARM parameters at deploy time.
    parameterValues: dict[str, str] | None = None
    # External deployments use the signed-in user's delegated ARM token.
    externalSubscription: bool = False
    subscriptionId: str = ""
    resourceGroupName: str = ""


class EstimateIn(BaseModel):
    useCase: str
    subscriptionId: str = ""
    # Region to price for; empty uses the use case's authored cost region.
    location: str = ""
    # Advanced-option overrides (param name -> value) that shift SKU/quantity.
    parameterValues: dict[str, str] | None = None


class RoleIn(BaseModel):
    upn: str
    role: str  # 'admin' | 'guest'


class CatalogVisibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=3, max_length=150)
    version: str = Field(pattern=r"^[a-f0-9]{40}$")
    removed: bool = Field(strict=True)
    confirm: bool = Field(default=False, strict=True)


class CatalogRepositoryRemovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_from_repository"]
    repository: str = Field(min_length=3, max_length=150)
    version: str = Field(pattern=r"^[a-f0-9]{40}$")
    branch: str = Field(min_length=1, max_length=100)
    sourceCommit: str = Field(pattern=r"^[a-f0-9]{40}$")
    confirm: bool = Field(default=False, strict=True)


def _terraform_exec_available() -> bool:
    """Whether the server can run Terraform in-app (binary resolvable). The
    manual CLI snippet is always offered; only the one-click deploy depends on
    this, so the UI can degrade to copy-the-CLI until the host is provisioned."""
    try:
        import terraform_exec

        ok, _ = terraform_exec.terraform_available()
        return ok
    except Exception:
        return False


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["gallery"])

    # ----------------------------------------------------------------- me ---
    @router.get("/me")
    async def me(user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
        mine = db.list_deployments(user_oid=user.oid, limit=200)
        active = sum(
            1 for d in mine if d["status"] in ("requested", "deploying", "active")
        )
        return {
            "signedIn": True,
            **branding(),
            "customerOperated": is_customer_distribution(),
            "subscriptions": setup_config.subscriptions() if is_customer_distribution() else [],
            "setupRequired": is_customer_distribution() and not setup_config.subscriptions(),
            "upn": user.upn,
            "name": user.name,
            "oid": user.oid,
            "role": user.role,
            "isAdmin": user.is_admin,
            "deploymentCount": len(mine),
            "activeDeployments": active,
            "maxLifetimeHours": DEFAULT_MAX_LIFETIME_HOURS,
            "terraformExecAvailable": _terraform_exec_available(),
        }

    @router.get("/catalog/sync")
    async def catalog_sync_status(user: CurrentUser = Depends(require_user)) -> JSONResponse:
        import setup_github
        status = setup_github.catalog_refresh_status() if is_customer_distribution() else {"status": "disabled"}
        return JSONResponse(status, headers={"Cache-Control": "no-store"})

    @router.post("/catalog/sync")
    async def request_catalog_sync(request: Request, user: CurrentUser = Depends(require_user)) -> JSONResponse:
        import setup_github
        if not (request.headers.get("origin") or request.headers.get("referer")):
            raise HTTPException(status_code=403, detail="browser origin required")
        verify_same_origin(request)
        status = setup_github.catalog_refresh_status() if is_customer_distribution() else {"status": "disabled"}
        if status["status"] not in ("disabled", "not_configured"):
            job = setup_github.queue_catalog_refresh(force=False)
            status = {"status": job["status"], "retryAfter": job.get("retryAfter", 2)}
        return JSONResponse(status, status_code=202 if status["status"] in ("queued", "running") else 200,
                            headers={"Cache-Control": "no-store"})

    # -------------------------------------------------------------- events ---
    @router.post("/events", status_code=204)
    async def post_event(
        body: EventIn,
        request: Request,
        user: CurrentUser = Depends(require_user),
    ) -> None:
        verify_same_origin(request)
        if is_customer_distribution():
            return
        if body.type not in _VALID_EVENT_TYPES:
            raise HTTPException(status_code=400, detail="unsupported event type")
        use_case = body.useCase[:120]
        if use_case and use_case not in _catalog_items():
            use_case = ""  # ignore unknown use-case tags but still record the event
        db.record_event(
            type=body.type,
            user_oid=user.oid,
            user_upn=user.upn,
            use_case=use_case,
            meta={k: str(v)[:200] for k, v in (body.meta or {}).items()},
        )

    # -------------------------------------------------------- estimate ---
    @router.get("/lifecycle")
    async def lifecycle_status(response: Response, user: CurrentUser = Depends(require_user)) -> dict:
        import lifecycle
        response.headers["Cache-Control"] = "no-store"
        return lifecycle.snapshot()

    @router.get("/regions/snapshot")
    async def regional_snapshot(useCase: str = "", subscriptionId: str = "", user: CurrentUser = Depends(require_user)) -> dict:
        subscriptions = setup_config.subscriptions()
        if subscriptionId:
            try:
                subscriptions = [setup_config.subscription_for(subscriptionId)]
            except ValueError:
                raise HTTPException(status_code=403, detail="Select a configured organization subscription") from None
        items = list(_catalog_items().values())
        if useCase:
            items = [item for item in items if item.get("path") == useCase]
        return catalog_processing.regional_results(items, subscriptions)

    @router.post("/regions/check")
    async def regional_check(body: EstimateIn, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
        verify_same_origin(request)
        try:
            subscription = setup_config.subscription_for(body.subscriptionId)
        except ValueError:
            raise HTTPException(status_code=403, detail="Select a configured organization subscription") from None
        region = (body.location or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region):
            raise HTTPException(status_code=400, detail="Select an Azure region")
        item = _catalog_items().get(body.useCase)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown workload")
        if _REGIONAL_CHECKS.locked():
            raise HTTPException(status_code=429, detail="Regional checks are busy; retry shortly.", headers={"Retry-After": "5"})
        parameters = {str(key)[:64]: str(value)[:128] for key, value in list((body.parameterValues or {}).items())[:50]}
        async with _REGIONAL_CHECKS:
            try:
                locations = await setup_cloud.subscription_locations(subscription["subscriptionId"])
            except (ValueError, httpx.HTTPError, AzureError):
                raise HTTPException(status_code=502, detail="Subscription regions unavailable; verify managed identity access.") from None
            if region not in {entry["name"] for entry in locations}:
                raise HTTPException(status_code=400, detail="Region is not listed for the selected subscription")
            return await catalog_processing.check_region(item, subscription, region, parameters)

    @router.post("/estimate")
    async def estimate_cost(body: EstimateIn, request: Request) -> dict[str, Any]:
        # Unauthenticated, so require a browser Origin/Referer (verify_same_origin
        # allows header-less server-to-server calls, which we don't want here).
        if not (request.headers.get("origin") or request.headers.get("referer")):
            raise HTTPException(status_code=403, detail="browser origin required")
        verify_same_origin(request)
        selected = None
        if is_customer_distribution():
            require_user(request)
            try:
                selected = setup_config.subscription_for(body.subscriptionId)
            except ValueError:
                raise HTTPException(status_code=403, detail="select a configured organization subscription") from None
        item = _catalog_items().get(body.useCase)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown use case")
        cost = item.get("cost")
        if not cost or not cost.get("components"):
            raise HTTPException(status_code=404, detail="no cost model for this use case")
        region = (body.location or "").strip().lower()
        if region and not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region):
            raise HTTPException(status_code=400, detail="invalid location")
        pv: dict[str, str] = {}
        for k, v in list((body.parameterValues or {}).items())[:50]:
            pv[str(k)[:64]] = str(v)[:128]
        try:
            defaults = catalog_processing.default_parameters(item)
            parameters = {**defaults, **pv}
            if selected:
                result = await pricing.estimate_for_subscription(cost, region or str(cost.get("region") or ""), parameters, selected)
            else:
                result = await pricing.estimate(cost, region or str(cost.get("region") or ""), parameters)
            result["unpricedParameters"] = pricing.unpriced_parameters(cost, pv, defaults)
            return result
        except Exception as exc:  # network/parse failure -> caller keeps static cost
            raise HTTPException(status_code=502, detail="pricing lookup failed") from exc

    # --------------------------------------------------------- deployments ---
    @router.post("/deployments")
    async def create_deployment(
        body: DeployIn,
        request: Request,
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        verify_same_origin(request)
        if is_customer_distribution():
            if body.externalSubscription:
                raise HTTPException(status_code=403, detail="only the configured organization subscription is allowed")
            try:
                selected_subscription = setup_config.subscription_for(body.subscriptionId)
            except ValueError:
                raise HTTPException(status_code=403, detail="select a configured organization subscription") from None
        else:
            selected_subscription = None
        if not selected_subscription and not DEFAULT_SUBSCRIPTION and not body.externalSubscription:
            raise HTTPException(status_code=503, detail="deployment subscription is not configured")
        items = _catalog_items()
        item = items.get(body.useCase)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown use case")
        if item.get("deployReady") is False:
            raise HTTPException(status_code=409, detail="Organization runtime inputs are missing from the repository")
        env = body.environment.lower().strip()
        if env not in _ALLOWED_ENVS:
            raise HTTPException(status_code=400, detail="environment must be dev or prod")
        if env == "prod" and not item.get("hasProd", False):
            raise HTTPException(
                status_code=400, detail="this use case has no prod parameters"
            )

        engine = body.engine.lower().strip() or "bicep"
        if engine not in ("bicep", "terraform"):
            raise HTTPException(status_code=400, detail="engine must be bicep or terraform")
        if engine not in (item.get("engines") or ["bicep"]):
            raise HTTPException(
                status_code=400,
                detail="this use case has no Terraform module" if engine == "terraform" else "this use case has no Bicep template",
            )
        if engine == "terraform" and not _terraform_exec_available():
            raise HTTPException(
                status_code=400,
                detail="in-app Terraform execution is not enabled on this server; use the CLI",
            )

        subscription_id = selected_subscription["subscriptionId"] if selected_subscription else DEFAULT_SUBSCRIPTION
        delegated_token: tuple[str, int] | None = None
        existing_resource_group = body.resourceGroupName.strip()
        if existing_resource_group and not body.externalSubscription:
            raise HTTPException(
                status_code=400,
                detail="an existing resource group is supported only for external subscriptions",
            )
        if body.externalSubscription:
            if engine != "bicep":
                raise HTTPException(
                    status_code=400,
                    detail="external subscription deployment currently supports Bicep only",
                )
            subscription_id = body.subscriptionId.strip().lower()
            if not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                subscription_id,
            ):
                raise HTTPException(
                    status_code=400,
                    detail="a valid external Azure subscription ID is required",
                )
            try:
                delegated_token = await acquire_external_arm_token_for_subscription(
                    request, request.cookies.get(COOKIE_NAME), subscription_id
                )
            except EntraAuthError as exc:
                raise HTTPException(
                    status_code=401,
                    detail="Azure authorization expired; authorize your Azure account again",
                ) from exc
            if delegated_token is None:
                raise HTTPException(
                    status_code=401,
                    detail="authorize an Azure account that can access this subscription",
                )

            if existing_resource_group:
                if (
                    len(existing_resource_group) > 90
                    or existing_resource_group.endswith(".")
                    or not re.fullmatch(r"[\w.()\-]+", existing_resource_group)
                ):
                    raise HTTPException(
                        status_code=400, detail="invalid external resource group name"
                    )
                token, expires_in = delegated_token
                preflight_dep = {
                    "subscription_id": subscription_id,
                    "resource_group": existing_resource_group,
                    "_delegated_arm_token": token,
                    "_delegated_arm_expires_at": time.time() + expires_in,
                }
                try:
                    await asyncio.to_thread(az.get_resource_group, preflight_dep)
                except Exception as exc:
                    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

                    if isinstance(exc, ResourceNotFoundError):
                        raise HTTPException(
                            status_code=404,
                            detail="the external resource group does not exist or is not visible",
                        ) from exc
                    if isinstance(exc, HttpResponseError) and exc.status_code == 403:
                        raise HTTPException(
                            status_code=403,
                            detail="you do not have access to the external resource group",
                        ) from exc
                    raise HTTPException(
                        status_code=502,
                        detail="could not verify the external resource group",
                    ) from exc

        now = time.time()
        end_at = _resolve_end_at(body, now, user)

        location = (body.location or "").strip().lower()
        if location and not re.fullmatch(r"[a-z][a-z0-9]{2,30}", location):
            raise HTTPException(status_code=400, detail="invalid location")

        parameter_values = _validate_parameter_values(body.parameterValues, item)

        preflight = None
        if selected_subscription:
            if not location:
                raise HTTPException(status_code=400, detail="Select a region before deployment")
            if _REGIONAL_CHECKS.locked():
                raise HTTPException(status_code=429, detail="Deployment checks are busy; retry shortly.", headers={"Retry-After": "5"})
            async with _REGIONAL_CHECKS:
                try:
                    regions = await setup_cloud.subscription_locations(subscription_id)
                except (ValueError, httpx.HTTPError, AzureError, TimeoutError):
                    raise HTTPException(status_code=502, detail="Cannot verify subscription regions before deployment. No resources were created.") from None
                if location not in {entry["name"] for entry in regions}:
                    raise HTTPException(status_code=400, detail="Region is not listed for this saved subscription")
                checked = await catalog_processing.check_region(item, selected_subscription, location, parameter_values, fresh=True)
            if checked["capacity"].get("status") == "unavailable":
                db.record_event(type="deployment_preflight_blocked", user_oid=user.oid, use_case=body.useCase,
                                meta={"subscriptionId": subscription_id, "location": location})
                raise HTTPException(status_code=409, detail="Capacity check reports this selection unavailable. Choose another region or resolve its quota/model restriction. No resources were created.")
            preflight = {"checkedAt": checked["checkedAt"], "location": location, "subscriptionId": subscription_id,
                         "capacityStatus": checked["capacity"].get("status", "unknown"),
                         "capacityDetail": checked["capacity"].get("detail", ""), "priceDetail": checked["priceDetail"],
                         "price": {key: (checked.get("price") or {}).get(key) for key in ("totalMonthly", "currency", "complete", "pricingSource", "fallbackReason")}}

        if is_customer_distribution() and body.useCase not in _catalog_items():
            raise HTTPException(status_code=409, detail="This use case is no longer available. Refresh the catalog. No deployment was created.")
        dep_id = secrets.token_hex(8)
        short_id = dep_id[:6]
        rg = existing_resource_group or _resource_group_name(body.useCase, env, short_id)
        cleanup_policy = "none" if existing_resource_group else "delete_resource_group"
        db.create_deployment(
            deployment_id=dep_id,
            use_case=body.useCase,
            environment=env,
            user_oid=user.oid,
            user_upn=user.upn,
            user_name=user.name,
            subscription_id=subscription_id,
            resource_group=rg,
            end_at=end_at,
            engine=engine,
            cleanup_policy=cleanup_policy,
            params={
                "template": item.get("template", ""),
                "catalogVersion": item.get("catalogVersion", ""),
                "parameterTypes": {entry["name"]: entry.get("type", "string") for entry in item.get("parameters", []) if isinstance(entry, dict) and entry.get("name")},
                "location": location,
                "parameterValues": parameter_values,
                "externalSubscription": body.externalSubscription,
                "existingResourceGroup": bool(existing_resource_group),
                "preflight": preflight,
            },
        )
        db.record_event(
            type="deploy_requested",
            user_oid=user.oid,
            user_upn=user.upn,
            use_case=body.useCase,
            deployment_id=dep_id,
            meta={
                "environment": env,
                "resourceGroup": rg,
                "engine": engine,
                "externalSubscription": body.externalSubscription,
                "existingResourceGroup": bool(existing_resource_group),
            },
        )

        dep = db.get_deployment(dep_id)
        assert dep is not None
        # Hand off to the in-process executor (managed identity + ARM SDK). The
        # provisioning runs as a background task; status flows to 'active' or
        # 'failed' as it completes.
        db.update_deployment(
            dep_id,
            status="deploying",
            gh_run_url=az.portal_rg_url(subscription_id, rg),
            detail="queued for provisioning",
        )
        if delegated_token is not None:
            token, expires_in = delegated_token
            dep["_delegated_arm_token"] = token
            dep["_delegated_arm_expires_at"] = time.time() + expires_in
        az.schedule(az.run_deploy(dep))
        dep = db.get_deployment(dep_id)
        assert dep is not None
        return {"deployment": _deployment_view(dep), "detail": "deployment started"}

    @router.get("/deployments")
    async def list_my_deployments(
        scope: str = "mine",
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        if scope == "all":
            if not user.is_admin:
                raise HTTPException(status_code=403, detail="admin only")
            rows = db.list_deployments(user_oid=None, limit=500)
        else:
            rows = db.list_deployments(user_oid=user.oid, limit=200)
        return {"deployments": [_deployment_view(d) for d in rows]}

    @router.get("/deployments/{deployment_id}")
    async def get_one(
        deployment_id: str,
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        dep = db.get_deployment(deployment_id)
        if dep is None:
            raise HTTPException(status_code=404, detail="not found")
        if dep["user_oid"] != user.oid and not user.is_admin:
            raise HTTPException(status_code=403, detail="not your deployment")
        return {"deployment": _deployment_view(dep)}

    @router.post("/deployments/{deployment_id}/destroy")
    async def destroy_now(
        deployment_id: str,
        request: Request,
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        verify_same_origin(request)
        dep = db.get_deployment(deployment_id)
        if dep is None:
            raise HTTPException(status_code=404, detail="not found")
        if dep["user_oid"] != user.oid and not user.is_admin:
            raise HTTPException(status_code=403, detail="not your deployment")
        if dep.get("cleanup_policy") == "none":
            raise HTTPException(
                status_code=409,
                detail="cleanup is managed outside TechLabs for this resource group",
            )
        if dep["status"] in ("destroyed", "destroying"):
            return {"deployment": _deployment_view(dep),
                    "detail": f"already {dep['status']}"}
        if (dep.get("params") or {}).get("externalSubscription"):
            try:
                delegated_token = await acquire_external_arm_token_for_subscription(
                    request,
                    request.cookies.get(COOKIE_NAME),
                    dep["subscription_id"],
                )
            except EntraAuthError as exc:
                raise HTTPException(
                    status_code=401,
                    detail="authorize your Azure account again before cleanup",
                ) from exc
            if delegated_token is None:
                raise HTTPException(
                    status_code=401,
                    detail="authorize your Azure account again before cleanup",
                )
            token, expires_in = delegated_token
            dep["_delegated_arm_token"] = token
            dep["_delegated_arm_expires_at"] = time.time() + expires_in
        db.update_deployment(deployment_id, status="destroying",
                             detail="teardown started")
        db.record_event(
            type="destroy_requested",
            user_oid=user.oid,
            user_upn=user.upn,
            use_case=dep["use_case"],
            deployment_id=deployment_id,
        )
        az.schedule(az.run_destroy(dep))
        dep = db.get_deployment(deployment_id)
        assert dep is not None
        return {"deployment": _deployment_view(dep), "detail": "teardown started"}

    # --------------------------------------------------------------- admin ---
    @router.get("/admin/catalog")
    async def admin_catalog(_: CurrentUser = Depends(require_admin)) -> JSONResponse:
        import setup_github
        result = setup_github.catalog_management() if is_customer_distribution() else {"items": []}
        return JSONResponse({**result, "supported": is_customer_distribution()}, headers={"Cache-Control": "no-store"})

    @router.put("/admin/catalog/{use_case}")
    async def set_catalog_visibility(
        use_case: str, body: CatalogVisibilityIn, request: Request, admin: CurrentUser = Depends(require_admin),
    ) -> JSONResponse:
        import setup_github
        if not (request.headers.get("origin") or request.headers.get("referer")):
            raise HTTPException(status_code=403, detail="browser origin required")
        verify_same_origin(request)
        if not is_customer_distribution():
            raise HTTPException(status_code=409, detail="Catalog removal requires an organization-operated portal")
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirm the catalog visibility change")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", use_case):
            raise HTTPException(status_code=400, detail="Invalid use case")
        snapshot = setup_github.catalog_snapshot()
        repository = str(snapshot.get("repository", "")).lower()
        if not repository or body.repository.lower() != repository or body.version != snapshot.get("version"):
            raise HTTPException(status_code=409, detail="The catalog changed. Refresh the use-case list before trying again.")
        items = {item["path"]: item for item in snapshot.get("items", [])}
        all_removed = db.get_setup_setting("catalog_removed", {})
        removed = all_removed.get(repository, {})
        if use_case not in items and use_case not in removed:
            raise HTTPException(status_code=404, detail="Unknown use case")
        changed = body.removed != (use_case in removed)
        if changed:
            if body.removed:
                removed[use_case] = {"displayName": items[use_case].get("displayName", use_case), "removedAt": time.time()}
            else:
                removed.pop(use_case, None)
            if removed:
                all_removed[repository] = removed
            else:
                all_removed.pop(repository, None)
            db.set_setup_setting("catalog_removed", all_removed)
            db.record_event(type="catalog_use_case_removed" if body.removed else "catalog_use_case_restored",
                            user_oid=admin.oid, user_upn=admin.upn, use_case=use_case,
                            meta={"repository": repository, "version": body.version})
        return JSONResponse({"path": use_case, "removed": body.removed, "changed": changed}, headers={"Cache-Control": "no-store"})

    @router.post("/admin/catalog/{use_case}/repository-removal")
    async def request_repository_removal(
        use_case: str, body: CatalogRepositoryRemovalIn, request: Request, admin: CurrentUser = Depends(require_admin),
    ) -> JSONResponse:
        import setup_github
        if not (request.headers.get("origin") or request.headers.get("referer")):
            raise HTTPException(status_code=403, detail="browser origin required")
        verify_same_origin(request)
        if not is_customer_distribution():
            raise HTTPException(status_code=409, detail="Repository removal requires an organization-operated portal")
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirm deletion of the use-case folder from the configured repository branch")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", use_case):
            raise HTTPException(status_code=400, detail="Invalid use case")
        now = time.time()
        if setup_github._SYNC_LOCK.locked() or now - db.get_setup_setting("catalog_repository_removal_requested_at", 0) < 60:
            raise HTTPException(status_code=429, detail="Catalog processing or a removal request is busy. Retry in one minute.", headers={"Retry-After": "60"})
        db.set_setup_setting("catalog_repository_removal_requested_at", now)
        try:
            result = await asyncio.wait_for(setup_github.delete_catalog_repository_folder(
                use_case, body.repository, body.version, body.branch, body.sourceCommit), timeout=90)
        except setup_github.GitHubConnectionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except setup_github.CatalogRepositoryConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except setup_github.CatalogRepositoryFailure as error:
            db.record_event(type="catalog_repository_deletion_failed", user_oid=admin.oid, use_case=use_case,
                            meta={"stage": error.stage, "httpStatus": error.http_status})
            raise HTTPException(status_code=502, detail=str(error)) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OSError, TimeoutError):
            raise HTTPException(status_code=502, detail="Repository deletion could not be confirmed. It may already have committed; inspect the configured branch and sync before retrying. No Azure resources or existing deployments were deleted.") from None
        db.record_event(type="catalog_repository_use_case_deleted", user_oid=admin.oid, user_upn=admin.upn, use_case=use_case,
                        meta={"repository": body.repository, "branch": result["branch"], "commit": result["commit"],
                              "changed": result["changed"], "deletedFiles": result["deletedFiles"]})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("/admin/telemetry")
    async def admin_telemetry(
        days: int = 30,
        _: CurrentUser = Depends(require_admin),
    ) -> dict[str, Any]:
        days = max(1, min(days, 365))
        return db.telemetry_summary(days=days)

    @router.get("/admin/users")
    async def admin_users(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
        users = db.list_users()
        return {
            "users": [
                {
                    "oid": u["oid"],
                    "upn": u["upn"],
                    "name": u["name"],
                    "role": u["role"],
                    "createdAt": _iso(u["created_at"]),
                    "lastSeen": _iso(u["last_seen"]),
                    "pending": str(u["oid"]).startswith("upn:"),
                }
                for u in users
            ]
        }

    @router.post("/admin/users", status_code=204)
    async def admin_set_role(
        body: RoleIn,
        request: Request,
        admin: CurrentUser = Depends(require_admin),
    ) -> None:
        verify_same_origin(request)
        upn = body.upn.strip()
        if not upn or "@" not in upn:
            raise HTTPException(status_code=400, detail="valid UPN required")
        if body.role not in ("admin", "guest"):
            raise HTTPException(status_code=400, detail="role must be admin or guest")
        # Guard against an admin removing their own admin rights by accident.
        if body.role == "guest" and upn.lower() == admin.upn.lower():
            raise HTTPException(
                status_code=400, detail="cannot demote yourself"
            )
        db.set_user_role(upn, body.role)
        db.record_event(
            type="admin_role_change",
            user_oid=admin.oid,
            user_upn=admin.upn,
            meta={"target": upn, "role": body.role},
        )

    # ------------------------------------------------------- submissions ---
    @router.post("/use-cases/upload")
    async def upload_use_case(
        request: Request,
        file: UploadFile = File(...),
        displayName: str = Form(""),
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        verify_same_origin(request)
        name = (file.filename or "").lower()
        if not name.endswith(".zip"):
            raise HTTPException(status_code=400, detail="Upload a .zip archive.")
        data = await file.read()
        if len(data) > submissions.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The zip is too large.")
        try:
            staged = submissions.stage_submission(
                data=data,
                display_name=displayName.strip()[:120],
                user_oid=user.oid,
                user_upn=user.upn,
                user_name=user.name,
            )
        except submissions.SubmissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        db.create_submission(
            submission_id=staged["id"],
            slug=staged["slug"],
            display_name=staged["displayName"],
            user_oid=user.oid,
            user_upn=user.upn,
            user_name=user.name,
            path=staged["path"],
            files=staged["files"],
            detail="metadata.json included" if staged["hasMetadata"]
            else "metadata.json drafted (submitter omitted it)",
        )
        db.record_event(
            type="use_case_submitted",
            user_oid=user.oid,
            user_upn=user.upn,
            use_case=staged["slug"],
            meta={"files": str(len(staged["files"]))},
        )

        # Create branch + commit the bundle so a maintainer can open the PR.
        # Best-effort: if the push fails (or no token is configured) the
        # submission stays staged for manual download from the admin panel.
        detail = (
            "Uploaded and saved in the portal only. GitHub is not connected or its authorization is unavailable. "
            "Ask a portal administrator to check Setup > GitHub. No review branch was created. "
            "The ZIP is available in Admin > Submissions; it will not be sent automatically."
        )
        db.update_submission(staged["id"], detail=detail)
        validation_failed = False
        if github_intake.configured():
            try:
                branch = f"usecase-intake/{staged['slug']}-{staged['id'][:6]}"
                files = submissions.staged_files(
                    {"path": staged["path"], "slug": staged["slug"]}
                )
                pushed = await github_intake.push_submission(
                    branch=branch,
                    files=files,
                    message=(
                        f"feat(catalog): {staged['displayName']} "
                        f"(community submission by {user.upn})"
                    ),
                )
                db.update_submission(
                    staged["id"],
                    status="branched",
                    branch=pushed["branch"],
                    compare_url=pushed["compareUrl"],
                    detail="Branch created; a maintainer can open the pull request.",
                )
                db.record_event(
                    type="use_case_branch_created",
                    user_oid=user.oid,
                    user_upn=user.upn,
                    use_case=staged["slug"],
                    meta={"branch": pushed["branch"]},
                )
                detail = "Uploaded and a review branch was created. A maintainer will open the pull request."
            except github_intake.SubmissionValidationError as exc:
                validation_failed = True
                detail = "Uploaded and staged, but validation failed: " + str(exc) + " No review branch was created."
                db.update_submission(staged["id"], detail=detail)
                db.record_event(type="use_case_validation_failed", user_oid=user.oid, user_upn=user.upn,
                                use_case=staged["slug"], meta={"stage": "before_review_branch"})
            except Exception as exc:  # push is best-effort; keep the staged copy
                detail = (
                    "Uploaded and saved in the portal, but GitHub review branch creation could not be confirmed. "
                    "Ask a portal administrator to check Setup > GitHub and the repository before retrying. "
                    "The ZIP is available in Admin > Submissions; it will not be sent automatically."
                )
                db.update_submission(
                    staged["id"],
                    detail=detail,
                )
                db.record_event(type="use_case_branch_failed", user_oid=user.oid, user_upn=user.upn,
                                use_case=staged["slug"], meta={"errorType": type(exc).__name__})

        sub = db.get_submission(staged["id"])
        assert sub is not None
        return {
            "submission": _submission_view(sub),
            "detail": detail,
            "validationFailed": validation_failed,
            "stagedOnly": sub["status"] != "branched",
        }

    @router.get("/admin/submissions")
    async def admin_submissions(
        _: CurrentUser = Depends(require_admin),
    ) -> dict[str, Any]:
        subs = db.list_submissions(limit=200)
        return {"submissions": [_submission_view(s) for s in subs]}

    @router.get("/admin/submissions/{submission_id}/download")
    async def admin_download_submission(
        submission_id: str,
        _: CurrentUser = Depends(require_admin),
    ) -> Response:
        sub = db.get_submission(submission_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="not found")
        try:
            blob = submissions.bundle_zip(sub)
        except submissions.SubmissionError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        filename = f"{sub['slug']}-submission.zip"
        return Response(
            content=blob,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/admin/submissions/{submission_id}/discard", status_code=204)
    async def admin_discard_submission(
        submission_id: str,
        request: Request,
        admin: CurrentUser = Depends(require_admin),
    ) -> None:
        verify_same_origin(request)
        sub = db.get_submission(submission_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="not found")
        if sub["status"] == "discarded":
            return
        if sub["status"] == "merged":
            raise HTTPException(status_code=409, detail="A merged submission must be managed through its repository.")
        try:
            await github_intake.delete_submission_branch(sub)
        except github_intake.BranchDeletionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        try:
            submissions.discard(sub)
        except submissions.SubmissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        detail = "Deleted by admin; review branch removed or already absent." if sub.get("branch") else "Deleted by admin; staged files removed."
        db.update_submission(submission_id, status="discarded", compare_url="", detail=detail)
        db.record_event(
            type="use_case_submission_discarded",
            user_oid=admin.oid,
            user_upn=admin.upn,
            use_case=sub["slug"],
            meta={"submissionId": submission_id, "branch": sub.get("branch", "")},
        )

    return router


def _resolve_end_at(body: DeployIn, now: float, user: CurrentUser) -> float:
    """Compute and validate the deployment end time."""
    max_hours = DEFAULT_MAX_LIFETIME_HOURS
    if body.endAt:
        try:
            parsed = datetime.fromisoformat(body.endAt.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid endAt") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        end_at = parsed.timestamp()
    elif body.lifetimeHours is not None:
        end_at = now + float(body.lifetimeHours) * 3600.0
    else:
        end_at = now + max_hours * 3600.0

    if end_at < now + MIN_LIFETIME_MINUTES * 60:
        raise HTTPException(
            status_code=400,
            detail=f"end time must be at least {MIN_LIFETIME_MINUTES} minutes away",
        )
    # Admins may exceed the cap; guests are clamped to the max lifetime.
    if not user.is_admin and end_at > now + max_hours * 3600.0:
        raise HTTPException(
            status_code=400,
            detail=f"end time exceeds the {max_hours}h maximum lifetime",
        )
    return end_at
