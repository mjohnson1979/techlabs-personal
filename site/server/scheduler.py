"""Background cleanup scheduler.

Polls the deployment table on an interval and tears down any deployment whose
chosen end time has passed by running the in-process Azure executor
(azure_deploy.py). Runs as an asyncio task inside the single gunicorn worker
(sessions are in-memory, so the app already runs one worker — exactly one
scheduler, no leader election needed).
"""

from __future__ import annotations

import asyncio
import os
import time

import azure_deploy as az
import db

_INTERVAL = max(
    30, int(os.environ.get("GALLERY_CLEANUP_INTERVAL_SECONDS", "300").strip() or "300")
)


async def _reconcile_destroying() -> None:
    """Mark deleted managed resource groups as destroyed in the local DB."""
    for dep in db.deployments_by_status("destroying"):
        if (dep.get("params") or {}).get("externalSubscription"):
            continue
        try:
            exists = await asyncio.to_thread(az.resource_group_exists, dep)
            if exists:
                continue
            db.update_deployment(
                dep["id"],
                status="destroyed",
                destroyed_at=time.time(),
                detail="resource group already deleted; status reconciled",
            )
            db.record_event(
                type="destroy_reconciled",
                user_oid=dep["user_oid"],
                user_upn=dep["user_upn"],
                use_case=dep["use_case"],
                deployment_id=dep["id"],
            )
        except Exception as exc:
            db.record_event(
                type="destroy_reconcile_failed",
                user_oid=dep["user_oid"],
                user_upn=dep["user_upn"],
                use_case=dep["use_case"],
                deployment_id=dep["id"],
                meta={"error": str(exc)[:500]},
            )


async def _sweep_once() -> None:
    await _reconcile_destroying()
    for dep in db.due_deployments():
        try:
            db.update_deployment(dep["id"], status="destroying",
                                 detail="end time reached; tearing down")
            db.record_event(
                type="cleanup_triggered",
                user_oid=dep["user_oid"],
                user_upn=dep["user_upn"],
                use_case=dep["use_case"],
                deployment_id=dep["id"],
            )
            az.schedule(az.run_destroy(dep))
        except Exception as exc:  # never let one bad row kill the loop
            db.update_deployment(dep["id"], detail=f"cleanup error: {exc}"[:500])


async def run_scheduler(stop: asyncio.Event) -> None:
    """Loop until ``stop`` is set, sweeping due deployments every interval."""
    db.init_db()
    while not stop.is_set():
        try:
            await _sweep_once()
        except Exception:
            # Swallow — the next tick retries. (DB/network hiccups.)
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=_INTERVAL)
        except asyncio.TimeoutError:
            continue
