"""Execute Azure deployments directly with the App Service managed identity.

Option B: the backend is BOTH the control plane and the executor. It holds no
secrets — it authenticates to Azure with ``DefaultAzureCredential`` (the App
Service system-assigned managed identity in production; ``az login`` / developer
credentials locally) and submits ARM deployments via the resource-management
SDK. No GitHub PAT, no callback token.

Customer templates are precompiled from Bicep to ARM JSON in their own toolchain
(run in CI before the site is published, and locally before testing deploys).
Each use case/environment becomes
``site/server/_templates/<use_case>/<env>.json`` with the shape
``{"template": {...}, "parameters": {...}}`` — ready to submit as-is.

Deployments and teardowns run as background asyncio tasks (the SDK is
synchronous, so the blocking calls run in a worker thread). The single gunicorn
worker means exactly one in-process executor — no leader election needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

import db

SERVER_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SERVER_DIR / "_templates"

_LOG = logging.getLogger(__name__)

# Keep references to in-flight tasks so they are not garbage-collected mid-run.
_TASKS: set[asyncio.Task] = set()


def _location() -> str:
    # Default to Central US: that is where this subscription has App Service /
    # serverFarms quota (East US is currently capped at 0). Override per
    # environment with GALLERY_DEPLOY_LOCATION.
    return os.environ.get("GALLERY_DEPLOY_LOCATION", "centralus").strip() or "centralus"


def executor_available() -> tuple[bool, str]:
    """Return (ok, reason) — whether the Azure SDK is importable."""
    try:
        import azure.identity  # noqa: F401
        from azure.mgmt.resource import ResourceManagementClient  # noqa: F401
    except Exception as exc:  # pragma: no cover - import-time environment issue
        return False, f"Azure SDK not installed ({exc})"
    return True, "ok"


def portal_rg_url(subscription_id: str, resource_group: str) -> str:
    """Azure portal link to the deployment's resource group.

    Pin the directory when configured so a B2B guest does not receive a portal
    token from an unrelated home tenant.
    """
    tenant_id = os.environ.get("ARM_TENANT_ID", "").strip()
    tenant_fragment = f"@{tenant_id}" if tenant_id else "@"
    return (
        f"https://portal.azure.com/#{tenant_fragment}/resource/subscriptions/"
        f"{subscription_id}/resourceGroups/{resource_group}/overview"
    )


# --------------------------------------------------------------------------- #
# Deployer view access (Reader role assignment on the deployment's RG)
# --------------------------------------------------------------------------- #
# This does NOT change how resources are provisioned — it runs after a
# successful deploy so the signed-in user can VIEW their resources in the Azure
# portal. Built-in "Reader" is grantable under the deployer identity's
# constrained RBAC-Admin condition (which blocks Owner/Contributor/UAA/RBAC
# Admin). Scoped to the resource group, so it is removed automatically when the
# group is torn down. Users signing in from another tenant have no principal in
# the subscription's tenant, so they are first invited as a B2B guest (Microsoft
# Graph) before the assignment. Best-effort: a failure never fails the deployment.
_READER_ROLE_ID = "acdd72a7-3385-48ef-bd42-f606fba81ae7"

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _view_grant_enabled() -> bool:
    from distribution import is_customer_distribution

    default = "false" if is_customer_distribution() else "true"
    val = os.environ.get("GALLERY_GRANT_VIEW_ACCESS", default).strip().lower()
    return val not in ("false", "0", "no", "off")


def _invite_enabled() -> bool:
    from distribution import is_customer_distribution

    if is_customer_distribution():
        return False
    val = os.environ.get("GALLERY_INVITE_GUEST", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _graph_headers() -> dict[str, str]:
    # Reuse the deploy credential (managed identity in prod). It needs the Graph
    # application permissions User.Invite.All and User.ReadBasic.All, admin
    # consented on id-techlabs-deployer.
    token = _get_credential().get_token(_GRAPH_SCOPE).token
    return {"Authorization": f"Bearer {token}"}


def _principal_exists(object_id: str) -> bool:
    """True if object_id is already a principal in the subscription's tenant."""
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{_GRAPH_BASE}/users/{object_id}",
            params={"$select": "id"},
            headers=_graph_headers(),
        )
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return True


