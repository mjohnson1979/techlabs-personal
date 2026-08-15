from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import scheduler  # noqa: E402


_SUBSCRIPTION_ID = "00000000-0000-0000-0000-000000000000"


def _deployment(resource_group: str, *, external: bool = False) -> dict[str, Any]:
    return {
        "id": f"dep-{resource_group}",
        "subscription_id": _SUBSCRIPTION_ID,
        "resource_group": resource_group,
        "user_oid": "user-oid",
        "user_upn": "user@example.com",
        "use_case": "web-app",
        "params": {"externalSubscription": external},
    }


def test_reconcile_marks_missing_resource_group_destroyed(monkeypatch) -> None:
    dep = _deployment("rg-missing")
    updates: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    monkeypatch.setattr(scheduler.db, "deployments_by_status", lambda _status: [dep])
    monkeypatch.setattr(scheduler.az, "resource_group_exists", lambda _dep: False)
    monkeypatch.setattr(
        scheduler.db,
        "update_deployment",
        lambda deployment_id, **kwargs: updates.append(
            {"deployment_id": deployment_id, **kwargs}
        ),
    )
    monkeypatch.setattr(scheduler.db, "record_event", lambda **event: events.append(event))

    asyncio.run(scheduler._reconcile_destroying())

    assert updates[0]["deployment_id"] == dep["id"]
    assert updates[0]["status"] == "destroyed"
    assert updates[0]["detail"] == "resource group already deleted; status reconciled"
    assert updates[0]["destroyed_at"] > 0
    assert events == [
        {
            "type": "destroy_reconciled",
            "user_oid": dep["user_oid"],
            "user_upn": dep["user_upn"],
            "use_case": dep["use_case"],
            "deployment_id": dep["id"],
        }
    ]


def test_reconcile_leaves_existing_resource_group_destroying(monkeypatch) -> None:
    dep = _deployment("rg-existing")
    updates: list[dict[str, Any]] = []

    monkeypatch.setattr(scheduler.db, "deployments_by_status", lambda _status: [dep])
    monkeypatch.setattr(scheduler.az, "resource_group_exists", lambda _dep: True)
    monkeypatch.setattr(
        scheduler.db,
        "update_deployment",
        lambda deployment_id, **kwargs: updates.append(
            {"deployment_id": deployment_id, **kwargs}
        ),
    )

    asyncio.run(scheduler._reconcile_destroying())

    assert updates == []


def test_reconcile_records_check_failure_and_continues(monkeypatch) -> None:
    first = _deployment("rg-error")
    second = _deployment("rg-missing")
    events: list[dict[str, Any]] = []

    monkeypatch.setattr(
        scheduler.db, "deployments_by_status", lambda _status: [first, second]
    )

    def _exists(dep: dict[str, Any]) -> bool:
        if dep["id"] == first["id"]:
            raise RuntimeError("Azure unavailable")
        return False

    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(scheduler.az, "resource_group_exists", _exists)
    monkeypatch.setattr(
        scheduler.db,
        "update_deployment",
        lambda deployment_id, **kwargs: updates.append(
            {"deployment_id": deployment_id, **kwargs}
        ),
    )
    monkeypatch.setattr(scheduler.db, "record_event", lambda **event: events.append(event))

    asyncio.run(scheduler._reconcile_destroying())

    assert updates[0]["deployment_id"] == second["id"]
    assert updates[0]["status"] == "destroyed"
    assert events[0]["type"] == "destroy_reconcile_failed"
    assert events[0]["deployment_id"] == first["id"]
    assert events[0]["meta"] == {"error": "Azure unavailable"}
    assert events[1]["type"] == "destroy_reconciled"


def test_reconcile_skips_external_deployments(monkeypatch) -> None:
    dep = _deployment("rg-external", external=True)
    checks: list[str] = []

    monkeypatch.setattr(scheduler.db, "deployments_by_status", lambda _status: [dep])
    monkeypatch.setattr(
        scheduler.az,
        "resource_group_exists",
        lambda deployment: checks.append(deployment["id"]) or False,
    )

    asyncio.run(scheduler._reconcile_destroying())

    assert checks == []
