"""Tests for the deployer view-access grant (Reader role on the RG).

The grant is a post-deploy, best-effort convenience: it must target the
deployment's resource-group scope as a User, be idempotent, be disableable via
env, and never let a failure escape (which would flip the deployment to failed).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import azure_deploy  # noqa: E402

import pytest  # noqa: E402


_DEP = {
    "id": "abc123",
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "resource_group": "rg-techlabs-webapp-abc123",
    "user_oid": "11111111-1111-1111-1111-111111111111",
    "user_upn": "user@contoso.com",
    "use_case": "web-app",
}


class _FakeRoleAssignments:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.raise_exc: Exception | None = None

    def create(self, scope, name, parameters):
        self.calls.append((scope, name, parameters))
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"id": name}


class _FakeAuthClient:
    def __init__(self) -> None:
        self.role_assignments = _FakeRoleAssignments()


@pytest.fixture
def fake_auth(monkeypatch):
    client = _FakeAuthClient()
    monkeypatch.setattr(azure_deploy, "_auth_client", lambda _sub: client)
    monkeypatch.setattr(azure_deploy, "_get_credential", lambda: object())
    # Resolve to the sign-in oid by default so the assignment assertions below
    # stay focused on RBAC; the guest-resolution paths are covered separately.
    monkeypatch.setattr(
        azure_deploy, "_resolve_principal_id", lambda oid, email, url: oid or None
    )
    return client


def test_grant_targets_rg_scope_as_user_reader(fake_auth) -> None:
    assert azure_deploy.grant_view_access(_DEP) is True

    assert len(fake_auth.role_assignments.calls) == 1
    scope, _name, params = fake_auth.role_assignments.calls[0]
    assert scope == (
        "/subscriptions/00000000-0000-0000-0000-000000000000"
        "/resourceGroups/rg-techlabs-webapp-abc123"
    )
    assert params["principal_id"] == _DEP["user_oid"]
    assert params["principal_type"] == "User"
    assert params["role_definition_id"].endswith(azure_deploy._READER_ROLE_ID)


def test_grant_is_idempotent_on_existing_assignment(fake_auth) -> None:
    from azure.core.exceptions import ResourceExistsError

    fake_auth.role_assignments.raise_exc = ResourceExistsError("already there")
    assert azure_deploy.grant_view_access(_DEP) is True  # idempotent success


def test_grant_skips_without_principal(fake_auth) -> None:
    assert azure_deploy.grant_view_access({**_DEP, "user_oid": ""}) is False
    assert fake_auth.role_assignments.calls == []


def test_grant_name_is_deterministic(fake_auth) -> None:
    azure_deploy.grant_view_access(_DEP)
    azure_deploy.grant_view_access(_DEP)
    names = {name for _scope, name, _params in fake_auth.role_assignments.calls}
    assert len(names) == 1  # same (scope, principal) -> same assignment name


def test_repeat_deployments_create_distinct_resource_group_assignments(
    fake_auth, monkeypatch
) -> None:
    monkeypatch.setattr(
        azure_deploy, "_resolve_principal_id", lambda _oid, _email, _url: "guest-oid"
    )

    first_dep = {**_DEP, "resource_group": "rg-techlabs-webapp-111111"}
    second_dep = {**_DEP, "resource_group": "rg-techlabs-webapp-222222"}

    assert azure_deploy.grant_view_access(first_dep) is True
    assert azure_deploy.grant_view_access(second_dep) is True

    scopes = [scope for scope, _name, _params in fake_auth.role_assignments.calls]
    names = [name for _scope, name, _params in fake_auth.role_assignments.calls]

    assert scopes == [
        "/subscriptions/00000000-0000-0000-0000-000000000000"
        "/resourceGroups/rg-techlabs-webapp-111111",
        "/subscriptions/00000000-0000-0000-0000-000000000000"
        "/resourceGroups/rg-techlabs-webapp-222222",
    ]
    assert len(set(names)) == 2


def test_best_effort_disabled_by_env(fake_auth, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_GRANT_VIEW_ACCESS", "false")
    asyncio.run(azure_deploy._grant_view_access(_DEP))
    assert fake_auth.role_assignments.calls == []


def test_best_effort_records_skipped_when_no_principal(fake_auth, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_GRANT_VIEW_ACCESS", "true")
    recorded: list[str] = []
    monkeypatch.setattr(
        azure_deploy.db, "record_event", lambda **kw: recorded.append(kw["type"])
    )

    asyncio.run(azure_deploy._grant_view_access({**_DEP, "user_oid": ""}))
    assert fake_auth.role_assignments.calls == []
    assert recorded == ["view_access_skipped"]


def test_best_effort_records_deployment_scope(fake_auth, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_GRANT_VIEW_ACCESS", "true")
    recorded: list[dict] = []
    monkeypatch.setattr(
        azure_deploy.db, "record_event", lambda **kw: recorded.append(kw)
    )

    asyncio.run(azure_deploy._grant_view_access(_DEP))

    assert recorded == [
        {
            "type": "view_access_granted",
            "user_oid": "11111111-1111-1111-1111-111111111111",
            "user_upn": "user@contoso.com",
            "use_case": "web-app",
            "deployment_id": "abc123",
            "meta": {
                "subscriptionId": "00000000-0000-0000-0000-000000000000",
                "resourceGroup": "rg-techlabs-webapp-abc123",
            },
        }
    ]


def test_best_effort_swallows_failure(fake_auth, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_GRANT_VIEW_ACCESS", "true")
    fake_auth.role_assignments.raise_exc = RuntimeError("boom")

    recorded: list[str] = []
    monkeypatch.setattr(
        azure_deploy.db, "record_event", lambda **kw: recorded.append(kw["type"])
    )

    asyncio.run(azure_deploy._grant_view_access(_DEP))  # must not raise
    assert recorded == ["view_access_grant_failed"]


def test_resolve_uses_oid_when_principal_exists(monkeypatch) -> None:
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: True)
    monkeypatch.setattr(
        azure_deploy, "_find_directory_user", lambda _e: pytest.fail("no lookup")
    )
    monkeypatch.setattr(
        azure_deploy, "_invite_guest", lambda _e, _u: pytest.fail("no invite")
    )
    pid = azure_deploy._resolve_principal_id("home-oid", "u@microsoft.com", "http://rg")
    assert pid == "home-oid"


def test_resolve_uses_existing_guest(monkeypatch) -> None:
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    monkeypatch.setattr(azure_deploy, "_find_directory_user", lambda _e: "guest-oid")
    monkeypatch.setattr(
        azure_deploy, "_invite_guest", lambda _e, _u: pytest.fail("no invite")
    )
    pid = azure_deploy._resolve_principal_id("home-oid", "u@microsoft.com", "http://rg")
    assert pid == "guest-oid"


def test_existing_guest_is_reused_for_new_deployment_access(monkeypatch) -> None:
    client = _FakeAuthClient()
    invite_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(azure_deploy, "_auth_client", lambda _sub: client)
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    monkeypatch.setattr(azure_deploy, "_find_directory_user", lambda _e: "guest-oid")
    monkeypatch.setattr(
        azure_deploy,
        "_invite_guest",
        lambda email, url: invite_calls.append((email, url)),
    )

    dep = {**_DEP, "resource_group": "rg-techlabs-webapp-newdeploy"}

    assert azure_deploy.grant_view_access(dep) is True
    assert invite_calls == []
    assert len(client.role_assignments.calls) == 1
    scope, _name, params = client.role_assignments.calls[0]
    assert scope.endswith("/resourceGroups/rg-techlabs-webapp-newdeploy")
    assert params["principal_id"] == "guest-oid"
    assert params["principal_type"] == "User"


def test_resolve_invites_new_guest(monkeypatch) -> None:
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    monkeypatch.setattr(azure_deploy, "_find_directory_user", lambda _e: None)
    captured: dict[str, str] = {}

    def _invite(email, url):
        captured["email"] = email
        captured["url"] = url
        return "new-guest-oid"

    monkeypatch.setattr(azure_deploy, "_invite_guest", _invite)
    pid = azure_deploy._resolve_principal_id(
        "home-oid", "u@microsoft.com", "http://rg/overview"
    )
    assert pid == "new-guest-oid"
    assert captured == {"email": "u@microsoft.com", "url": "http://rg/overview"}


def test_resolve_skips_invite_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_INVITE_GUEST", "false")
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    monkeypatch.setattr(azure_deploy, "_find_directory_user", lambda _e: None)
    monkeypatch.setattr(
        azure_deploy, "_invite_guest", lambda _e, _u: pytest.fail("no invite")
    )
    pid = azure_deploy._resolve_principal_id("home-oid", "u@microsoft.com", "http://rg")
    assert pid is None


def test_resolve_without_email_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(azure_deploy, "_principal_exists", lambda _oid: False)
    pid = azure_deploy._resolve_principal_id("home-oid", "", "http://rg")
    assert pid is None


def test_delegated_credential_returns_access_token(monkeypatch) -> None:
    credential = azure_deploy.DelegatedTokenCredential("arm-token", 2_000_000_000)
    token = credential.get_token("https://management.azure.com/.default")
    assert token.token == "arm-token"
    assert token.expires_on == 2_000_000_000


def test_delegated_credential_rejects_expired_token() -> None:
    credential = azure_deploy.DelegatedTokenCredential("expired", 0)
    with pytest.raises(RuntimeError, match="access token expired"):
        credential.get_token("https://management.azure.com/.default")
