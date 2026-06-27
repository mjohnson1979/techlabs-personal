"""Execute Azure deployments directly with the App Service managed identity.

Option B: the backend is BOTH the control plane and the executor. It holds no
secrets — it authenticates to Azure with ``DefaultAzureCredential`` (the App
Service system-assigned managed identity in production; ``az login`` / developer
credentials locally) and submits ARM deployments via the resource-management
SDK. No GitHub PAT, no callback token.

Templates are precompiled from Bicep to ARM JSON by ``tools/Build-Templates.ps1``
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
import os
import time
from pathlib import Path
from typing import Any

import db

SERVER_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SERVER_DIR / "_templates"

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
    """Azure portal link to the deployment's resource group."""
    return (
        "https://portal.azure.com/#@/resource/subscriptions/"
        f"{subscription_id}/resourceGroups/{resource_group}/overview"
    )


# --------------------------------------------------------------------------- #
# Template loading (precompiled ARM JSON)
# --------------------------------------------------------------------------- #
def _template_path(use_case: str, environment: str) -> Path:
    return TEMPLATES_DIR / use_case / f"{environment}.json"


def load_template(use_case: str, environment: str) -> tuple[dict, dict]:
    path = _template_path(use_case, environment)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No compiled template for {use_case}/{environment}. "
            "Run tools/Build-Templates.ps1 to generate site/server/_templates."
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


def _get_credential() -> Any:
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential

        # Managed identity in App Service; az/developer creds locally. Never the
        # interactive browser flow (this runs headless on the server).
        _credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
    return _credential


def _client(subscription_id: str) -> Any:
    # The unified ``ResourceManagementClient`` (top-level import) exposes the
    # ``deployments``, ``deployment_operations`` and ``resource_groups`` operation
    # groups the executor relies on. This holds for azure-mgmt-resource 23.x
    # (pinned ``>=23.1,<24`` in requirements.txt). v24+ split deployments into a
    # separate client and dropped them from the resources client.
    from azure.mgmt.resource import ResourceManagementClient

    return ResourceManagementClient(_get_credential(), subscription_id)


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
def _do_deploy(dep: dict[str, Any]) -> None:
    subscription_id = dep["subscription_id"]
    resource_group = dep["resource_group"]
    template, parameters = load_template(dep["use_case"], dep["environment"])

    client = _client(subscription_id)
    client.resource_groups.create_or_update(
        resource_group,
        {
            "location": _location(),
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


def _deploy_progress(dep: dict[str, Any]) -> str | None:
    """Summarise in-flight ARM deployment operations for the live log."""
    client = _client(dep["subscription_id"])
    name = f"techlabs-{dep['id']}"
    try:
        ops = list(client.deployment_operations.list(dep["resource_group"], name))
    except Exception:
        return None
    states: dict[str, str] = {}
    for op in ops:
        props = getattr(op, "properties", None)
        target = getattr(props, "target_resource", None)
        rtype = getattr(target, "resource_type", None)
        if rtype:
            states[rtype] = getattr(props, "provisioning_state", "") or ""
    if not states:
        return None
    done = sum(1 for s in states.values() if s in ("Succeeded", "Failed"))
    parts = [f"{rt.split('/')[-1]}: {st}" for rt, st in list(states.items())[:6]]
    return f"{done}/{len(states)} resources — " + ", ".join(parts)



def _do_destroy(dep: dict[str, Any]) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    client = _client(dep["subscription_id"])
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


# --------------------------------------------------------------------------- #
# Async orchestration
# --------------------------------------------------------------------------- #
async def run_deploy(dep: dict[str, Any]) -> None:
    dep_id = dep["id"]
    try:
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
    except Exception as exc:
        db.update_deployment(dep_id, status="failed", detail=_clean_error(exc))
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