def _find_directory_user(email: str) -> str | None:
    """Object id of an existing member/guest matching email, or None.

    Guests keep the original address in otherMails (and in mail once redeemed);
    members match on mail or userPrincipalName. Query all three defensively.
    """
    safe = email.replace("'", "''")
    headers = {**_graph_headers(), "ConsistencyLevel": "eventual"}
    params = {
        "$select": "id",
        "$count": "true",
        "$top": "1",
        "$filter": (
            f"mail eq '{safe}' or userPrincipalName eq '{safe}' "
            f"or otherMails/any(x:x eq '{safe}')"
        ),
    }
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{_GRAPH_BASE}/users", params=params, headers=headers)
    resp.raise_for_status()
    values = resp.json().get("value") or []
    return values[0]["id"] if values else None


def _invite_guest(email: str, redirect_url: str) -> str:
    """Invite email as a B2B guest; return the new guest's object id."""
    body = {
        "invitedUserEmailAddress": email,
        "inviteRedirectUrl": redirect_url,
        "sendInvitationMessage": True,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{_GRAPH_BASE}/invitations", json=body, headers=_graph_headers()
        )
    resp.raise_for_status()
    return resp.json()["invitedUser"]["id"]


def _resolve_principal_id(user_oid: str, email: str, redirect_url: str) -> str | None:
    """Object id to grant Reader to, resolved in the subscription's own tenant.

    RBAC can only reference principals in the subscription's tenant, so a user
    signing in from another tenant (whose home oid is meaningless here) must be
    resolved to — or invited as — a B2B guest first.
    """
    if user_oid and _principal_exists(user_oid):
        return user_oid  # native member (or a guest whose oid we already hold)
    if not email:
        return None
    try:
        existing = _find_directory_user(email)
    except httpx.HTTPStatusError as exc:
        # Directory search may be blocked by tenant policy even when the app
        # is allowed to create invitations. Continue so Graph can evaluate the
        # invitation request itself instead of failing before POST /invitations.
        if exc.response.status_code != 403:
            raise
        _LOG.warning("directory lookup forbidden for %s; attempting invitation", email)
        existing = None
    if existing:
        return existing
    if not _invite_enabled():
        return None
    return _invite_guest(email, redirect_url)


def _auth_client(subscription_id: str) -> Any:
    from azure.mgmt.authorization import AuthorizationManagementClient

    return AuthorizationManagementClient(_get_credential(), subscription_id)


def grant_view_access(dep: dict[str, Any]) -> bool:
    """Give the deploying user Reader on their deployment's resource group.

    Resolves the user to a principal in the subscription's tenant first
    (inviting them as a B2B guest when they sign in from another tenant), then
    assigns Reader. Idempotent: an already-existing assignment is success.

    Returns True when an assignment exists after the call (created or already
    present), False when the grant was skipped because no principal could be
    resolved (system deploy, or invite disabled for a cross-tenant user).
    """
    # External deployments are authorized by the user's own subscription role;
    # the TechLabs identity must not grant RBAC in that subscription.
    if dep.get("_delegated_arm_token"):
        return False

    subscription_id = dep["subscription_id"]
    resource_group = dep["resource_group"]
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"

    principal_id = _resolve_principal_id(
        (dep.get("user_oid") or "").strip(),
        (dep.get("user_upn") or "").strip(),
        portal_rg_url(subscription_id, resource_group),
    )
    if not principal_id:
        return False  # nothing to grant (system deploy, or invite disabled/unresolved)

    role_definition_id = (
        f"/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Authorization/roleDefinitions/{_READER_ROLE_ID}"
    )
    # Deterministic name so retries reuse the same assignment instead of
    # creating duplicates.
    name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}|{principal_id}|reader"))

    from azure.core.exceptions import HttpResponseError, ResourceExistsError

    client = _auth_client(subscription_id)
    try:
        client.role_assignments.create(
            scope,
            name,
            {
                "role_definition_id": role_definition_id,
                "principal_id": principal_id,
                # Declaring the type avoids replication races where Azure cannot
                # yet resolve a just-created principal.
                "principal_type": "User",
            },
        )
    except ResourceExistsError:
        return True  # already granted — idempotent success
    except HttpResponseError as exc:
        if getattr(getattr(exc, "error", None), "code", "") == "RoleAssignmentExists":
            return True
        raise
    return True


