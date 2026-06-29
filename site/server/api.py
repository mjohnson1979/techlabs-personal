"""Gallery control-plane API: identity, telemetry, and deployments.

The backend is the system of record AND the executor. It records who deployed
what and when, schedules teardown at the chosen end time, and runs the actual
Azure deployments in-process using the App Service managed identity (see
azure_deploy.py). No GitHub PAT, no stored Azure secret.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field

import azure_deploy as az
import db
import github_intake
import submissions
from identity import (
    CurrentUser,
    require_admin,
    require_user,
    verify_same_origin,
)

SITE_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = SITE_ROOT / "catalog.json"

DEFAULT_SUBSCRIPTION = os.environ.get(
    "GALLERY_SUBSCRIPTION_ID", "8ad2703f-069c-4c1a-8f4f-850c2a4c2676"
).strip()
DEFAULT_MAX_LIFETIME_HOURS = int(
    os.environ.get("GALLERY_MAX_LIFETIME_HOURS", "48").strip() or "48"
)
MIN_LIFETIME_MINUTES = 15
_VALID_EVENT_TYPES = {"view", "search", "modal_open"}
_ALLOWED_ENVS = {"dev", "prod"}

# --------------------------------------------------------------------------- #
# Catalog lookup (validates deploy requests against real use cases)
# --------------------------------------------------------------------------- #
_catalog_cache: dict[str, Any] = {"mtime": 0.0, "items": {}}


def _catalog_items() -> dict[str, dict[str, Any]]:
    """Return {path: item} from catalog.json, reloaded when the file changes."""
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


def _sanitize(part: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")


def _resource_group_name(use_case: str, environment: str, short_id: str) -> str:
    base = f"rg-techlabs-{_sanitize(use_case)}-{_sanitize(environment)}-{short_id}"
    return base[:88]  # Azure RG name max length is 90.


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _deployment_view(d: dict[str, Any]) -> dict[str, Any]:
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
        "endAt": _iso(d["end_at"]),
        "destroyedAt": _iso(d.get("destroyed_at")),
        "ghRunUrl": d["gh_run_url"],
        "detail": d["detail"],
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
    # Azure region to deploy into (e.g. "centralus"). Empty falls back to the
    # server default (GALLERY_DEPLOY_LOCATION).
    location: str = ""
    # Either an explicit end time (ISO 8601) or a lifetime in hours.
    endAt: str = ""
    lifetimeHours: float | None = None


class RoleIn(BaseModel):
    upn: str
    role: str  # 'admin' | 'guest'


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
            "upn": user.upn,
            "name": user.name,
            "oid": user.oid,
            "role": user.role,
            "isAdmin": user.is_admin,
            "deploymentCount": len(mine),
            "activeDeployments": active,
            "maxLifetimeHours": DEFAULT_MAX_LIFETIME_HOURS,
        }

    # -------------------------------------------------------------- events ---
    @router.post("/events", status_code=204)
    async def post_event(
        body: EventIn,
        request: Request,
        user: CurrentUser = Depends(require_user),
    ) -> None:
        verify_same_origin(request)
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

    # --------------------------------------------------------- deployments ---
    @router.post("/deployments")
    async def create_deployment(
        body: DeployIn,
        request: Request,
        user: CurrentUser = Depends(require_user),
    ) -> dict[str, Any]:
        verify_same_origin(request)
        items = _catalog_items()
        item = items.get(body.useCase)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown use case")
        env = body.environment.lower().strip()
        if env not in _ALLOWED_ENVS:
            raise HTTPException(status_code=400, detail="environment must be dev or prod")
        if env == "prod" and not item.get("hasProd", False):
            raise HTTPException(
                status_code=400, detail="this use case has no prod parameters"
            )

        now = time.time()
        end_at = _resolve_end_at(body, now, user)

        location = (body.location or "").strip().lower()
        if location and not re.fullmatch(r"[a-z][a-z0-9]{2,30}", location):
            raise HTTPException(status_code=400, detail="invalid location")

        dep_id = secrets.token_hex(8)
        short_id = dep_id[:6]
        rg = _resource_group_name(body.useCase, env, short_id)
        db.create_deployment(
            deployment_id=dep_id,
            use_case=body.useCase,
            environment=env,
            user_oid=user.oid,
            user_upn=user.upn,
            user_name=user.name,
            subscription_id=DEFAULT_SUBSCRIPTION,
            resource_group=rg,
            end_at=end_at,
            params={"template": item.get("template", ""), "location": location},
        )
        db.record_event(
            type="deploy_requested",
            user_oid=user.oid,
            user_upn=user.upn,
            use_case=body.useCase,
            deployment_id=dep_id,
            meta={"environment": env, "resourceGroup": rg},
        )

        dep = db.get_deployment(dep_id)
        assert dep is not None
        # Hand off to the in-process executor (managed identity + ARM SDK). The
        # provisioning runs as a background task; status flows to 'active' or
        # 'failed' as it completes.
        db.update_deployment(
            dep_id,
            status="deploying",
            gh_run_url=az.portal_rg_url(DEFAULT_SUBSCRIPTION, rg),
            detail="queued for provisioning",
        )
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
        if dep["status"] in ("destroyed", "destroying"):
            return {"deployment": _deployment_view(dep),
                    "detail": f"already {dep['status']}"}
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
        detail = "Uploaded; queued for a maintainer to review."
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
            except Exception as exc:  # push is best-effort; keep the staged copy
                db.update_submission(
                    staged["id"],
                    detail=f"Staged; branch creation failed ({type(exc).__name__}). "
                    "A maintainer can download the bundle instead.",
                )

        sub = db.get_submission(staged["id"])
        assert sub is not None
        return {
            "submission": _submission_view(sub),
            "detail": detail,
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
        submissions.discard(sub)
        db.update_submission(submission_id, status="discarded", detail="discarded by admin")
        db.record_event(
            type="use_case_submission_discarded",
            user_oid=admin.oid,
            user_upn=admin.upn,
            use_case=sub["slug"],
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
