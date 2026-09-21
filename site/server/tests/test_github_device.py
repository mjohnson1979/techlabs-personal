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
    replies.append({"access_token": "private-access-token", "token_type": "bearer", "scope": "repo,read:org", "expires_in": 28800,
                    "refresh_token": "private-refresh-token", "refresh_token_expires_in": 15897600})
    result = asyncio.run(github_device.poll("admin", identifier))
    assert result == {"status": "authorized"}
    assert "private-" not in json.dumps(result)
    assert github_device.get_flow("admin", identifier).token == "private-access-token"
    flow = github_device.get_flow("admin", identifier)
    assert flow.refresh_token == "private-refresh-token"
    assert flow.refresh_expires_at == clock[0] + 15897600
    assert "private-" not in repr(flow)


@pytest.mark.parametrize("refresh,duration", [(None, 1000), ("", 1000), ("private-refresh", 0), ("private-refresh", "invalid"), ("private-refresh", True)])
def test_invalid_refresh_credentials_never_become_connected(device, refresh, duration):
    clock, calls, replies = device
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    clock[0] += 5
    replies.append({"access_token": "private-access", "token_type": "bearer", "scope": "repo,read:org", "expires_in": 28800,
                    "refresh_token": refresh if refresh is not None else 123, "refresh_token_expires_in": duration})
    with pytest.raises(ValueError, match="invalid renewal credentials"):
        asyncio.run(github_device.poll("admin", identifier))
    assert identifier not in github_device._flows


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
def renewable(device, monkeypatch):
    credentials = {"version": 1, "accessToken": "private-old-access", "expiresAt": 900,
                   "refreshToken": "private-old-refresh", "refreshExpiresAt": 500000,
                   "clientId": "organization-client", "accountId": 42}
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", json.dumps(credentials))
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "900")
    monkeypatch.setattr(github_device, "_renewal_lock", asyncio.Lock())
    monkeypatch.setattr(github_device, "_pending_renewal", None)
    monkeypatch.setattr(github_device, "_renewal_failed_source", "")
    monkeypatch.setattr(github_device, "_renewal_retry_after", 0)
    db.set_setup_setting("github_connection", {"id": 42, "login": "organization", "method": "device"})
    async def account(token=None):
        assert token == "private-new-access"
        return {"id": 42, "login": "organization"}
    monkeypatch.setattr(setup_github, "github_account", account)
    device[2].clear()
    device[2].append({"access_token": "private-new-access", "refresh_token": "private-new-refresh", "token_type": "bearer",
                      "expires_in": 28800, "refresh_token_expires_in": 15897600})
    stored = []
    async def store(token, expires_at, account, storage, renewal):
        stored.append(token)
        credentials.update(accessToken=token, expiresAt=expires_at, refreshToken=renewal["token"], refreshExpiresAt=renewal["expiresAt"])
        monkeypatch.setenv("GALLERY_GITHUB_TOKEN", json.dumps(credentials))
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", store)
    return credentials, stored


def test_concurrent_expired_requests_refresh_once(device, renewable):
    async def requests():
        return await asyncio.gather(*(github_device.ensure_access_token() for _index in range(5)))
    assert asyncio.run(requests()) == ["private-new-access"] * 5
    assert len(device[1]) == 1
    assert device[1][0][1] == {"client_id": "organization-client", "grant_type": "refresh_token", "refresh_token": "private-old-refresh"}
    assert renewable[1] == ["private-new-access"]
    assert renewable[0]["refreshToken"] == "private-new-refresh"


def test_concurrent_renewal_waiters_do_not_exchange_twice(device, renewable, monkeypatch):
    oauth = github_device.oauth_request
    async def requests():
        entered, release = asyncio.Event(), asyncio.Event()
        async def exchange(path, payload):
            entered.set()
            await release.wait()
            return await oauth(path, payload)
        monkeypatch.setattr(github_device, "oauth_request", exchange)
        tasks = [asyncio.create_task(github_device.ensure_access_token()) for _index in range(5)]
        await entered.wait()
        release.set()
        return await asyncio.gather(*tasks)
    assert asyncio.run(requests()) == ["private-new-access"] * 5
    assert len(device[1]) == len(renewable[1]) == 1