async def _grant_view_access(dep: dict[str, Any]) -> None:
    """Best-effort wrapper: grant view access without ever failing the deploy."""
    if not _view_grant_enabled() or str(dep.get("user_oid", "")).startswith("oidc:"):
        return
    dep_id = dep["id"]
    event_meta = {
        "subscriptionId": dep.get("subscription_id", ""),
        "resourceGroup": dep.get("resource_group", ""),
    }
    try:
        granted = await asyncio.to_thread(grant_view_access, dep)
        db.record_event(
            type="view_access_granted" if granted else "view_access_skipped",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
            meta=event_meta,
        )
    except Exception as exc:  # viewing is a convenience, not the deployment
        reason = _clean_error(exc)
        _LOG.warning("could not grant view access for %s: %s", dep_id, reason)
        db.record_event(
            type="view_access_grant_failed",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
            meta={**event_meta, "error": reason},
        )


# --------------------------------------------------------------------------- #
# Template loading (precompiled ARM JSON)
# --------------------------------------------------------------------------- #
def _template_path(use_case: str, environment: str) -> Path:
    return TEMPLATES_DIR / use_case / f"{environment}.json"


def load_template(use_case: str, environment: str) -> tuple[dict, dict]:
    path = _template_path(use_case, environment)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No compiled template for {use_case}/{environment}. "
            "Publish customer-compiled runtime inputs through the configured repository."
        ) from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Bad template for {use_case}/{environment}: {exc}") from exc
    template = data.get("template")
    if not isinstance(template, dict):
        raise RuntimeError(f"Compiled template for {use_case}/{environment} is malformed")
    parameters = data.get("parameters") or {}
    return template, parameters


# --------------------------------------------------------------------------- #
# Azure SDK clients (lazy — only imported when actually deploying)
# --------------------------------------------------------------------------- #
_credential: Any = None


class DelegatedTokenCredential:
    """Azure credential backed by one user-delegated ARM access token."""

    def __init__(self, token: str, expires_on: int | float) -> None:
        self._token = token
        self._expires_on = float(expires_on)

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        if self._expires_on <= time.time() + 60:
            raise RuntimeError("delegated Azure access token expired")
        return AccessToken(self._token, int(self._expires_on))


def _credential_for_deployment(dep: dict[str, Any]) -> Any:
    from distribution import is_customer_distribution

    if is_customer_distribution() and (
        dep.get("_delegated_arm_token") or (dep.get("params") or {}).get("externalSubscription")
    ):
        raise RuntimeError("customer installations require their configured managed identity")
    token = dep.get("_delegated_arm_token")
    if token:
        return DelegatedTokenCredential(
            str(token), float(dep.get("_delegated_arm_expires_at", 0.0))
        )
    if (dep.get("params") or {}).get("externalSubscription"):
        raise RuntimeError("external deployment requires delegated Azure authorization")
    return _get_credential()


def _get_credential() -> Any:
    global _credential
    if _credential is None:
        from distribution import is_customer_distribution, uses_system_identity

        if is_customer_distribution():
            from azure.identity import ManagedIdentityCredential

            client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
            if uses_system_identity():
                if client_id:
                    raise RuntimeError("conflicting customer managed identity configuration")
                _credential = ManagedIdentityCredential()
                return _credential
            if not client_id:
                raise RuntimeError("customer managed identity is not configured")
            _credential = ManagedIdentityCredential(client_id=client_id)
            return _credential
        from azure.identity import DefaultAzureCredential

        # Managed identity in App Service; az/developer creds locally. Never the
        # interactive browser flow (this runs headless on the server).
        _credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
    return _credential


def _client(subscription_id: str, credential: Any = None) -> Any:
    from distribution import is_customer_distribution

    if is_customer_distribution():
        from setup_config import subscription_for

        try:
            if not subscription_id:
                raise ValueError
            subscription_for(subscription_id)
        except ValueError:
            raise RuntimeError("subscription is outside the customer installation") from None
    # The unified ``ResourceManagementClient`` (top-level import) exposes the
    # ``deployments``, ``deployment_operations`` and ``resource_groups`` operation
    # groups the executor relies on. This holds for azure-mgmt-resource 23.x
    # (pinned ``>=23.1,<24`` in requirements.txt). v24+ split deployments into a
    # separate client and dropped them from the resources client.
    from azure.mgmt.resource import ResourceManagementClient

    return ResourceManagementClient(credential or _get_credential(), subscription_id)


def get_resource_group(dep: dict[str, Any]) -> Any:
    """Return an existing resource group using the deployment credential."""
    client = _client(dep["subscription_id"], _credential_for_deployment(dep))
    return client.resource_groups.get(dep["resource_group"])


