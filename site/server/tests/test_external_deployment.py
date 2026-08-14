"""Tests for delegated external-subscription deployment plumbing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import HTTPStatusError, Request, Response

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import azure_deploy  # noqa: E402


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