@pytest.mark.parametrize("invalid", [{"error": "bad_refresh_token", "error_description": "private-details"},
    {"token_type": 123}, {"expires_in": True}, {"expires_in": -1}, {"refresh_token": ""}, {"refresh_token_expires_in": "bad"}])
def test_failed_exchange_is_redacted_and_throttled(device, renewable, invalid):
    device[2][0].update(invalid)
    for _index in range(2):
        with pytest.raises(github_device.GitHubRenewalError) as error:
            asyncio.run(github_device.ensure_access_token())
        assert "private-" not in str(error.value)
    assert len(device[1]) == 1 and not renewable[1]
    assert github_device.access_token() == ""


def test_refresh_near_expiry_renews_before_current_token_expires(device, renewable, monkeypatch):
    renewable[0]["expiresAt"] = device[0][0] + 300
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", json.dumps(renewable[0]))
    assert github_device.access_token() == "private-old-access"
    assert asyncio.run(github_device.ensure_access_token()) == "private-new-access"
    assert len(device[1]) == 1


def test_github_app_device_token_uses_permissions_not_oauth_scopes(device):
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    device[0][0] += 5
    device[2].append({"access_token": "ghu_private-token", "scope": "", "token_type": "bearer", "expires_in": 28800,
                      "refresh_token": "private-refresh", "refresh_token_expires_in": 15897600})
    assert asyncio.run(github_device.poll("admin", identifier)) == {"status": "authorized"}
    assert github_device.get_flow("admin", identifier).refresh_token == "private-refresh"


