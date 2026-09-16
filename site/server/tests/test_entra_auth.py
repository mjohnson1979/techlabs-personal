"""Tests for memory-only external Azure authorization."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import entra_auth  # noqa: E402


@pytest.mark.parametrize("enabled,tenant,accepted", [
    ("false", "organization-tenant", False),
    ("true", "", False),
    ("true", "different-tenant", False),
    ("true", "ORGANIZATION-TENANT", True),
])
def test_easy_auth_requires_trusted_mode_and_tenant(
    monkeypatch: pytest.MonkeyPatch, enabled: str, tenant: str, accepted: bool
) -> None:
    monkeypatch.setenv("GALLERY_EASY_AUTH", enabled)
    monkeypatch.setenv("GALLERY_ENTRA_TENANT_ID", "organization-tenant")
    principal = base64.b64encode(json.dumps({"claims": [
        {"typ": "oid", "val": "organization-oid"},
        {"typ": "tid", "val": tenant},
    ]}).encode())
    request = Request({"type": "http", "headers": [
        (b"x-ms-client-principal", principal),
    ]})
    assert (entra_auth.identity_from_easy_auth(request) is not None) is accepted


def test_easy_auth_does_not_accept_headers_without_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    request = Request({"type": "http", "headers": [
        (b"x-ms-client-principal-id", b"forged-oid"),
        (b"x-ms-client-principal-name", b"forged@example.com"),
    ]})
    assert entra_auth.identity_from_easy_auth(request) is None


@pytest.mark.parametrize("claims", [None, {}, "invalid", [{"typ": "oid", "val": None}]])
def test_easy_auth_rejects_malformed_claims(
    monkeypatch: pytest.MonkeyPatch, claims: object,
) -> None:
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    principal = base64.b64encode(json.dumps({"claims": claims}).encode())
    request = Request({"type": "http", "headers": [(b"x-ms-client-principal", principal)]})
    assert entra_auth.identity_from_easy_auth(request) is None


@pytest.mark.parametrize("easy_auth,tenant", [("true", "home"), ("false", "")])
def test_api_rejects_session_fallback_or_missing_tenant(
    monkeypatch: pytest.MonkeyPatch, easy_auth: str, tenant: str
) -> None:
    import identity

    monkeypatch.setenv("GALLERY_EASY_AUTH", easy_auth)
    monkeypatch.setenv("GALLERY_ENTRA_TENANT_ID", "home")
    application = FastAPI()
    application.state.session_store = entra_auth.SessionStore()
    application.state.session_store.set_identity(
        "session", entra_auth.Identity("user", "user@example.com", tenant)
    )
    request = Request({"type": "http", "app": application, "headers": [
        (b"cookie", f"{entra_auth.COOKIE_NAME}=session".encode()),
    ]})
    assert identity.current_user_optional(request) is None


def _id_token() -> str:
    claims = {
        "oid": "external-oid",
        "preferred_username": "external@example.com",
        "tid": "external-tenant",
        "name": "External User",
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class _Response:
    def __init__(self, body: dict[str, object], status_code: int = 200) -> None:
        self._body = body
        self.content = b"json"
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._body


class _HttpClient:
    responses: list[_Response] = []
    get_responses: list[_Response] = []
    calls: list[tuple[str, dict[str, str]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_HttpClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, data: dict[str, str]) -> _Response:
        self.calls.append((url, data))
        return self.responses.pop(0)

    async def get(self, url: str) -> _Response:
        self.calls.append((url, {}))
        return self.get_responses.pop(0)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _HttpClient.responses = []
    _HttpClient.get_responses = []
    _HttpClient.calls = []
    monkeypatch.setattr(entra_auth.httpx, "AsyncClient", _HttpClient)


def test_external_device_code_uses_azure_cli_client_and_arm_scope() -> None:
    _HttpClient.responses.append(
        _Response(
            {
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
            }
        )
    )

    asyncio.run(entra_auth.ExternalAzureClient().begin_device_code())

    url, data = _HttpClient.calls[0]
    assert "/organizations/oauth2/v2.0/devicecode" in url
    assert data["client_id"] == entra_auth.AZURE_CLI_CLIENT_ID
    assert entra_auth.ARM_SCOPE in data["scope"]


def test_gallery_authorization_url_never_requests_arm_scope() -> None:
    url = entra_auth.EntraClient(
        tenant_id="gallery-tenant", client_id="gallery-client"
    ).build_authorize_url(
        "https://gallery.example.com/", "state", "challenge"
    )

    assert entra_auth.ARM_SCOPE not in url
    assert "openid+profile+offline_access" in url


def test_external_device_poll_returns_isolated_memory_authorization() -> None:
    _HttpClient.responses.append(
        _Response(
            {
                "id_token": _id_token(),
                "access_token": "external-access-token",
                "refresh_token": "external-refresh-token",
                "expires_in": 3600,
            }
        )
    )

    authorization = asyncio.run(
        entra_auth.ExternalAzureClient().poll_device_code("device-code")
    )

    assert authorization is not None
    assert authorization.identity.tenant_id == "external-tenant"
    store = entra_auth.SessionStore()
    store.set_identity(
        "session", entra_auth.Identity("gallery-oid", "gallery@example.com", "home")
    )
    store.set_external_azure("session", authorization)
    session = store.get("session")
    assert session is not None
    assert session.identity is not authorization.identity
    assert session.external_azure is authorization


def test_discovers_subscription_tenant_without_exposing_tokens() -> None:
    authorization = entra_auth.ExternalAzureAuthorization(
        identity=entra_auth.Identity(
            "external-oid", "external@example.com", "external-tenant"
        ),
        access_token="home-access-token",
        refresh_token="home-refresh-token",
        expires_at=2_000_000_000,
        tenant_tokens={"external-tenant": ("home-access-token", 2_000_000_000)},
    )
    _HttpClient.get_responses.extend(
        [
            _Response({"value": [{"tenantId": "target-tenant"}]}),
            _Response({"value": []}),
            _Response(
                {
                    "value": [
                        {
                            "subscriptionId": "00000000-0000-0000-0000-000000000001",
                            "displayName": "External Test",
                        }
                    ]
                }
            ),
        ]
    )
    _HttpClient.responses.append(
        _Response(
            {
                "access_token": "target-access-token",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
            }
        )
    )

    asyncio.run(
        entra_auth.ExternalAzureClient().discover_subscriptions(authorization)
    )

    subscription = authorization.subscriptions[
        "00000000-0000-0000-0000-000000000001"
    ]
    assert subscription == {
        "subscriptionId": "00000000-0000-0000-0000-000000000001",
        "displayName": "External Test",
        "tenantId": "target-tenant",
    }
    assert "token" not in json.dumps(subscription).lower()


def test_external_authorization_endpoints_never_return_tokens() -> None:
    authorization = entra_auth.ExternalAzureAuthorization(
        identity=entra_auth.Identity(
            "external-oid", "external@example.com", "external-tenant"
        ),
        access_token="external-access-token",
        refresh_token="external-refresh-token",
        expires_at=2_000_000_000,
        subscriptions={
            "00000000-0000-0000-0000-000000000001": {
                "subscriptionId": "00000000-0000-0000-0000-000000000001",
                "displayName": "External Test",
                "tenantId": "external-tenant",
            }
        },
        tenant_tokens={
            "external-tenant": ("external-access-token", 2_000_000_000)
        },
    )

    class ExternalClient:
        async def begin_device_code(self) -> dict[str, object]:
            return {
                "device_code": "secret-device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
            }

        async def poll_device_code(
            self, _device_code: str
        ) -> entra_auth.ExternalAzureAuthorization:
            return authorization

        async def discover_subscriptions(
            self, _authorization: entra_auth.ExternalAzureAuthorization
        ) -> None:
            return None

    app = FastAPI()
    app.state.session_store = entra_auth.SessionStore()
    app.state.session_store.set_identity(
        "session-id",
        entra_auth.Identity("gallery-oid", "gallery@example.com", "gallery-tenant"),
    )
    app.state.external_azure_client = ExternalClient()
    app.state.external_azure_device = {}
    app.state.entra_device = {}
    app.state.entra_pkce = {}
    app.include_router(entra_auth.build_router(secure_cookie=False))
    client = TestClient(app)
    client.cookies.set(entra_auth.COOKIE_NAME, "session-id")

    start = client.post("/api/auth/entra/external/start")
    assert start.status_code == 200
    assert "secret-device-code" not in start.text
    poll = client.post("/api/auth/entra/external/poll")
    assert poll.json()["status"] == "complete"
    status = client.get("/api/auth/entra/external/status")
    assert status.status_code == 200
    encoded = status.text.lower()
    assert "external-access-token" not in encoded
    assert "external-refresh-token" not in encoded
    assert "external test" in encoded

    session = app.state.session_store.get("session-id")
    assert session is not None
    assert session.identity is not authorization.identity
    assert session.external_azure is authorization