def _innermost_arm_error(err: Any) -> tuple[str | None, str | None]:
    """Walk an ARM error tree to the deepest, most specific code/message.

    Azure wraps the useful reason (e.g. a quota error) several levels deep under
    generic 'InvalidTemplateDeployment' / 'ValidationForResourceFailed' nodes.
    The leaf carries the actionable detail, so dig to it.
    """
    code = getattr(err, "code", None)
    message = getattr(err, "message", None)
    details = getattr(err, "details", None) or []
    for child in details:
        c_code, c_msg = _innermost_arm_error(child)
        if c_code or c_msg:
            return c_code, c_msg
    return code, message


def _clean_error(exc: Exception) -> str:
    # Azure SDK errors carry a structured ``.error`` tree whose leaf has the
    # actionable reason; surface that instead of the generic outer wrapper.
    arm_error = getattr(exc, "error", None)
    if arm_error is not None:
        code, message = _innermost_arm_error(arm_error)
        if code or message:
            text = " ".join(p for p in (code, message) if p).strip()
            text = " ".join(text.split())  # collapse newlines/whitespace
            if text:
                return text[:600]
    msg = " ".join(str(exc).split()).strip()
    return (msg[:600] if msg else exc.__class__.__name__)


# --------------------------------------------------------------------------- #
# Blocking SDK work (run in a thread via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def _merge_parameter_overrides(
    template: dict[str, Any],
    parameters: dict[str, Any],
    overrides: dict[str, str] | None,
) -> None:
    """Merge validated advanced deployment options into the ARM parameters map
    (mutated in place). Values arrive as strings from the API; cast each to the
    type declared in the compiled template so ARM accepts them. Unknown params
    are ignored (the API already validates against the catalog)."""
    if not overrides:
        return
    declared = template.get("parameters") or {}
    for name, raw in overrides.items():
        spec = declared.get(name)
        if not isinstance(spec, dict):
            continue
        ptype = str(spec.get("type") or "string").lower()
        sval = str(raw)
        if ptype == "int":
            try:
                value: Any = int(sval)
            except ValueError:
                continue
        elif ptype == "bool":
            value = sval.strip().lower() == "true"
        else:
            value = sval
        parameters[name] = {"value": value}


def _do_deploy(dep: dict[str, Any]) -> None:
    subscription_id = dep["subscription_id"]
    resource_group = dep["resource_group"]
    version = (dep.get("params") or {}).get("catalogVersion")
    if version:
        from setup_github import snapshot_root
        path = snapshot_root(version) / "_templates" / dep["use_case"] / f"{dep['environment']}.json"
        compiled = json.loads(path.read_text(encoding="utf-8-sig"))
        template, parameters = compiled["template"], compiled.get("parameters", {})
    else:
        template, parameters = load_template(dep["use_case"], dep["environment"])

    params_meta = dep.get("params") or {}
    _merge_parameter_overrides(template, parameters, params_meta.get("parameterValues"))

    location = params_meta.get("location") or _location()
    client = _client(subscription_id, _credential_for_deployment(dep))
    if dep.get("cleanup_policy", "delete_resource_group") == "none":
        client.resource_groups.get(resource_group)
    else:
        client.resource_groups.create_or_update(
            resource_group,
            {
                "location": location,
                "tags": {
                    "techlabs-managed": "true",
                    "techlabs-deployment": dep["id"],
                    "techlabs-usecase": dep["use_case"],
                    "techlabs-owner": dep.get("user_upn", ""),
                },
            },
        )
    # Pass a plain dict rather than importing the SDK model classes
    # (Deployment/DeploymentProperties/DeploymentMode). Their import location
    # moved across azure-mgmt-resource major versions; the SDK serializes this
    # dict the same way, so the executor stays version-agnostic.
    deployment = {
        "properties": {
            "mode": "Incremental",
            "template": template,
            "parameters": parameters,
        }
    }
    poller = client.deployments.begin_create_or_update(
        resource_group, f"techlabs-{dep['id']}", deployment
    )
    poller.result()  # blocks until the deployment finishes; raises on failure


