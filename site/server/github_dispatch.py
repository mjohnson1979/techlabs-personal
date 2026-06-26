"""Trigger GitHub Actions workflows that do the real Azure work.

The backend never holds Azure credentials. To deploy or tear down a use case it
dispatches a repository workflow (``workflow_dispatch``) which authenticates to
Azure with OIDC federated credentials and runs ``az`` (see
``.github/workflows/deploy-usecase.yml`` / ``destroy-usecase.yml``).

Configuration (env):
  GALLERY_GH_TOKEN   Fine-grained token / GitHub App token with ``actions:write``
                     on the repo. When unset, dispatch is skipped (local dev) and
                     the deployment is recorded as ``requested`` with a note.
  GALLERY_GH_REPO    ``owner/repo`` (default ``mcaps-microsoft/TechLabs``).
  GALLERY_GH_REF     Branch to run the workflow from (default ``main``).
  GALLERY_PUBLIC_URL Public base URL of this app, used to build the status
                     callback URL passed to the workflow.
"""

from __future__ import annotations

import os

import httpx

GITHUB_API = "https://api.github.com"
DEPLOY_WORKFLOW = "deploy-usecase.yml"
DESTROY_WORKFLOW = "destroy-usecase.yml"


def _repo() -> str:
    return os.environ.get("GALLERY_GH_REPO", "mcaps-microsoft/TechLabs").strip()


def _ref() -> str:
    return os.environ.get("GALLERY_GH_REF", "main").strip() or "main"


def _token() -> str:
    return os.environ.get("GALLERY_GH_TOKEN", "").strip()


def dispatch_configured() -> bool:
    return bool(_token())


def _callback_url(deployment_id: str) -> str:
    base = os.environ.get("GALLERY_PUBLIC_URL", "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/api/deployments/{deployment_id}/callback"


async def _dispatch(workflow: str, inputs: dict[str, str]) -> tuple[bool, str]:
    """POST a workflow_dispatch. Returns (ok, detail)."""
    token = _token()
    if not token:
        return False, "GitHub dispatch not configured (GALLERY_GH_TOKEN unset)"
    url = f"{GITHUB_API}/repos/{_repo()}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TechlabsGallery/1.0",
    }
    payload = {"ref": _ref(), "inputs": inputs}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
            resp = await http.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return False, f"GitHub dispatch failed: {exc}"
    if resp.status_code == 204:
        return True, "workflow dispatched"
    detail = ""
    try:
        detail = resp.json().get("message", "")
    except (ValueError, KeyError):
        detail = resp.text[:200]
    return False, f"GitHub dispatch HTTP {resp.status_code}: {detail}"


async def dispatch_deploy(deployment: dict) -> tuple[bool, str]:
    inputs = {
        "use_case": deployment["use_case"],
        "environment": deployment["environment"],
        "resource_group": deployment["resource_group"],
        "deployment_id": deployment["id"],
        "subscription_id": deployment.get("subscription_id", ""),
        "callback_url": _callback_url(deployment["id"]),
    }
    return await _dispatch(DEPLOY_WORKFLOW, inputs)


async def dispatch_destroy(deployment: dict) -> tuple[bool, str]:
    inputs = {
        "resource_group": deployment["resource_group"],
        "deployment_id": deployment["id"],
        "subscription_id": deployment.get("subscription_id", ""),
        "callback_url": _callback_url(deployment["id"]),
    }
    return await _dispatch(DESTROY_WORKFLOW, inputs)


def runs_url() -> str:
    """Best-effort link to the repo's Actions runs (shown in the UI)."""
    return f"https://github.com/{_repo()}/actions"