def test_storage_retry_does_not_reuse_consumed_refresh_token(device, renewable, monkeypatch):
    store = setup_routes.setup_auth.store_github_device_token
    attempts = []
    async def fail_once(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise httpx.ConnectError("private-storage-details")
        await store(*args)
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", fail_once)
    with pytest.raises(github_device.GitHubRenewalError) as error:
        asyncio.run(github_device.ensure_access_token())
    assert "private-" not in str(error.value)
    assert github_device.access_token() == ""
    with pytest.raises(github_device.GitHubRenewalError):
        asyncio.run(github_device.ensure_access_token())
    assert len(device[1]) == 1 and len(attempts) == 1
    device[0][0] += 61
    assert asyncio.run(github_device.ensure_access_token()) == "private-new-access"
    assert len(device[1]) == 1 and len(attempts) == 2


@pytest.mark.parametrize("case", ["expired_refresh", "different_client", "different_account", "healthy_access"])
def test_no_exchange_for_unusable_or_unneeded_renewal(device, renewable, monkeypatch, case):
    credentials, stored = renewable
    if case == "expired_refresh": credentials["refreshExpiresAt"] = 900
    if case == "different_client": credentials["clientId"] = "another-client"
    if case == "different_account": credentials["accountId"] = 99
    if case == "healthy_access": credentials["expiresAt"] = 5000
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", json.dumps(credentials))
    assert asyncio.run(github_device.ensure_access_token()) == ("private-old-access" if case == "healthy_access" else "")
    assert not device[1] and not stored


def test_refresh_rejects_changed_account_and_keeps_secrets_out_of_audit(device, renewable, monkeypatch):
    async def account(token=None): return {"id": 99, "login": "other"}
    monkeypatch.setattr(setup_github, "github_account", account)
    with pytest.raises(github_device.GitHubRenewalError):
        asyncio.run(github_device.ensure_access_token())
    assert not renewable[1]
    assert all("private-" not in row[0] for row in db.init_db().execute("SELECT value_json FROM setup_settings").fetchall())


def test_repository_request_renews_before_sending_authorization(device, renewable, monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.headers["authorization"])
        return httpx.Response(200, json={"verified": True})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(setup_github.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    assert asyncio.run(setup_github.github_request("/user/repos")) == {"verified": True}
    assert sent == ["Bearer private-new-access"]
    assert len(device[1]) == 1


def test_expired_but_renewable_upload_creates_review_branch_without_signin(device, renewable, monkeypatch):
    import base64
    import github_intake
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    async def verify(account, token):
        assert token == "private-new-access" and account["id"] == 42
        return True
    monkeypatch.setattr(setup_github, "verify_reconnection", verify)
    async def validate(files, repo, base_tree, token):
        assert token == "private-new-access"
    monkeypatch.setattr(github_intake, "validate_submission_sources", validate)
    writes = []
    def handle(request):
        assert request.headers["authorization"] == "Bearer private-new-access"
        if request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if request.method == "GET":
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        payload = json.loads(request.content)
        writes.append(request.url.path)
        if request.url.path.endswith("/git/blobs"):
            assert base64.b64decode(payload["content"]) == b"terraform {}"
        if request.url.path.endswith("/git/refs"):
            assert payload["ref"] == "refs/heads/usecase-intake/example-abcdef"
        return httpx.Response(201, json={"sha": "c" * 40})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(github_intake.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    assert not github_device.access_token() and github_intake.configured()
    result = asyncio.run(github_intake.push_submission(branch="usecase-intake/example-abcdef",
        files={"catalog/example/terraform/main.tf": b"terraform {}"}, message="Review"))
    assert result["commit"] == "c" * 40 and len(writes) == 4
    assert len(device[1]) == 1


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
    assert response.json() == {"status": "connected", "account": {"login": login, "id": 42}, "expiresAt": None,
                               "autoRenew": False, "refreshExpiresAt": None}
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


def test_refresh_pair_is_persisted_together_and_not_in_sqlite_or_browser(api, device, monkeypatch):
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "0")
    writes = []
    async def arm(path, method="GET", body=None, **kwargs):
        if "/appsettings/list" in path:
            return {"properties": {"EXISTING": "keep"}}
        if method == "PUT":
            writes.append(body["properties"])
        return {}
    async def account(token=None):
        assert token == "private-access"
        return {"login": "organization", "id": 42}
    monkeypatch.setattr(setup_routes.setup_auth.setup_cloud, "arm_request", arm)
    monkeypatch.setattr(setup_github, "github_account", account)
    headers = {"Origin": "http://testserver"}
    started = api.post("/api/admin/setup/github/device/start", headers=headers, json={"confirm": True}).json()
    device[0][0] += 5
    device[2].append({"access_token": "private-access", "token_type": "bearer", "scope": "repo,read:org", "expires_in": 28800,
                      "refresh_token": "private-refresh", "refresh_token_expires_in": 15897600})
    response = api.post("/api/admin/setup/github/device/poll", headers=headers, json={"flowId": started["flowId"]})
    assert response.status_code == 200
    assert len(writes) == 1 and writes[0]["EXISTING"] == "keep"
    credentials = json.loads(writes[0]["GALLERY_GITHUB_TOKEN"])
    assert credentials["accessToken"] == "private-access" and credentials["refreshToken"] == "private-refresh"
    assert credentials["clientId"] == "organization-client" and credentials["accountId"] == 42
    assert github_device.access_token() == "private-access"
    assert db.get_setup_setting("github_connection")["autoRenew"] is True
    assert "private-" not in response.text
    assert all("private-" not in row[0] for row in db.init_db().execute("SELECT value_json FROM setup_settings").fetchall())


def test_key_vault_refresh_pair_storage_can_recover_previous_version_reference(api, monkeypatch):
    import setup_auth
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "0")
    uri = "https://portal-vault.vault.azure.net/secrets/github-token"
    writes, secret_values = [], []
    async def target(*args, **kwargs): return uri
    async def secret(destination, *, value=None, tags=None):
        assert destination == uri
        if value is not None:
            secret_values.append(json.loads(value))
        return {"id": uri + "/newversion", "tags": {"portalPurpose": "github-device-token"}}
    async def arm(path, method="GET", body=None, **kwargs):
        if "/appsettings/list" in path:
            return {"properties": {"EXISTING": "keep", "GALLERY_GITHUB_TOKEN": f"@Microsoft.KeyVault(SecretUri={uri}/oldversion)"}}
        if method == "PUT": writes.append(body["properties"])
        return {}
    monkeypatch.setattr(setup_auth, "key_vault_target", target)
    monkeypatch.setattr(setup_auth, "key_vault_secret", secret)
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    storage = {"secretStorage": "key_vault", "keyVaultResourceId": "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/portal/providers/Microsoft.KeyVault/vaults/portal-vault", "keyVaultSecretName": "github-token"}
    asyncio.run(setup_auth.store_github_device_token("private-access", 2000, {"login": "organization", "id": 42}, storage,
        {"token": "private-refresh", "expiresAt": 500000, "clientId": "organization-client"}))
    assert len(secret_values) == 1
    assert secret_values[0]["accessToken"] == "private-access" and secret_values[0]["refreshToken"] == "private-refresh"
    assert "private-" not in json.dumps(writes)
    assert writes[0]["EXISTING"] == "keep"
    assert writes[0]["GALLERY_GITHUB_TOKEN"] == f"@Microsoft.KeyVault(SecretUri={uri}/newversion)"
    assert github_device.access_token() == "private-access"


@pytest.mark.parametrize("case", ["success", "other_account", "unknown_previous", "no_permission", "invalid_permissions", "wrong_repository", "branch_denied", "wrong_branch", "invalid_ref", "app_changed", "storage_failed"])
def test_reconnect_preserves_active_repository_and_verifies_before_storage(api, device, monkeypatch, case):
    configuration = {"enabled": True, "owner": "organization", "repository": "portal", "branch": "release/v1.0", "seeded": True}
    db.set_setup_setting("github", configuration)
    db.set_setup_setting("github_connection", {} if case == "unknown_previous" else {"id": 42, "login": "previous"})
    previous_connection = db.get_setup_setting("github_connection")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "expired-private-token")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "1")
    monkeypatch.setattr(setup_github, "_SYNC_WAKE", asyncio.Event())
    requests = []
    writes = []
    async def request(path, *, token=None):
        assert token == "new-private-token"
        requests.append(path)
        if path == "/user":
            return {"id": 99 if case == "other_account" else 42, "login": "renamed-account"}
        if path == "/repos/organization/portal":
            return {"full_name": "another/portal" if case == "wrong_repository" else "organization/portal", "permissions": None if case == "invalid_permissions" else {"push": case != "no_permission"}}
        assert path == "/repos/organization/portal/git/ref/heads/release/v1.0"
        if case == "branch_denied":
            httpx.Response(404, text="private upstream response", request=httpx.Request("GET", "https://api.github.com/private")).raise_for_status()
        return {"ref": "refs/heads/other" if case == "wrong_branch" else "refs/heads/release/v1.0", "object": None if case == "invalid_ref" else {"sha": "a" * 40}}
    async def store(token, expires_at, account, storage):
        if case == "storage_failed":
            raise httpx.ConnectError("private storage response")
        writes.append((token, account))
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(setup_routes.setup_auth, "store_github_device_token", store)
    headers = {"Origin": "http://testserver"}
    started = api.post("/api/admin/setup/github/device/start", headers=headers, json={"confirm": True})
    assert started.status_code == 200
    assert db.get_setup_setting("github") == configuration
    assert not writes
    flow_id = started.json()["flowId"]
    device[0][0] += 5
    device[2].append({"access_token": "new-private-token", "token_type": "bearer", "scope": "repo,read:org", "expires_in": 28800})
    if case == "app_changed":
        monkeypatch.setattr(github_device, "client_id", lambda: "changed-application")
    response = api.post("/api/admin/setup/github/device/poll", headers=headers, json={"flowId": flow_id})
    assert "private" not in response.text
    assert db.get_setup_setting("github") == configuration
    if case == "success":
        assert response.status_code == 200 and response.json()["status"] == "connected"
        assert writes == [("new-private-token", {"id": 42, "login": "renamed-account"})]
        assert len(requests) == 3
        assert db.get_setup_setting("catalog_job")["status"] == "queued"
        assert setup_github._SYNC_WAKE.is_set()
        assert flow_id not in github_device._flows
    else:
        assert response.status_code == (502 if case == "storage_failed" else 400)
        assert not writes and db.get_setup_setting("github_connection") == previous_connection
        assert db.get_setup_setting("catalog_job") is None
        assert flow_id in github_device._flows


def test_insufficient_scope_does_not_connect(device):
    identifier = asyncio.run(github_device.start("admin"))["flowId"]
    device[0][0] += 5
    device[2].append({"access_token": "private-access-token", "token_type": "bearer", "scope": "read:user"})
    with pytest.raises(ValueError, match="permissions"):
        asyncio.run(github_device.poll("admin", identifier))
    assert identifier not in github_device._flows