# Friendly display names for the resource types our catalog templates create,
# so the live log reads "App Service plan: ready" instead of "serverfarms: Succeeded".
_RESOURCE_LABELS = {
    "microsoft.web/serverfarms": "App Service plan",
    "microsoft.web/sites": "Web App",
    "microsoft.web/sites/config": "Web App settings",
    "microsoft.web/sites/slots": "Web App slot",
    "microsoft.cognitiveservices/accounts": "Azure OpenAI",
    "microsoft.cognitiveservices/accounts/deployments": "Model deployment",
    "microsoft.documentdb/databaseaccounts": "Cosmos DB",
    "microsoft.documentdb/databaseaccounts/sqldatabases": "Cosmos database",
    "microsoft.documentdb/databaseaccounts/sqldatabases/containers": "Cosmos container",
    "microsoft.storage/storageaccounts": "Storage account",
    "microsoft.keyvault/vaults": "Key Vault",
    "microsoft.insights/components": "Application Insights",
    "microsoft.operationalinsights/workspaces": "Log Analytics",
    "microsoft.managedidentity/userassignedidentities": "Managed identity",
    "microsoft.search/searchservices": "AI Search",
    "microsoft.sql/servers": "SQL server",
    "microsoft.sql/servers/databases": "SQL database",
    "microsoft.network/virtualnetworks": "Virtual network",
    "microsoft.network/privateendpoints": "Private endpoint",
    "microsoft.authorization/roleassignments": "Role assignment",
    "microsoft.containerregistry/registries": "Container registry",
    "microsoft.app/containerapps": "Container App",
    "microsoft.app/managedenvironments": "Container Apps environment",
}

# Map raw ARM provisioning states to friendly, consistently-cased words that the
# frontend colour-codes (green = done, red = failed, accent = in progress).
_STATE_LABELS = {
    "succeeded": "ready",
    "running": "provisioning",
    "creating": "creating",
    "started": "provisioning",
    "accepted": "queued",
    "deleting": "deleting",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
}


def _resource_label(rtype: str, name: str) -> str:
    """Human-friendly label for a deployment operation's target resource."""
    key = (rtype or "").lower()
    if key == "microsoft.resources/deployments":
        # Nested module deployments — the resource name is the bicep module name
        # (e.g. "openai", "cosmos"), which is the most descriptive thing to show.
        return f"{name} module" if name else "module"
    label = _RESOURCE_LABELS.get(key)
    if label:
        return label
    return (rtype or "resource").split("/")[-1]


def _deploy_progress(dep: dict[str, Any]) -> str | None:
    """Summarise in-flight ARM deployment operations for the live log."""
    client = _client(dep["subscription_id"], _credential_for_deployment(dep))
    name = f"techlabs-{dep['id']}"
    try:
        ops = list(client.deployment_operations.list(dep["resource_group"], name))
    except Exception:
        return None
    # Key by resource *name* so multiple resources of the same type — or several
    # nested module deployments — stay distinct instead of overwriting each other.
    entries: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for op in ops:
        props = getattr(op, "properties", None)
        target = getattr(props, "target_resource", None)
        if target is None:
            continue
        rtype = getattr(target, "resource_type", None) or ""
        rname = getattr(target, "resource_name", None) or ""
        if not rtype and not rname:
            continue
        key = f"{rtype}/{rname}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        raw_state = (getattr(props, "provisioning_state", "") or "").strip()
        label = _resource_label(rtype, rname)
        state = _STATE_LABELS.get(raw_state.lower(), raw_state.lower() or "pending")
        entries.append((label, state))
    if not entries:
        return None
    total = len(entries)
    ready = sum(1 for _, s in entries if s == "ready")
    failed = sum(1 for _, s in entries if s in ("failed", "canceled"))
    # Show failing/in-progress resources first so they're immediately visible,
    # then the finished ones. Cap the list so the live-log line stays readable.
    rank = {"failed": 0, "canceled": 0, "provisioning": 1, "creating": 1,
            "queued": 2, "deleting": 2, "ready": 3}
    entries.sort(key=lambda e: rank.get(e[1], 2))
    shown = entries[:8]
    detail = " · ".join(f"{label}: {state}" for label, state in shown)
    if len(entries) > len(shown):
        detail += f" · +{len(entries) - len(shown)} more"
    head = f"{ready}/{total} resources ready"
    if failed:
        head += f", {failed} failed"
    return f"{head} · {detail}"



def _do_destroy(dep: dict[str, Any]) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    if dep.get("cleanup_policy") == "none":
        raise RuntimeError("refusing to delete an externally managed resource group")
    client = _client(dep["subscription_id"], _credential_for_deployment(dep))
    resource_group = dep["resource_group"]
    try:
        existing = client.resource_groups.get(resource_group)
    except ResourceNotFoundError:
        return  # already gone — treat as success
    # Safety: only ever delete resource groups this system created.
    tags = getattr(existing, "tags", None) or {}
    if tags.get("techlabs-managed") != "true":
        raise RuntimeError(
            f"refusing to delete unmanaged resource group {resource_group}"
        )
    client.resource_groups.begin_delete(resource_group).result()


