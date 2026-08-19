"""Tests for delegated external-subscription deployment plumbing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import HTTPStatusError, Request, Response

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import azure_deploy  # noqa: E402
import api  # noqa: E402
import db  # noqa: E402
from identity import CurrentUser, require_user  # noqa: E402


def test_delegated_credential_returns_arm_token() -> None:
    credential = azure_deploy.DelegatedTokenCredential("arm-token", 2_000_000_000)
    token = credential.get_token("https://management.azure.com/.default")
    assert token.token == "arm-token"
    assert token.expires_on == 2_000_000_000


def test_delegated_credential_rejects_expired_token() -> None:
    credential = azure_deploy.DelegatedTokenCredential("expired", 0)
    with pytest.raises(RuntimeError, match="access token expired"):
        credential.get_token("https://management.azure.com/.default")


def test_default_deployment_credential_uses_managed_identity(monkeypatch) -> None:
    managed_identity = object()
    monkeypatch.setattr(azure_deploy, "_get_credential", lambda: managed_identity)
    assert azure_deploy._credential_for_deployment({}) is managed_identity


def test_external_deployment_credential_uses_delegated_token() -> None:
    credential = azure_deploy._credential_for_deployment(
        {
            "_delegated_arm_token": "arm-token",
            "_delegated_arm_expires_at": 2_000_000_000,
        }
    )
    assert isinstance(credential, azure_deploy.DelegatedTokenCredential)


def test_external_deployment_never_falls_back_to_managed_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        azure_deploy,
        "_get_credential",
        lambda: pytest.fail("managed identity must not authorize external deployment"),
    )

    with pytest.raises(RuntimeError, match="requires delegated Azure authorization"):
        azure_deploy._credential_for_deployment(
            {"params": {"externalSubscription": True}}
        )


def test_invitation_proceeds_when_directory_lookup_is_forbidden(monkeypatch) -> None:
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    response = Response(
        403, request=Request("GET", "https://graph.microsoft.com/v1.0/users")
    )
    error = HTTPStatusError(
        "forbidden", request=response.request, response=response
    )
    monkeypatch.setattr(
        azure_deploy,
        "_find_directory_user",
        lambda _email: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(azure_deploy, "_invite_guest", lambda _e, _u: "guest-oid")

    assert azure_deploy._resolve_principal_id(
        "home-oid", "u@microsoft.com", "http://rg"
    ) == "guest-oid"


def test_portal_resource_group_link_uses_subscription_tenant(monkeypatch) -> None:
    monkeypatch.setenv("ARM_TENANT_ID", "2929f0bd-f87b-47ae-bb73-ec22b65de557")

    url = azure_deploy.portal_rg_url(
        "8ad2703f-069c-4c1a-8f4f-850c2a4c2676", "rg-techlabs-web-app-dev-5d6d6f"
    )

    assert url.startswith(
        "https://portal.azure.com/#@2929f0bd-f87b-47ae-bb73-ec22b65de557/"
    )


def test_existing_resource_group_deployment_skips_group_creation(monkeypatch) -> None:
    class ResourceGroups:
        def __init__(self) -> None:
            self.got: list[str] = []

        def get(self, name: str) -> object:
            self.got.append(name)
            return object()

        def create_or_update(self, *_args, **_kwargs) -> None:
            raise AssertionError("existing resource group must not be created or tagged")

    class Poller:
        def result(self) -> None:
            return None

    class Deployments:
        def begin_create_or_update(self, *_args, **_kwargs) -> Poller:
            return Poller()

    client = type(
        "Client",
        (),
        {"resource_groups": ResourceGroups(), "deployments": Deployments()},
    )()
    monkeypatch.setattr(azure_deploy, "_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(azure_deploy, "load_template", lambda *_args: ({}, {}))

    azure_deploy._do_deploy(
        {
            "id": "deployment-id",
            "use_case": "web-app",
            "environment": "dev",
            "subscription_id": "subscription-id",
            "resource_group": "existing-rg",
            "cleanup_policy": "none",
            "params": {},
        }
    )

    assert client.resource_groups.got == ["existing-rg"]


def test_external_existing_resource_group_has_no_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "external.db"))
    db._CONN = None
    connection = db.init_db()
    app = FastAPI()
    app.include_router(api.build_api_router())
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        oid="user-oid",
        upn="user@example.com",
        name="User",
        tenant_id="tenant-id",
        role="guest",
    )

    async def acquire_token(*_args, **_kwargs) -> tuple[str, int]:
        return "arm-token", 3600

    monkeypatch.setattr(
        api, "acquire_external_arm_token_for_subscription", acquire_token
    )
    monkeypatch.setattr(
        api,
        "_catalog_items",
        lambda: {
            "web-app": {
                "path": "web-app",
                "hasProd": True,
                "engines": ["bicep"],
                "parameters": [],
            }
        },
    )
    monkeypatch.setattr(api.az, "get_resource_group", lambda _dep: object())
    monkeypatch.setattr(api.az, "schedule", lambda _coro: _coro.close())

    client = TestClient(app)
    response = client.post(
        "/api/deployments",
        json={
            "useCase": "web-app",
            "externalSubscription": True,
            "subscriptionId": "00000000-0000-0000-0000-000000000001",
            "resourceGroupName": "shared-external-rg",
            "lifetimeHours": 1,
        },
    )

    assert response.status_code == 200
    deployment = response.json()["deployment"]
    assert deployment["resourceGroup"] == "shared-external-rg"
    assert deployment["cleanupPolicy"] == "none"
    assert deployment["externallyManaged"] is True
    assert deployment["endAt"] is None
    assert db.due_deployments(now=10**12) == []

    destroy = client.post(f"/api/deployments/{deployment['id']}/destroy")
    assert destroy.status_code == 409
    assert "managed outside TechLabs" in destroy.json()["detail"]

    connection.close()
    db._CONN = None
