"""Offline GitHub device-flow tests; no real OAuth credentials."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
import httpx

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import github_device
import db
import setup_github
import setup_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import CurrentUser, require_admin


@pytest.mark.parametrize("status", [200, 400, 401, 403])
@pytest.mark.parametrize("code,expected", [
    ("device_flow_disabled", "enable Device Flow"),
    ("incorrect_client_credentials", "Client ID"),
    ("invalid_client", "Client ID"),
])
def test_device_start_errors_are_actionable_and_private(monkeypatch, status, code, expected):
    def handle(request):
        assert request.url == "https://github.com/login/device/code"
        return httpx.Response(status, json={"error": code, "error_description": "private-response", "device_code": "private-code"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(github_device.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ValueError, match=expected) as failure:
        asyncio.run(github_device.oauth_request("/login/device/code", {"client_id": "organization-client"}))
    assert "private-" not in str(failure.value)


@pytest.mark.parametrize("status,payload", [
    (403, {"error": "unrecognized", "error_description": "private-response"}),
    (429, {"error": "device_flow_disabled"}),
    (503, {"error": "device_flow_disabled"}),
])
def test_unrecognized_and_service_http_failures_remain_http_errors(monkeypatch, status, payload):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    monkeypatch.setattr(github_device.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(httpx.HTTPStatusError) as failure:
        asyncio.run(github_device.oauth_request("/login/device/code", {"client_id": "organization-client"}))
    assert failure.value.response.status_code == status


@pytest.mark.parametrize("path,payload", [
    ("/login/device/code", {"user_code": "ABCD-EFGH", "device_code": "private-device-code"}),
    ("/login/oauth/access_token", {"error": "authorization_pending"}),
    ("/login/oauth/access_token", {"access_token": "private-token", "token_type": "bearer"}),
])
def test_oauth_transport_preserves_success_responses(monkeypatch, path, payload):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(github_device.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    assert asyncio.run(github_device.oauth_request(path, {"client_id": "organization-client"})) == payload


@pytest.mark.parametrize("body,expected", [
    (b"<html>private-response</html>", "invalid authorization response"),
    (b"[\"private-response\"]", "Invalid GitHub authorization response"),
    (b"private-response" * 2000, "exceeds the limit"),
])
def test_oauth_transport_rejects_invalid_or_oversized_content_safely(monkeypatch, body, expected):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    monkeypatch.setattr(github_device.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(ValueError, match=expected) as failure:
        asyncio.run(github_device.oauth_request("/login/device/code", {"client_id": "organization-client"}))
    assert "private-response" not in str(failure.value)


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setenv("GALLERY_GITHUB_OAUTH_CLIENT_ID", "organization-client")
    monkeypatch.setattr(github_device, "_flows", {})
    clock = [1000.0]
    monkeypatch.setattr(github_device.time, "time", lambda: clock[0])
    calls = []
    replies = [{"device_code": "private-device-code", "user_code": "ABCD-EFGH", "verification_uri": "https://github.com/login/device", "interval": 5, "expires_in": 900}]
    async def oauth(path, data):
        calls.append((path, data))
        return replies.pop(0)
    monkeypatch.setattr(github_device, "oauth_request", oauth)
    return clock, calls, replies


def test_device_code_is_not_exposed_and_flow_is_admin_bound(device):
    result = asyncio.run(github_device.start("admin"))
    assert "private-device-code" not in json.dumps(result)
    with pytest.raises(ValueError): asyncio.run(github_device.poll("other", result["flowId"]))
    assert device[1][0][1] == {"client_id": "organization-client", "scope": "repo read:org workflow"}


def test_poll_respects_interval_slowdown_and_keeps_token_private(device):
    clock, calls, replies = device
    result = asyncio.run(github_device.start("admin"))
    identifier = result["flowId"]
    assert asyncio.run(github_device.poll("admin", identifier))["status"] == "pending"
    assert len(calls) == 1
    clock[0] += 5
    replies.append({"error": "slow_down"})
    assert asyncio.run(github_device.poll("admin", identifier))["interval"] == 10
    clock[0] += 10
    replies.append({"access_token": "private-access-token", "token_type": "bearer", "scope": "repo,read:org", "expires_in": 28800, "refresh_token": "private-refresh-token"})
    result = asyncio.run(github_device.poll("admin", identifier))
    assert result == {"status": "authorized"}
    assert "private-" not in json.dumps(result)
    assert github_device.get_flow("admin", identifier).token == "private-access-token"
    assert not hasattr(github_device.get_flow("admin", identifier), "refresh_token")


@pytest.mark.parametrize("error", ["access_denied", "expired_token"])
def test_denial_and_expiry_clear_pending_state(device, error):
    clock, calls, replies = device
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    clock[0] += 5
    replies.append({"error": error})
    assert asyncio.run(github_device.poll("admin", identifier))["status"] == "error"
    assert identifier not in github_device._flows


def test_expired_flow_cannot_be_polled(device):
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    device[0][0] += 901
    with pytest.raises(ValueError, match="expired"):
        asyncio.run(github_device.poll("admin", identifier))


def test_expired_token_is_not_used(monkeypatch):
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "private-access-token")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "1")
    assert github_device.access_token() == ""


@pytest.fixture
def api(device, monkeypatch, tmp_path):
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "github.db"))
    monkeypatch.setattr(db, "_CONN", None)
    connection = db.init_db()
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "Admin", "tenant", "admin")
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    yield TestClient(application)
    connection.close()


def test_device_endpoints_require_admin_and_same_origin(api):
    api.app.dependency_overrides.clear()
    assert api.post("/api/admin/setup/github/device/start", json={"confirm": True}).status_code == 401
    api.app.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", "", "admin")
    assert api.post("/api/admin/setup/github/device/start", json={"confirm": True}).status_code == 403


@pytest.mark.parametrize("status,expected", [(400, "HTTP 400"), (403, "HTTP 403"), (429, "rate-limited"), (503, "unavailable")])
def test_start_route_reports_safe_http_failure(api, monkeypatch, status, expected):
    async def start(*args):
        httpx.Response(status, text="private-response", request=httpx.Request("POST", "https://github.com/login/device/code")).raise_for_status()

    monkeypatch.setattr(github_device, "start", start)
    response = api.post("/api/admin/setup/github/device/start", headers={"Origin": "http://testserver"}, json={"confirm": True})
    assert response.status_code == 502
    assert expected in response.json()["detail"]
    assert "private-" not in response.text
    assert not github_device._flows


@pytest.mark.parametrize("error,expected", [(httpx.ConnectError, "DNS"), (httpx.ReadTimeout, "timed out")])
def test_start_route_distinguishes_transport_failures(api, monkeypatch, error, expected):
    async def start(*args):
        raise error("private-connection-details")

    monkeypatch.setattr(github_device, "start", start)
    response = api.post("/api/admin/setup/github/device/start", headers={"Origin": "http://testserver"}, json={"confirm": True})
    assert response.status_code == 502
    assert expected in response.json()["detail"]
    assert "private-" not in response.text


def test_start_route_explains_disabled_device_flow(api, device):
    device[2][:] = [{"error": "device_flow_disabled", "error_description": "private-response"}]
    response = api.post("/api/admin/setup/github/device/start", headers={"Origin": "http://testserver"}, json={"confirm": True})
    assert response.status_code == 400
    assert "enable Device Flow" in response.json()["detail"]
    assert "private-" not in response.text
    assert not github_device._flows


@pytest.mark.parametrize("login", ["organization", "customer_enterprise"])
def test_complete_device_flow_persists_without_returning_token(api, device, monkeypatch, login):
    stored = []
    async def request(path, *, token=None):
        assert path == "/user"
        assert token == "private-access-token"
        return {"login": login, "id": 42}
    async def store(token, expires_at, account, storage): stored.append((token, account, storage))
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", store)
    headers = {"Origin": "http://testserver"}
    started = api.post("/api/admin/setup/github/device/start", headers=headers, json={"confirm": True}).json()
    assert "private-device-code" not in json.dumps(started)
    device[0][0] += 5
    device[2].append({"access_token": "private-access-token", "token_type": "bearer", "scope": "repo,read:org"})
    response = api.post("/api/admin/setup/github/device/poll", headers=headers, json={"flowId": started["flowId"]})
    assert response.json() == {"status": "connected", "account": {"login": login, "id": 42}, "expiresAt": None}
    assert stored[0][0] == "private-access-token"
    assert stored[0][2]["secretStorage"] == "app_service"
    assert started["flowId"] not in github_device._flows
    assert "private-access-token" not in response.text


@pytest.mark.parametrize("login", ["organization", "customer_enterprise"])
def test_discovery_only_offers_writable_repos(monkeypatch, login):
    async def request(path, *args, **kwargs):
        if path == "/user": return {"id": 42, "login": login}
        if path == "/user/orgs": return [{"login": "organization-org"}]
        return [
            {"full_name": f"{login}/portal", "owner": {"login": login}, "name": "portal", "default_branch": "release/v1.0", "permissions": {"push": True}, "private": True},
            {"full_name": "other/read-only", "permissions": {"push": False}},
            {"full_name": "other/archived", "permissions": {"push": True}, "archived": True},
        ]
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.discover_repositories())
    assert [repo["fullName"] for repo in result["repositories"]] == [f"{login}/portal"]
    assert result["repositories"][0]["defaultBranch"] == "release/v1.0"
    assert setup_github.GitHubSettings(owner=login, repository="portal", branch="release/v1.0").branch == "release/v1.0"
    assert [owner["login"] for owner in result["owners"]] == [login, "organization-org"]


@pytest.mark.parametrize("login", ["../organization", "organization/repo", "organization?token=value", "organization space", "", "a" * 40])
def test_account_and_repository_owner_reject_unsafe_names(monkeypatch, login):
    async def request(*args, **kwargs):
        return {"id": 42, "login": login}

    monkeypatch.setattr(setup_github, "github_request", request)
    with pytest.raises(ValueError, match="Could not verify"):
        asyncio.run(setup_github.github_account())
    with pytest.raises(ValueError):
        setup_github.GitHubSettings(owner=login, repository="portal")


def test_managed_user_can_save_existing_repository(api, monkeypatch):
    async def request(path, *args, **kwargs):
        assert path == "/repos/customer_enterprise/portal"
        return {"full_name": "customer_enterprise/portal"}

    monkeypatch.setattr(setup_github, "github_request", request)
    response = api.put("/api/admin/setup/github", headers={"Origin": "http://testserver"}, json={
        "enabled": True, "owner": "customer_enterprise", "repository": "portal", "confirm": True,
    })
    assert response.status_code == 200
    assert response.json()["owner"] == "customer_enterprise"
    assert db.get_setup_setting("github")["owner"] == "customer_enterprise"


def test_account_verification_retry_reuses_authorized_token(api, device, monkeypatch):
    accounts = [{"id": 42, "login": "invalid/name"}, {"id": 42, "login": "customer_enterprise"}]
    stored = []

    async def request(path, *, token=None):
        assert path == "/user"
        assert token == "private-access-token"
        return accounts.pop(0)

    async def store(*args):
        stored.append(args)

    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", store)
    headers = {"Origin": "http://testserver"}
    started = api.post("/api/admin/setup/github/device/start", headers=headers, json={"confirm": True}).json()
    device[0][0] += 5
    device[2].append({"access_token": "private-access-token", "token_type": "bearer", "scope": "repo,read:org"})
    body = {"flowId": started["flowId"]}
    failed = api.post("/api/admin/setup/github/device/poll", headers=headers, json=body)
    assert failed.status_code == 400
    assert not stored
    connected = api.post("/api/admin/setup/github/device/poll", headers=headers, json=body)
    assert connected.json()["status"] == "connected"
    assert connected.json()["account"]["login"] == "customer_enterprise"
    assert len(stored) == 1
    assert len(device[1]) == 2
    assert "private-access-token" not in failed.text + connected.text


def test_device_token_storage_is_server_side(api, monkeypatch):
    import setup_auth
    writes = []
    monkeypatch.delenv("GALLERY_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", raising=False)
    async def arm(path, method="GET", body=None, **kwargs):
        if "/appsettings/list" in path: return {"properties": {"EXISTING": "keep"}}
        if method == "PUT": writes.append(body)
        return {}
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    asyncio.run(setup_auth.store_github_device_token("private-token", 2000, {"login": "organization", "id": 42}))
    assert writes[0]["properties"] == {"EXISTING": "keep", "GALLERY_GITHUB_TOKEN": "private-token", "GALLERY_GITHUB_TOKEN_EXPIRES_AT": "2000"}
    assert "private-token" not in json.dumps(db.get_setup_setting("github_connection"))
    assert github_device.access_token() == "private-token"


def test_storage_failure_can_retry_without_exchanging_code_again(api, device, monkeypatch):
    import httpx
    stored = []
    async def account(token=None): return {"login": "organization", "id": 42}
    async def store(*args):
        stored.append(args)
        if len(stored) == 1:
            httpx.Response(403, request=httpx.Request("PUT", "https://management.azure.com/")).raise_for_status()
    monkeypatch.setattr(setup_github, "github_account", account)
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", store)
    headers = {"Origin": "http://testserver"}
    started = api.post("/api/admin/setup/github/device/start", headers=headers, json={"confirm": True}).json()
    device[0][0] += 5
    device[2].append({"access_token": "private-access-token", "token_type": "bearer", "scope": "repo,read:org"})
    body = {"flowId": started["flowId"]}
    first = api.post("/api/admin/setup/github/device/poll", headers=headers, json=body)
    assert first.status_code == 502
    assert "private-access-token" not in first.text
    assert api.post("/api/admin/setup/github/device/poll", headers=headers, json=body).json()["status"] == "connected"
    assert len(device[1]) == 2


def test_reconnect_requires_disabling_active_repository(api):
    db.set_setup_setting("github", {"enabled": True})
    response = api.post("/api/admin/setup/github/device/start", headers={"Origin": "http://testserver"}, json={"confirm": True})
    assert response.status_code == 409


def test_insufficient_scope_does_not_connect(device):
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    device[0][0] += 5
    device[2].append({"access_token": "private-access-token", "token_type": "bearer", "scope": "read:user"})
    with pytest.raises(ValueError, match="permissions"):
        asyncio.run(github_device.poll("admin", identifier))
    assert identifier not in github_device._flows