def resource_group_exists(dep: dict[str, Any]) -> bool:
    """Return whether the deployment's resource group still exists in Azure."""
    from azure.core.exceptions import ResourceNotFoundError

    client = _client(dep["subscription_id"], _credential_for_deployment(dep))
    try:
        client.resource_groups.get(dep["resource_group"])
    except ResourceNotFoundError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Async orchestration
# --------------------------------------------------------------------------- #
def _engine(dep: dict[str, Any]) -> str:
    return (dep.get("engine") or "bicep").strip().lower()


async def run_deploy(dep: dict[str, Any]) -> None:
    dep_id = dep["id"]
    try:
        if _engine(dep) == "terraform":
            import terraform_exec

            ok, reason = terraform_exec.terraform_available()
            if not ok:
                raise RuntimeError(reason)
            db.update_deployment(
                dep_id,
                status="deploying",
                gh_run_url=portal_rg_url(dep["subscription_id"], dep["resource_group"]),
                detail="running terraform apply",
            )
            # tf_apply streams its own progress into the deployment detail.
            await asyncio.to_thread(terraform_exec.tf_apply, dep)
            db.update_deployment(dep_id, status="active", detail="deployment succeeded")
            db.record_event(
                type="deploy_active",
                user_oid=dep["user_oid"],
                user_upn=dep["user_upn"],
                use_case=dep["use_case"],
                deployment_id=dep_id,
            )
            await _grant_view_access(dep)
            return

        ok, reason = executor_available()
        if not ok:
            raise RuntimeError(reason)
        db.update_deployment(
            dep_id,
            status="deploying",
            gh_run_url=portal_rg_url(dep["subscription_id"], dep["resource_group"]),
            detail="provisioning in Azure",
        )
        task = asyncio.create_task(asyncio.to_thread(_do_deploy, dep))
        # Stream coarse progress into the deployment's detail while the blocking
        # SDK call runs, so the UI can show a live log.
        while True:
            done, _ = await asyncio.wait({task}, timeout=5)
            if task in done:
                break
            try:
                progress = await asyncio.to_thread(_deploy_progress, dep)
                if progress:
                    db.update_deployment(dep_id, detail=progress)
            except Exception:  # progress is best-effort; never break the deploy
                pass
        await task  # re-raise any failure from the deploy thread
        db.update_deployment(dep_id, status="active", detail="deployment succeeded")

        db.record_event(
            type="deploy_active",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
        )
        await _grant_view_access(dep)
    except Exception as exc:
        # A failed deploy can still leave the resource group and partially
        # created resources behind; say so and log it so an operator can clean up.
        detail = (
            _clean_error(exc)
            + f" | Partial resources may remain in {dep['resource_group']}; "
            "use Clean up to remove them."
        )
        db.update_deployment(dep_id, status="failed", detail=detail[:500])
        db.record_event(
            type="deploy_failed",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
        )


async def run_destroy(dep: dict[str, Any]) -> None:
    dep_id = dep["id"]
    try:
        if _engine(dep) == "terraform":
            import terraform_exec

            ok, reason = terraform_exec.terraform_available()
            if not ok:
                raise RuntimeError(reason)
            db.update_deployment(dep_id, status="destroying", detail="running terraform destroy")
            await asyncio.to_thread(terraform_exec.tf_destroy, dep)
            db.update_deployment(
                dep_id,
                status="destroyed",
                destroyed_at=time.time(),
                detail="terraform destroy complete",
            )
            db.record_event(
                type="destroy_succeeded",
                user_oid=dep["user_oid"],
                user_upn=dep["user_upn"],
                use_case=dep["use_case"],
                deployment_id=dep_id,
            )
            return

        ok, reason = executor_available()
        if not ok:
            raise RuntimeError(reason)
        await asyncio.to_thread(_do_destroy, dep)
        db.update_deployment(
            dep_id,
            status="destroyed",
            destroyed_at=time.time(),
            detail="resource group deleted",
        )
        db.record_event(
            type="destroy_succeeded",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
        )
    except Exception as exc:
        db.update_deployment(dep_id, status="failed", detail=_clean_error(exc))
        db.record_event(
            type="destroy_failed",
            user_oid=dep["user_oid"],
            user_upn=dep["user_upn"],
            use_case=dep["use_case"],
            deployment_id=dep_id,
        )


def schedule(coro: Any) -> asyncio.Task:
    """Run an executor coroutine as a tracked background task."""
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
