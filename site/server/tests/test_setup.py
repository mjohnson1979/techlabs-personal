"""Hosted setup validation and remote integration tests."""

from __future__ import annotations

import sys
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import db
import setup_config
import setup_routes
from identity import CurrentUser, require_admin

TENANT = "11111111-1111-4111-8111-111111111111"
SUBSCRIPTION = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "setup.db"))
    monkeypatch.setattr(db, "_CONN", None)
    connection = db.init_db()
    yield
    connection.close()


def test_branding_defaults_and_admin_updates_persist(configured, monkeypatch):
    import distribution
    monkeypatch.setenv("GALLERY_BRAND_NAME", "Engineering Hub")
    monkeypatch.setenv("GALLERY_FOOTER_TEXT", "Internal use. Verify estimates.")
    assert distribution.branding() == {"brandName": "Engineering Hub", "footerText": "Internal use. Verify estimates."}
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    client = TestClient(application)
    payload = {"brandName": "  Platform Hub  ", "footerText": "<b>Plain text only</b>"}
    assert client.put("/api/admin/setup/branding", json=payload).status_code in (401, 403)
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    assert client.put("/api/admin/setup/branding", json=payload).status_code == 403
    response = client.put("/api/admin/setup/branding", headers={"Origin": "http://testserver"}, json=payload)
    assert response.status_code == 200
    assert distribution.brand_name() == "Platform Hub"
    assert response.json() == db.get_setup_setting("branding")
    assert client.get("/api/admin/setup").json()["branding"] == response.json()
    assert client.put("/api/admin/setup/branding", headers={"Origin": "http://testserver"}, json={"brandName": "Hub", "footerText": ""}).json()["footerText"] == ""


@pytest.mark.parametrize("fields", [{"brandName": " "}, {"brandName": "a" * 81}, {"footerText": "a" * 501}, {"footerText": "bad\ntext"}, {"logoUrl": "https://untrusted.example"}])
def test_branding_rejects_invalid_fields(fields):
    with pytest.raises(ValueError):
        setup_routes.BrandingInput(**({"brandName": "Hub", "footerText": "Notice"} | fields))


def test_branding_guest_cannot_save(configured, monkeypatch):
    import identity
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: CurrentUser("guest", "", "", TENANT, "guest"))
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    response = TestClient(application).put("/api/admin/setup/branding", headers={"Origin": "http://testserver"}, json={"brandName": "Hub", "footerText": ""})
    assert response.status_code == 403
    assert db.get_setup_setting("branding") is None


def test_subscription_csv_and_persistence(configured):
    content = f'subscriptionId,pricingMode\n{SUBSCRIPTION},negotiated\n'.encode()
    items = setup_config.parse_subscriptions_csv(content)
    assert items[0].model_dump() == {"subscriptionId": SUBSCRIPTION, "pricingMode": "negotiated"}
    assert setup_config.parse_subscriptions_csv(f"\ufeffsubscriptionId\n{SUBSCRIPTION}\n".encode())[0].pricingMode == "retail"


def test_github_oauth_application_can_be_configured_in_browser(configured, monkeypatch):
    import github_device
    monkeypatch.delenv("GALLERY_GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GALLERY_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github_device, "_flows", {})
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser(oid="admin", upn="admin@example.com", name="Admin", tenant_id=TENANT, role="admin")
    client = TestClient(application)
    path = "/api/admin/setup/github/oauth-client"
    body = {"clientId": "organization-oauth-client", "confirm": True}
    assert client.put(path, json=body).status_code == 403
    headers = {"Origin": "http://testserver"}
    assert client.put(path, json={**body, "confirm": False}, headers=headers).status_code == 400
    response = client.put(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert github_device.client_id() == "organization-oauth-client"
    db.set_setup_setting("github", {"enabled": True})
    assert client.put(path, json={**body, "clientId": "another-client"}, headers=headers).status_code == 409
    assert github_device.client_id() == "organization-oauth-client"


@pytest.mark.parametrize("rows", [
    "not-a-guid",
    "00000000-0000-0000-0000-000000000000",
    "{subscription}\n{subscription}",
    "{subscription},extra",
])
def test_invalid_csv_is_rejected(configured, rows):
    content = "subscriptionId\n" + rows.format(subscription=SUBSCRIPTION)
    with pytest.raises(ValueError):
        setup_config.parse_subscriptions_csv(content.encode())
    assert db.get_setup_setting("subscriptions") is None


def test_allowlist_rejects_unknown_subscription(configured):
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION}])
    with pytest.raises(ValueError, match="configured"):
        setup_config.subscription_for("33333333-3333-4333-8333-333333333333")


def test_lookup_resolves_azure_metadata(configured, monkeypatch):
    async def arm(path):
        assert path == f"/subscriptions/{SUBSCRIPTION}?api-version=2022-12-01"
        return {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Azure-resolved name", "state": "Enabled"}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    record = asyncio.run(setup_routes.setup_cloud.verify_subscription({"subscriptionId": SUBSCRIPTION}))
    assert record == {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Azure-resolved name", "pricingMode": "retail"}


@pytest.mark.parametrize("change", [
    {"tenantId": "33333333-3333-4333-8333-333333333333"},
    {"subscriptionId": "33333333-3333-4333-8333-333333333333"},
    {"state": "Disabled"}, {"displayName": ""},
])
def test_subscription_lookup_rejects_unverified_metadata(configured, monkeypatch, change):
    async def arm(path):
        return {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Team", "state": "Enabled", **change}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    with pytest.raises(ValueError):
        asyncio.run(setup_routes.setup_cloud.verify_subscription({"subscriptionId": SUBSCRIPTION}))


def test_subscription_api_requires_admin_and_origin(configured):
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    client = TestClient(application)
    assert client.get("/api/admin/setup").status_code == 401
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    assert client.post("/api/admin/setup/subscriptions/parse", json={"csv": "x"}).status_code == 403


def test_subscription_save_verifies_before_persisting(configured, monkeypatch):
    monkeypatch.delenv("GALLERY_SUBSCRIPTION_ID", raising=False)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    async def reject(item):
        raise ValueError("Subscription tenant could not be verified")
    monkeypatch.setattr(setup_routes.setup_cloud, "verify_subscription", reject)
    client = TestClient(application)
    response = client.put("/api/admin/setup/subscriptions", headers={"Origin": "http://testserver"}, json={"subscriptions": [{"subscriptionId": SUBSCRIPTION}]})
    assert response.status_code == 400
    assert db.get_setup_setting("subscriptions") is None


@pytest.mark.parametrize("route,payload,key", [
    ("/resolve", {"subscriptionId": SUBSCRIPTION}, "subscription"),
    ("/resolve", {"subscriptionId": SUBSCRIPTION, "pricingMode": "retail"}, "subscription"),
    ("/parse", {"csv": f"subscriptionId\n{SUBSCRIPTION}\n"}, "subscriptions"),
])
def test_id_only_requests_resolve_metadata(configured, monkeypatch, route, payload, key):
    async def arm(path):
        return {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Azure-resolved name", "state": "Enabled"}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    response = TestClient(application).post("/api/admin/setup/subscriptions" + route, headers={"Origin": "http://testserver"}, json=payload)
    assert response.status_code == 200
    record = response.json()[key]
    if isinstance(record, list): record = record[0]
    assert record == {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Azure-resolved name", "pricingMode": "retail"}
    assert db.get_setup_setting("subscriptions") is None


@pytest.mark.parametrize("method,path,payload,field", [
    ("POST", "/resolve", {"subscriptionId": "00000000-0000-0000-0000-000000000000", "pricingMode": "retail"}, "subscriptionId"),
    ("POST", "/resolve", {"subscriptionId": SUBSCRIPTION, "pricingMode": ""}, "pricingMode"),
    ("POST", "/resolve", {"subscriptionId": SUBSCRIPTION, "displayName": "Supplied name"}, "displayName"),
    ("PUT", "", {"subscriptions": []}, "subscriptions"),
    ("PUT", "", {"subscriptions": [{"subscriptionId": SUBSCRIPTION, "pricingMode": "invalid"}]}, "pricingMode"),
])
def test_subscription_validation_identifies_field_before_cloud_calls(configured, monkeypatch, method, path, payload, field):
    async def unexpected_cloud(*args, **kwargs):
        pytest.fail("Invalid input must not call Azure")

    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", unexpected_cloud)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    response = TestClient(application).request(method, "/api/admin/setup/subscriptions" + path, headers={"Origin": "http://testserver"}, json=payload)
    assert response.status_code == 422
    assert any(error["loc"][-1] == field for error in response.json()["detail"])
    assert db.get_setup_setting("subscriptions") is None


def test_subscription_resolution_explains_denied_host_access(configured, monkeypatch):
    import httpx

    async def forbidden(path):
        httpx.Response(403, text="private-azure-response", request=httpx.Request("GET", "https://management.azure.com" + path)).raise_for_status()

    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", forbidden)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    response = TestClient(application).post("/api/admin/setup/subscriptions/resolve", headers={"Origin": "http://testserver"}, json={"subscriptionId": SUBSCRIPTION, "pricingMode": "retail"})
    assert response.status_code == 502
    assert "managed identity's Reader access" in response.json()["detail"]
    assert "private-azure-response" not in response.text
    assert db.get_setup_setting("subscriptions") is None


def test_save_uses_fresh_azure_metadata(configured, monkeypatch):
    monkeypatch.delenv("GALLERY_SUBSCRIPTION_ID", raising=False)
    async def arm(path):
        return {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Renamed in Azure", "state": "Enabled"}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    client = TestClient(application)
    headers = {"Origin": "http://testserver"}
    spoofed = client.put("/api/admin/setup/subscriptions", headers=headers, json={"subscriptions": [{"subscriptionId": SUBSCRIPTION, "displayName": "Spoofed"}]})
    assert spoofed.status_code == 422
    response = client.put("/api/admin/setup/subscriptions", headers=headers, json={"subscriptions": [{"subscriptionId": SUBSCRIPTION, "pricingMode": "negotiated"}]})
    assert response.status_code == 200
    assert db.get_setup_setting("subscriptions")[0] == {"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Renamed in Azure", "pricingMode": "negotiated"}


def test_single_action_add_and_pricing_save_preserve_existing_subscriptions(configured, monkeypatch):
    monkeypatch.delenv("GALLERY_SUBSCRIPTION_ID", raising=False)
    second = "33333333-3333-4333-8333-333333333333"
    paths = []

    async def arm(path):
        paths.append(path)
        identifier = path.split("/")[2].split("?")[0]
        return {"subscriptionId": identifier, "tenantId": TENANT, "displayName": "Azure name", "state": "Enabled"}

    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    client = TestClient(application)
    selections = [{"subscriptionId": SUBSCRIPTION, "pricingMode": "retail"}]
    for action in ("add", "append", "pricing"):
        if action == "append":
            selections.append({"subscriptionId": second, "pricingMode": "retail"})
        elif action == "pricing":
            selections[0]["pricingMode"] = "negotiated"
        paths.clear()
        response = client.put("/api/admin/setup/subscriptions", headers={"Origin": "http://testserver"}, json={"subscriptions": selections})
        assert response.status_code == 200
        assert len(paths) == len(selections)
        saved = db.get_setup_setting("subscriptions")
        assert response.json()["subscriptions"] == saved
        assert [{key: item[key] for key in ("subscriptionId", "pricingMode")} for item in saved] == selections
        assert all(item["displayName"] == "Azure name" and item["tenantId"] == TENANT for item in saved)


def test_batch_failure_keeps_saved_subscriptions(configured, monkeypatch):
    import httpx
    previous = [{"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Existing", "pricingMode": "retail"}]
    db.set_setup_setting("subscriptions", previous)
    async def arm(path):
        if SUBSCRIPTION not in path:
            httpx.Response(403, request=httpx.Request("GET", "https://management.azure.com/")).raise_for_status()
        return {**previous[0], "state": "Enabled"}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    response = TestClient(application).put("/api/admin/setup/subscriptions", headers={"Origin": "http://testserver"}, json={"subscriptions": [{"subscriptionId": SUBSCRIPTION}, {"subscriptionId": "33333333-3333-4333-8333-333333333333"}]})
    assert response.status_code == 502
    assert db.get_setup_setting("subscriptions") == previous


def test_locations_are_loaded_separately_and_quota_requires_region(configured, monkeypatch):
    from identity import require_user
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Team", "pricingMode": "retail"}])
    paths = []
    async def arm(path):
        paths.append(path)
        return {"value": [
            {"name": "westus", "displayName": "West US", "metadata": {"regionType": "Physical"}},
            {"name": "eastus", "displayName": "East US"},
            {"name": "global", "displayName": "Global", "metadata": {"regionType": "Logical"}},
        ]}
    monkeypatch.setattr(setup_routes.setup_cloud, "arm_request", arm)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "", TENANT)
    client = TestClient(application)
    response = client.get(f"/api/subscriptions/{SUBSCRIPTION}/locations")
    assert response.json() == {"locations": [{"name": "eastus", "displayName": "East US"}, {"name": "westus", "displayName": "West US"}]}
    assert paths == [f"/subscriptions/{SUBSCRIPTION}/locations?api-version=2022-12-01"]
    assert client.get(f"/api/subscriptions/{SUBSCRIPTION}/capacity").status_code == 422
    assert client.get("/api/subscriptions/33333333-3333-4333-8333-333333333333/locations").status_code == 403


@pytest.mark.parametrize("valid", [True, False])
def test_negotiated_prices_and_explicit_fallback(monkeypatch, valid):
    import pricing
    import setup_cloud

    async def sheet(identifier):
        assert identifier == SUBSCRIPTION
        return [{"meterId": "meter-1" if valid else "other", "currencyCode": "USD", "unitOfMeasure": "1 Hour", "unitPrice": 2, "includedQuantity": 0}]
    monkeypatch.setattr(setup_cloud, "price_sheet", sheet)
    cost = {"currency": "USD", "region": "eastus", "components": [{"name": "Compute", "meterId": "meter-1", "unitOfMeasure": "1 Hour", "unitPrice": 4, "quantityPerMonth": 10, "billing": "fixed"}]}
    result = asyncio.run(pricing.estimate_for_subscription(cost, "eastus", {}, {"subscriptionId": SUBSCRIPTION, "pricingMode": "negotiated"}))
    assert result["totalMonthly"] == (20 if valid else 40)
    assert result["pricingSource"] == ("negotiated" if valid else "retail")
    assert bool(result["fallbackReason"]) is not valid


def test_negotiated_failure_preserves_retail(monkeypatch):
    import httpx
    import pricing
    import setup_cloud

    async def denied(identifier):
        response = httpx.Response(403, request=httpx.Request("GET", "https://management.azure.com/"))
        response.raise_for_status()
    monkeypatch.setattr(setup_cloud, "price_sheet", denied)
    cost = {"currency": "USD", "region": "eastus", "components": [{"unitPrice": 4, "quantityPerMonth": 10, "billing": "fixed"}]}
    result = asyncio.run(pricing.estimate_for_subscription(cost, "eastus", {}, {"subscriptionId": SUBSCRIPTION, "pricingMode": "negotiated"}))
    assert result["totalMonthly"] == 40
    assert "403" in result["fallbackReason"]


def test_github_source_copy_requires_confirmation(configured):
    import setup_github
    with pytest.raises(ValueError, match="confirmation"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal")))
    with pytest.raises(ValueError, match="confirmation"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", seed=True)))


def test_github_sync_publishes_metadata_without_executing_source(configured, monkeypatch):
    import base64
    import json
    import setup_github
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    async def request(path, *args):
        if "/git/ref/" in path:
            return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        if path.endswith("/root"):
            return {"tree": [{"path": "catalog", "sha": "catalog", "type": "tree"}]}
        if path.endswith("/catalog"):
            return {"tree": [{"path": "example", "sha": "example", "type": "tree"}]}
        if path.endswith("/example"):
            return {"tree": [{"path": "metadata.json", "sha": "meta", "type": "blob", "mode": "100644", "size": 40}]}
        return {"content": base64.b64encode(json.dumps({"displayName": "Organization example"}).encode()).decode()}
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.sync_catalog())
    assert result["items"][0]["displayName"] == "Organization example"
    assert result["items"][0]["deployReady"] is False
    assert db.get_setup_setting("catalog_snapshot")["sourceCommit"] == "a" * 40
    assert len(result["version"]) == 40


def test_generated_prices_require_matching_metadata(configured):
    import hashlib
    import setup_github
    metadata = b'{"displayName":"Example"}'
    authored = {"region": "eastus", "currency": "USD", "components": [{"billing": "fixed", "quantityPerMonth": 10}]}
    document = {"metadataSha256": hashlib.sha256(metadata).hexdigest(), "cost": {"pricingSource": "retail", "complete": True, "region": "eastus", "currency": "USD", "pricedAt": "2026-01-01T00:00:00+00:00", "components": [{"unitPrice": 2}]}}
    assert setup_github.generated_cost(document, metadata, authored)["totalMonthly"] == 20
    assert setup_github.generated_cost(document, metadata + b" ", authored) is None
    document["cost"]["components"][0]["unitPrice"] = float("nan")
    with pytest.raises(ValueError):
        setup_github.generated_cost(document, metadata, authored)


def test_capacity_snapshot_is_validated_and_filtered(configured):
    import setup_github
    other = "33333333-3333-4333-8333-333333333333"
    rows = [{"name": "vCPUs", "limit": 10, "current": 2, "available": 8}]
    document = {"schemaVersion": 1, "tenantId": TENANT, "generated": "2026-01-01T00:00:00+00:00", "physicalCapacity": "unknown", "subscriptions": {SUBSCRIPTION: {"eastus": rows}, other: {"eastus": rows}}}
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Organization", "pricingMode": "retail"}])
    db.set_setup_setting("catalog_snapshot", {"capacity": setup_github.validate_capacity(document)})
    assert set(setup_github.capacity_snapshot()["subscriptions"]) == {SUBSCRIPTION}
    for mutation in ({"tenantId": other}, {"generated": None}, {"physicalCapacity": "available"}):
        with pytest.raises(ValueError):
            setup_github.validate_capacity({**document, **mutation})
    rows[0]["available"] = 99
    with pytest.raises(ValueError):
        setup_github.validate_capacity(document)


def test_scheduled_snapshot_endpoint_requires_user_and_filters_removed_subscriptions(configured, monkeypatch):
    import api
    import catalog_processing
    from identity import require_user
    item = {"path": "example"}
    subscription = {"subscriptionId": SUBSCRIPTION}
    db.set_setup_setting("subscriptions", [subscription])
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": item})
    key = catalog_processing.regional_input_key(item, subscription)
    row = {"subscriptionId": SUBSCRIPTION, "useCase": "example", "location": "eastus", "inputKey": key, "checkedAt": 1.0}
    db.set_setup_setting("regional_snapshot", {"rows": [row, {**row, "subscriptionId": "33333333-3333-4333-8333-333333333333"}]})
    application = FastAPI()
    application.include_router(api.build_api_router())
    client = TestClient(application)
    assert client.get("/api/regions/snapshot").status_code == 401
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "", TENANT)
    response = client.get("/api/regions/snapshot?useCase=example")
    assert len(response.json()["rows"]) == 1
    assert response.json()["rows"][0]["stale"] is True
    assert client.get("/api/regions/snapshot?subscriptionId=33333333-3333-4333-8333-333333333333").status_code == 403


def test_capacity_snapshot_accepts_all_region_scope(configured):
    import setup_github
    regions = {f"region{index}": [] for index in range(80)}
    subscriptions = {f"{index:08d}-1111-4111-8111-111111111111": regions for index in range(25)}
    result = setup_github.validate_capacity({"schemaVersion": 1, "tenantId": TENANT,
        "generated": "2026-01-01T00:00:00+00:00", "physicalCapacity": "unknown", "subscriptions": subscriptions})
    assert len(result["subscriptions"]) == 25
    assert len(next(iter(result["subscriptions"].values()))) == 80


@pytest.mark.parametrize("price_failure", [False, True])
def test_regional_comparison_prices_saved_subscription_and_keeps_quota_results(configured, monkeypatch, price_failure):
    import api
    import setup_cloud
    from identity import require_user
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION, "tenantId": TENANT, "displayName": "Team", "pricingMode": "negotiated"}])
    item = {"path": "example", "cost": {"region": "eastus", "components": [{"unitPrice": 99, "monthly": 99}]}}
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": item})
    async def locations(identifier):
        assert identifier == SUBSCRIPTION
        return [{"name": "eastus"}, {"name": "westus"}]
    async def estimate(cost, region, parameters, subscription):
        assert subscription["pricingMode"] == "negotiated"
        assert region == "westus"
        assert "unitPrice" not in cost["components"][0]
        if price_failure:
            raise ValueError("private upstream message")
        return {"complete": True, "totalMonthly": 20, "pricingSource": "retail", "fallbackReason": "Negotiated meters unavailable", "currency": "USD"}
    async def capacity(identifier, region, workload, parameters):
        return {"status": "unknown", "quotas": [{"name": "cores", "available": 3}], "checks": [], "physicalCapacity": "unknown"}
    monkeypatch.setattr(setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(api.pricing, "estimate_for_subscription", estimate)
    monkeypatch.setattr(setup_cloud, "workload_capacity", capacity)
    application = FastAPI()
    application.include_router(api.build_api_router())
    client = TestClient(application)
    payload = {"useCase": "example", "subscriptionId": SUBSCRIPTION, "location": "westus"}
    assert client.post("/api/regions/check", json=payload).status_code == 401
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "user@example.test", "User", TENANT)
    headers = {"Origin": "http://testserver"}
    assert client.post("/api/regions/check", json=payload, headers={"Origin": "https://other.test"}).status_code == 403
    assert client.post("/api/regions/check", json={**payload, "subscriptionId": "33333333-3333-4333-8333-333333333333"}, headers=headers).status_code == 403
    assert client.post("/api/regions/check", json={**payload, "location": "otherregion"}, headers=headers).status_code == 400
    response = client.post("/api/regions/check", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["capacity"]["quotas"][0]["available"] == 3
    assert (response.json()["price"] is None) == price_failure
    assert "private upstream message" not in response.text
    assert item["cost"]["components"][0]["unitPrice"] == 99


@pytest.mark.parametrize("lifecycle", ["GenerallyAvailable", "Deprecating", ""])
def test_model_capacity_matches_exact_sku_and_lifecycle(monkeypatch, lifecycle):
    import setup_cloud
    async def read(path):
        if "/usages?" in path:
            return {"value": [{"name": {"value": "cores"}, "limit": 10, "currentValue": 2}]}
        if "/modelCapacities?" in path:
            assert "modelVersion=2026" in path
            return {"value": [{"location": "eastus", "properties": {"skuName": "Standard", "availableCapacity": 0}},
                              {"location": "eastus", "properties": {"skuName": "GlobalStandard", "availableCapacity": 100}}]}
        return {"value": [{"model": {"name": "example-model", "version": "2026", "lifecycleStatus": lifecycle, "skus": [{"name": "Standard"}, {"name": "GlobalStandard"}]}}]}
    monkeypatch.setattr(setup_cloud, "arm_request", read)
    result = asyncio.run(setup_cloud.workload_capacity(SUBSCRIPTION, "eastus", {"quota": {"requirements": [
        {"service": "Model", "kind": "model-tpm", "model": "example-model", "modelVersion": "2026", "sku": "Standard"}]}}, {}))
    assert result["checks"][0]["status"] == ("unknown" if not lifecycle else "unavailable")
    assert result["physicalCapacity"] == "unknown"


def test_capacity_cache_is_subscription_scoped_and_does_not_share_mutable_results(monkeypatch):
    import setup_cloud
    calls = []
    async def read(path):
        calls.append(path)
        return {"value": []}
    monkeypatch.setattr(setup_cloud, "arm_request", read)
    async def run():
        first = await setup_cloud.cached_read(f"/subscriptions/{SUBSCRIPTION}/locations?api-version=2022-12-01")
        first["value"].append("not cached")
        second = await setup_cloud.cached_read(f"/subscriptions/{SUBSCRIPTION}/locations?api-version=2022-12-01")
        assert second == {"value": []}
        await setup_cloud.cached_read("/subscriptions/33333333-3333-4333-8333-333333333333/locations?api-version=2022-12-01")
        assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize("requirements", [[None], "invalid", [{"kind": "model-tpm", "model": "example", "modelVersion": 1}]])
def test_malformed_capacity_metadata_is_unknown(monkeypatch, requirements):
    import setup_cloud
    async def read(path):
        assert "/usages?" in path
        return {"value": []}
    monkeypatch.setattr(setup_cloud, "arm_request", read)
    result = asyncio.run(setup_cloud.workload_capacity(SUBSCRIPTION, "eastus", {"quota": {"requirements": requirements}}, {}))
    assert result["status"] == "unknown"


def test_service_offering_does_not_claim_available_capacity(monkeypatch):
    import setup_cloud
    async def read(path):
        if "/usages?" in path:
            return {"value": []}
        return {"resourceTypes": [{"resourceType": "serverfarms", "locations": ["East US"]}]}
    monkeypatch.setattr(setup_cloud, "arm_request", read)
    workload = {"quota": {"requirements": [{"service": "App Service", "provider": "Microsoft.Web/serverfarms"}]}}
    async def run():
        offered = await setup_cloud.workload_capacity(SUBSCRIPTION, "eastus", workload, {})
        missing = await setup_cloud.workload_capacity(SUBSCRIPTION, "westus", workload, {})
        assert offered["status"] == "offered"
        assert "not verified" in offered["checks"][0]["detail"]
        assert missing["status"] == "unavailable"
    asyncio.run(run())


def test_workflow_pr_only_changes_review_branch_and_is_idempotent(configured, monkeypatch):
    import base64
    import setup_github
    names = (".github/workflows/portal-catalog.yml", ".github/workflows/portal-capacity.yml", ".github/workflows/portal-lifecycle.yml", "tools/portal_workflows.py", "tools/portal_catalog.py", "site/server/pricing.py", "site/server/catalog_workflows.py",
             "site/server/catalog_processing.py", "site/server/setup_cloud.py", "site/server/lifecycle.py", "site/requirements.txt")
    monkeypatch.setattr(setup_github, "source_seed", lambda: {name: base64.b64encode(b"reviewed").decode() for name in names})
    db.set_setup_setting("catalog_processing", {"mode": "github-hosted"})
    async def probe(*args, **kwargs):
        return {"github-hosted": {"available": True}}
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "release"})
    calls = []
    opened = []
    async def request(path, method="GET", body=None, **kwargs):
        calls.append((path, method, body))
        if path.endswith("/pulls"):
            if method == "POST":
                opened.append({"number": 7})
                assert body["base"] == "release"
                return opened[0]
            return opened
        if "/git/ref/heads/" in path:
            return {"object": {"sha": "parent"}}
        if path.endswith("/git/commits/parent"):
            return {"tree": {"sha": "original-tree"}}
        if "/contents/" in path:
            return {}
        return {"sha": "generated"}
    monkeypatch.setattr(setup_github, "github_request", request)
    with pytest.raises(ValueError, match="confirmation"):
        asyncio.run(setup_github.create_workflow_pr(False))
    assert not calls
    first = asyncio.run(setup_github.create_workflow_pr(True))
    count = len(calls)
    assert asyncio.run(setup_github.create_workflow_pr(True)) == first
    assert len(calls) == count + 1
    assert first["url"] == "https://github.com/organization/portal/pull/7"
    assert not any(method == "PATCH" for _, method, _ in calls)
    tree = next(body for path, method, body in calls if path.endswith("/git/trees"))
    assert tree["base_tree"] == "original-tree"
    assert {entry["path"] for entry in tree["tree"]} == set(names)


def test_generated_feeds_sync_atomically_and_reject_invalid_refresh(configured, monkeypatch):
    import base64
    import hashlib
    import json
    import setup_github
    db.set_setup_setting("catalog_processing", {"mode": "github-hosted"})
    async def probe(*args, **kwargs):
        return {"github-hosted": {"available": True}}
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    metadata = json.dumps({"displayName": "Example", "cost": {"currency": "USD", "region": "eastus", "components": [{"billing": "fixed", "quantityPerMonth": 10}]}}).encode()
    prices = {"metadataSha256": hashlib.sha256(metadata).hexdigest(), "cost": {"currency": "USD", "region": "eastus", "pricingSource": "retail", "complete": True, "pricedAt": "2026-01-01T00:00:00+00:00", "components": [{"unitPrice": 2}]}}
    capacity = {"schemaVersion": 1, "tenantId": TENANT, "generated": "2026-01-01T00:00:00+00:00", "physicalCapacity": "unknown", "subscriptions": {SUBSCRIPTION: {"eastus": [{"name": "vCPUs", "limit": 10, "current": 2, "available": 8}]}}}
    data = {"meta": metadata, "prices": json.dumps(prices).encode(), "capacity": json.dumps(capacity).encode()}
    version = ["a" * 40]
    def blob(path, sha):
        return {"path": path, "sha": sha, "type": "blob", "mode": "100644", "size": len(data[sha])}
    async def request(path, *args):
        if "/git/ref/" in path:
            return {"object": {"sha": version[0]}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        if path.endswith("/root"):
            return {"tree": [{"path": name, "sha": name, "type": "tree"} for name in ("catalog", "site")]}
        if path.endswith("/catalog"):
            return {"tree": [{"path": "example", "sha": "example", "type": "tree"}]}
        if path.endswith("/example"):
            return {"tree": [blob("metadata.json", "meta"), blob("pricing.json", "prices")]}
        if path.endswith("/site"):
            return {"tree": [blob("capacity.json", "capacity"), {"path": "main.py", "type": "blob", "sha": "never-read"}]}
        return {"content": base64.b64encode(data[path.rsplit("/", 1)[1]]).decode()}
    monkeypatch.setattr(setup_github, "github_request", request)
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    result = asyncio.run(setup_github.sync_catalog())
    assert result["items"][0]["cost"]["totalMonthly"] == 20
    assert result["capacity"]["subscriptions"][SUBSCRIPTION]["eastus"][0]["available"] == 8
    version[0] = "b" * 40
    capacity["generated"] = None
    data["capacity"] = json.dumps(capacity).encode()
    with pytest.raises(ValueError):
        asyncio.run(setup_github.sync_catalog())
    assert setup_github.catalog_snapshot() == result


def test_workflow_endpoint_requires_admin_origin_and_confirmation(configured, monkeypatch):
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    client = TestClient(application)
    assert client.post("/api/admin/setup/github/workflows", json={"confirm": True}).status_code in (401, 403)
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    assert client.post("/api/admin/setup/github/workflows", json={"confirm": True}).status_code == 403
    response = client.post("/api/admin/setup/github/workflows", headers={"Origin": "http://testserver"}, json={})
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"]
    async def create(confirm):
        return {"status": "current"}
    monkeypatch.setattr(setup_routes.setup_github, "create_workflow_pr", create)
    assert client.post("/api/admin/setup/github/workflows", headers={"Origin": "http://testserver"}, json={"confirm": True}).status_code == 200
    assert client.post("/api/admin/setup/github/workflows", headers={"Origin": "http://testserver"}, json={"confirm": True}).status_code == 429


def test_capacity_is_not_leaked_via_catalog_json(configured, monkeypatch):
    import json
    import app
    import identity
    from fastapi import HTTPException
    from starlette.requests import Request
    monkeypatch.setattr(app, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(app.setup_github, "catalog_snapshot", lambda: {"items": [], "version": "a" * 40, "capacity": {"private": True}})
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(app.serve_gallery(Request({"type": "http", "headers": []}), "catalog.json"))
    assert error.value.status_code == 401
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: identity.CurrentUser("guest", "", "", "", "guest"))
    response = asyncio.run(app.serve_gallery(Request({"type": "http", "headers": []}), "catalog.json"))
    assert "capacity" not in json.loads(response.body)
    assert response.headers["cache-control"] == "no-store"


def test_auth_provider_preserves_bootstrap(configured):
    import setup_auth
    settings = setup_auth.AuthenticationSettings(kind="oidc", providerName="organization", clientId="app", issuer="https://id.example.com", discoveryUrl="https://id.example.com/.well-known/openid-configuration", publicUrl="https://portal.example.com")
    current = {"properties": {"platform": {"enabled": True}, "globalValidation": {"requireAuthentication": True, "redirectToProvider": "azureactivedirectory"}, "identityProviders": {"azureActiveDirectory": {"enabled": True, "registration": {"clientId": "bootstrap"}}}}}
    result = setup_auth.provider_document(current, settings)
    assert result["properties"]["identityProviders"]["azureActiveDirectory"] == current["properties"]["identityProviders"]["azureActiveDirectory"]
    assert result["properties"]["globalValidation"]["redirectToProvider"] == "azureactivedirectory"
    assert "customOpenIdConnectProviders" not in current["properties"]["identityProviders"]
    assert "clientCredential" not in result["properties"]["identityProviders"]["customOpenIdConnectProviders"]["organization"]["registration"]


@pytest.mark.parametrize("fields", [
    {"secretStorage": "none"},
    {"secretStorage": "key_vault"},
    {"secretStorage": "app_service", "keyVaultSecretName": "unused"},
    {"secretStorage": "key_vault", "keyVaultResourceId": "https://untrusted.example", "keyVaultSecretName": "credential"},
])
def test_entra_requires_valid_secret_storage(fields):
    import setup_auth
    with pytest.raises(ValueError):
        setup_auth.EntraRegistration(displayName="Portal", publicUrl="https://portal.example.com", **fields)


def test_oidc_no_secret_rejects_secret_setting():
    import setup_auth
    with pytest.raises(ValueError, match="No-secret"):
        setup_auth.AuthenticationSettings(kind="oidc", clientId="app", issuer="https://id.example.com", discoveryUrl="https://id.example.com/.well-known/openid-configuration", publicUrl="https://portal.example.com", secretStorage="none", secretSettingName="AUTH_SECRET")


def test_oidc_subject_cannot_claim_entra_role(configured, monkeypatch):
    import base64
    import json
    import entra_auth
    from starlette.requests import Request
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    db.set_setup_setting("authentication", {"providerName": "organization", "issuer": "https://id.example.com", "clientId": "app"})
    principal = {"auth_typ": "organization", "claims": [{"typ": "iss", "val": "https://id.example.com"}, {"typ": "aud", "val": "app"}, {"typ": "sub", "val": "123"}, {"typ": "email", "val": "admin@example.com"}, {"typ": "oid", "val": "entra-admin"}]}
    request = Request({"type": "http", "headers": [(b"x-ms-client-principal", base64.b64encode(json.dumps(principal).encode()))]})
    identity = entra_auth.identity_from_easy_auth(request)
    assert identity.oid.startswith("oidc:")
    assert db.upsert_user(identity.oid, identity.upn, identity.name) == "guest"
    db.set_user_role(identity.upn, "admin")
    assert db.get_role(identity.oid) == "guest"
    db.set_user_role_by_oid(identity.oid, "admin")
    assert db.get_role(identity.oid) == "admin"


@pytest.mark.parametrize("code,message", [("already_exists", "private"), ("custom", "name already exists on this account")])
def test_github_duplicate_repository_error_has_safe_recovery_guidance(code, message):
    import httpx
    import setup_github
    request = httpx.Request("POST", "https://api.github.com/user/repos")
    response = httpx.Response(422, request=request, json={"message": "private-response", "errors": [
        {"resource": "Repository", "field": "name", "code": code, "message": message}
    ]})
    failure = httpx.HTTPStatusError("private-exception", request=request, response=response)
    result = setup_github.repository_failure(failure, creating=True)
    assert "already exists" in result and "Connect an existing repository" in result
    assert "Copy portal source" in result
    assert "LICENSE" in result and "reviewed source import" in result
    assert "private-response" not in result and "private-exception" not in result
    assert "already exists" not in setup_github.repository_failure(failure, creating=False)


@pytest.mark.parametrize("status,body,expected", [
    (422, {"errors": "private-response"}, "rejected the repository settings"),
    (403, {}, "denied access"), (401, {}, "authorization expired"),
    (404, {}, "not found"), (429, {}, "rate-limited"), (502, [], "service availability"),
])
def test_github_repository_failure_reports_http_status_without_raw_content(status, body, expected):
    import httpx
    import setup_github
    request = httpx.Request("POST", "https://api.github.com/user/repos")
    response = httpx.Response(status, request=request, json=body)
    result = setup_github.repository_failure(httpx.HTTPStatusError("private", request=request, response=response), True)
    assert f"HTTP {status}" in result and expected in result
    assert "private" not in result and "inspect" in result.lower()


def test_github_repository_timeout_warns_that_creation_may_have_succeeded():
    import httpx
    import setup_github
    result = setup_github.repository_failure(httpx.ReadTimeout("private"), True)
    assert "may have succeeded" in result and "do not create another repository" in result
    assert "private" not in result


def test_github_duplicate_create_returns_json_without_seeding_or_saving(configured, monkeypatch):
    import httpx
    import setup_github
    monkeypatch.setattr(setup_github, "source_seed", lambda: {})
    calls = []

    async def request(path, method="GET", body=None):
        calls.append((path, method))
        if path == "/user":
            return {"login": "organization"}
        assert path == "/user/repos" and method == "POST"
        outbound = httpx.Request(method, "https://api.github.com" + path)
        response = httpx.Response(422, request=outbound, json={"errors": [
            {"resource": "Repository", "field": "name", "code": "already_exists"}
        ]})
        raise httpx.HTTPStatusError("private", request=outbound, response=response)

    monkeypatch.setattr(setup_github, "github_request", request)
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
    response = TestClient(application).put("/api/admin/setup/github", headers={"Origin": "http://testserver"}, json={
        "owner": "organization", "repository": "portal", "create": True, "confirm": True,
    })
    assert response.status_code == 502
    assert "already exists" in response.json()["detail"]
    assert calls == [("/user", "GET"), ("/user/repos", "POST")]
    assert db.get_setup_setting("github") is None


def test_setup_client_handles_empty_redirected_and_error_responses():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Setup request helper regression")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/setup.js', 'utf8');
const helper = source.slice(source.indexOf('  async function request('), source.indexOf('  function field('));
async function requestWith(response, method = 'PUT', path = '/github') {
  const context = { fetch: async () => response };
  vm.createContext(context);
  vm.runInContext(helper, context);
    return context.request(path, method, {create:true});
}
(async () => {
    await assert.rejects(requestWith({status:502, ok:false, text:async()=>''}), /empty or non-JSON.*HTTP 502.*may have completed.*Do not create another repository/);
  await assert.rejects(requestWith({status:200, ok:true, text:async()=>'<html>private-details</html>'}), error => /non-JSON/.test(error.message) && !/private-details/.test(error.message));
  await assert.rejects(requestWith({status:200, ok:true, redirected:true, text:async()=>{throw new Error('body must not be read');}}), /session needs sign-in/);
  await assert.rejects(requestWith({status:401, ok:false, text:async()=>''}), /session needs sign-in/);
  await assert.rejects(requestWith({status:400, ok:false, text:async()=>JSON.stringify({detail:'Repository already exists'})}), /Repository already exists/);
  await assert.rejects(requestWith({status:200, ok:true, text:async()=>'null'}), /unexpected response/);
  await assert.rejects(requestWith({status:422, ok:false, text:async()=>JSON.stringify({detail:[{loc:['body','subscriptionId']}]})}, 'PUT', '/subscriptions'), /Subscription ID must be a nonzero GUID/);
  assert.equal((await requestWith({status:200, ok:true, text:async()=>JSON.stringify({enabled:true})})).enabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script], cwd=SERVER_DIR.parent.parent, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_github_seed_initializes_license_before_bulk_commit(configured, monkeypatch):
    import base64
    import hashlib
    import setup_github
    files = {"LICENSE": base64.b64encode(b"MIT").decode(), "site/main.py": base64.b64encode(b"app").decode()}
    monkeypatch.setattr(setup_github, "source_seed", lambda: files)
    calls = []
    async def request(path, method="GET", body=None, **kwargs):
        calls.append((path, method, body))
        if path == "/user": return {"login": "organization"}
        if path == "/user/repos": return {"full_name": "organization/portal", "private": True}
        if path.endswith("/branches"): return [{"name": "main"}]
        if "/contents/" in path: return {"commit": {"sha": "initial"}}
        if "/git/ref/" in path: return {"object": {"sha": "seeded" if any(verb == "PATCH" for _, verb, _ in calls) else "initial"}}
        if path.endswith("/commits/initial"): return {"tree": {"sha": "license-tree"}}
        if path.endswith("/trees/license-tree"): return {"tree": [{"path": "LICENSE", "mode": "100644", "type": "blob", "sha": hashlib.sha1(b"blob 3\0MIT", usedforsecurity=False).hexdigest()}]}
        if path.endswith("/commits"): return {"sha": "seeded"}
        return {"sha": "object"}
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", create=True, seed=True, confirm=True)))
    assert result["seeded"]
    assert calls[1][2]["private"] is True
    assert calls[2][0].endswith("/contents/LICENSE")
    assert calls[-2][1:] == ("PATCH", {"sha": "seeded", "force": False})
    assert calls[-1][1] == "GET"
    assert next(body for path, method, body in calls if path.endswith("/commits"))["parents"] == ["initial"]
    assert next(body for path, method, body in calls if path.endswith("/trees"))["tree"] == [
        {"path": "LICENSE", "mode": "100644", "type": "blob", "content": "MIT"},
        {"path": "site/main.py", "mode": "100644", "type": "blob", "content": "app"},
    ]
    assert not any(path.endswith("/blobs") for path, _, _ in calls)


@pytest.mark.parametrize("status,message,headers,expected", [
    (403, "refusing to allow an OAuth App to create workflow without workflow scope", {}, "workflow permission"),
    (403, "protected branch update failed: private repository data", {}, "branch protection"),
    (422, "Repository rule violations found: private rule data", {}, "repository rules"),
    (403, "private response", {"x-ratelimit-remaining": "0"}, "rate-limited"),
    (429, "private response", {}, "rate-limited"),
    (401, "private response", {}, "authorization expired"),
    (403, "private response", {}, "denied the write"),
    (409, "private response", {}, "repository state"),
    (503, "private response", {}, "service availability"),
])
def test_seed_failure_reports_safe_operation_and_guidance(status, message, headers, expected):
    import httpx
    import setup_github
    request = httpx.Request("PATCH", "https://api.github.com/repos/private/repository/git/refs/heads/main", headers={"Authorization": "Bearer private-token"})
    response = httpx.Response(status, request=request, json={"message": message}, headers=headers)
    failure = httpx.HTTPStatusError("private exception", request=request, response=response)
    result = setup_github.seed_failure(failure, "updating the repository branch")
    assert expected in result
    assert f"HTTP {status}" in result
    assert "updating the repository branch" in result
    assert "private" not in result.replace("private repository", "repository")
    assert "private-token" not in result


def test_partial_seed_failure_identifies_stage_without_deleting_repo(configured, monkeypatch):
    import base64
    import httpx
    import setup_github
    monkeypatch.setattr(setup_github, "source_seed", lambda: {"LICENSE": base64.b64encode(b"MIT").decode()})
    async def initial_base(*args):
        return "initial", False
    monkeypatch.setattr(setup_github, "source_copy_base", initial_base)
    calls = []
    async def request(path, method="GET", body=None):
        calls.append((path, method))
        if path == "/user": return {"login": "organization"}
        if path == "/user/repos": return {"full_name": "organization/portal", "private": True}
        if "/contents/" in path: return {"commit": {"sha": "initial"}}
        if path.endswith("/trees"):
            request = httpx.Request(method, "https://api.github.com" + path)
            response = httpx.Response(403, request=request, json={"message": "API rate limit exceeded private data"})
            raise httpx.HTTPStatusError("private", request=request, response=response)
        raise AssertionError("Unexpected request")
    monkeypatch.setattr(setup_github, "github_request", request)
    with pytest.raises(ValueError, match="creating the source tree.*rate-limited"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", create=True, seed=True, confirm=True)))
    assert db.get_setup_setting("github")["seeded"] is False
    assert not any(method == "DELETE" for _, method in calls)


@pytest.fixture
def source_copy_remote(configured, monkeypatch):
    import base64
    import hashlib
    import setup_github
    contents = {"LICENSE": "MIT", "site/main.py": "app\n"}
    bundle = {name: base64.b64encode(content.encode()).decode() for name, content in contents.items()}
    entries = [{"path": name, "type": "blob", "mode": "100644", "sha": hashlib.sha1(
        f"blob {len(content.encode())}\0".encode() + content.encode(), usedforsecurity=False).hexdigest()}
        for name, content in contents.items()]
    state = {"head": "initial", "entries": entries[:1], "private": True, "push": True,
             "archived": False, "truncated": False, "workflow": True, "drift": False,
             "readback_failure": False, "race_initial": False, "calls": []}
    monkeypatch.setattr(setup_github, "source_seed", lambda: bundle)

    async def request(path, method="GET", body=None, **kwargs):
        state["calls"].append((path, method, body))
        if path == "/user":
            assert kwargs["require_workflow"] is True
            if not state["workflow"]:
                raise ValueError("GitHub workflow permission is missing")
            return {"login": "organization"}
        if path == "/repos/organization/portal":
            return {"full_name": "organization/portal", "private": state["private"],
                    "archived": state["archived"], "permissions": {"push": state["push"]}}
        if path.endswith("/branches"):
            return [{"name": "main"}] if state["head"] else []
        if "/git/ref/" in path:
            return {"object": {"sha": state["head"]}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "tree"}}
        if path.endswith("/trees/tree"):
            return {"tree": state["entries"], "truncated": state["truncated"]}
        if path.endswith("/contents/LICENSE"):
            assert method == "PUT" and "sha" not in body
            state["head"] = "initial"
            state["entries"] = entries[:1]
            if state["race_initial"]:
                state["entries"] = [*state["entries"], {"path": "README.md", "type": "blob", "mode": "100644", "sha": "custom"}]
            return {"commit": {"sha": "initial"}}
        if path.endswith("/git/trees"):
            assert method == "POST"
            assert {entry["path"]: entry["content"] for entry in body["tree"]} == contents
            assert all("sha" not in entry for entry in body["tree"])
            return {"sha": "source-tree"}
        if path.endswith("/git/commits"):
            assert body["parents"] == ["initial"]
            if state["drift"]:
                state["head"] = "other-user-commit"
            return {"sha": "copied"}
        if "/git/refs/heads/" in path:
            assert method == "PATCH" and body == {"sha": "copied", "force": False}
            state["head"] = "other-user-commit" if state["readback_failure"] else "copied"
            state["entries"] = entries
            return {"object": {"sha": state["head"]}}
        raise AssertionError(f"Unexpected GitHub call: {method} {path}")

    monkeypatch.setattr(setup_github, "github_request", request)
    return state


@pytest.mark.parametrize("empty", [True, False])
def test_github_source_copy_recovers_then_retries_without_writes(source_copy_remote, empty):
    import setup_github
    state = source_copy_remote
    if empty:
        state["head"] = None
        state["entries"] = []
    settings = setup_github.GitHubSettings(owner="organization", repository="portal", seed=True, confirm=True)
    assert asyncio.run(setup_github.configure(settings))["seeded"] is True
    assert state["head"] == "copied"
    assert not any(path.endswith("/blobs") or path == "/user/repos" for path, _, _ in state["calls"])
    assert sum(method == "PUT" for _, method, _ in state["calls"]) == int(empty)
    state["calls"].clear()
    assert asyncio.run(setup_github.configure(settings))["seeded"] is True
    assert all(method == "GET" for _, method, _ in state["calls"])
    assert asyncio.run(setup_github.configure(settings.model_copy(update={"seed": False})))["seeded"] is True


@pytest.mark.parametrize("case", ["code", "license", "symlink", "submodule", "truncated", "public", "no-push", "archived"])
def test_github_source_copy_refuses_unsafe_targets_without_writes(source_copy_remote, case):
    import setup_github
    state = source_copy_remote
    if case == "code":
        state["entries"].append({"path": "README.md", "type": "blob", "mode": "100644", "sha": "custom"})
    elif case == "license":
        state["entries"][0] = {**state["entries"][0], "sha": "changed"}
    elif case == "symlink":
        state["entries"][0] = {**state["entries"][0], "mode": "120000"}
    elif case == "submodule":
        state["entries"][0] = {**state["entries"][0], "type": "commit", "mode": "160000"}
    else:
        key = {"truncated": "truncated", "public": "private", "no-push": "push", "archived": "archived", "workflow": "workflow"}[case]
        state[key] = case in ("truncated", "archived")
    with pytest.raises(ValueError):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", seed=True, confirm=True)))
    assert all(method == "GET" for _, method, _ in state["calls"])
    assert db.get_setup_setting("github") is None


@pytest.mark.parametrize("case", ["drift", "readback_failure", "race_initial"])
def test_github_source_copy_stops_on_concurrent_change(source_copy_remote, case):
    import setup_github
    state = source_copy_remote
    state[case] = True
    if case == "race_initial":
        state["head"] = None
    with pytest.raises(ValueError, match="source copy did not finish"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", seed=True, confirm=True)))
    assert db.get_setup_setting("github")["seeded"] is False
    if case != "readback_failure":
        assert not any(method == "PATCH" for _, method, _ in state["calls"])
    assert not any(method == "DELETE" or (body or {}).get("force") for _, method, body in state["calls"])


def test_github_source_copy_route_requires_admin_origin_and_confirmation(source_copy_remote, monkeypatch):
        import identity
        import setup_github
        application = FastAPI()
        application.include_router(setup_routes.build_setup_router())
        client = TestClient(application)
        payload = {"owner": "organization", "repository": "portal", "seed": True, "confirm": True}
        monkeypatch.setattr(identity, "_resolve_identity", lambda request: CurrentUser("guest", "", "", TENANT, "guest"))
        assert client.put("/api/admin/setup/github", json=payload, headers={"Origin": "http://testserver"}).status_code == 403
        application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", TENANT, "admin")
        assert client.put("/api/admin/setup/github", json=payload).status_code == 403
        assert client.put("/api/admin/setup/github", json={**payload, "confirm": False}, headers={"Origin": "http://testserver"}).status_code == 400
        assert not source_copy_remote["calls"]
        result = client.put("/api/admin/setup/github", json=payload, headers={"Origin": "http://testserver"})
        assert result.status_code == 200 and result.json()["seeded"] is True
        assert setup_github.catalog_snapshot() == {}


def test_setup_wizard_preserves_drafts_and_guards_navigation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Setup UI regression")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/setup.js', 'utf8');
const code = source.slice(source.indexOf('  async function open()'), source.indexOf('  function branding('));
let focused;
class Element {
    constructor(tag, props = {}, children = []) {
        this.tag = tag; this.attrs = {...props}; this.children = []; this.listeners = {};
        this.dataset = {}; this.isConnected = true; this.hidden = !!props.hidden;
        this.disabled = false; this.textContent = props.text || ''; this.value = props.value || '';
        this.append(...(Array.isArray(children) ? children : [children]));
    }
    append(...items) { items.filter(Boolean).forEach(item => this.appendChild(item)); }
    appendChild(item) { item.parent = this; this.children.push(item); return item; }
    remove() { this.parent.children = this.parent.children.filter(item => item !== this); }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    focus() { focused = this; }
    click() { if (!this.disabled) this.listeners.click?.(); }
    descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
    querySelectorAll(selector) { return this.descendants().filter(child => selector === 'button' && child.tag === 'button'); }
    querySelector() { return this.descendants().find(child => child.attrs['aria-busy'] === 'true' || child.attrs['data-setup-busy'] === 'true'); }
}
const root = new Element('div');
const builds = [0,0,0,0];
let requests = 0, closed = 0, observer;
const build = index => body => { builds[index]++; body.appendChild(new Element('input', {value:'initial'})); };
const context = {
    el:(...args)=>new Element(...args),
    panel:{open:render=>render(root), close:()=>closed++},
    request:async()=>{requests++;return {primaryAuthentication:{clientId:'configured'},subscriptions:[]};},
    authentication:build(0), github:build(1), subscriptions:build(2), branding:build(3),
    MutationObserver:class {constructor(callback){observer=callback;} observe(){}},
};
vm.createContext(context); vm.runInContext(code,context);
(async()=>{
    await context.open();
    const tabs=root.querySelectorAll('button').filter(button=>button.attrs.role==='tab');
    const panels=root.descendants().filter(element=>element.attrs.role==='tabpanel');
    const navigation=root.descendants().find(element=>element.attrs.class==='setup-navigation-buttons');
    const [back,next]=navigation.children;
    assert.equal(back.disabled,true); assert.equal(next.textContent,'Next: GitHub');
    next.click();
    const draft=panels[1].descendants().find(element=>element.tag==='input');
    draft.value='unsaved repository';
    next.click(); back.click();
    assert.equal(panels[1].descendants().find(element=>element.tag==='input'),draft);
    assert.equal(draft.value,'unsaved repository'); assert.deepEqual(builds,[1,1,1,0]);
    assert.equal(panels.filter(element=>!element.hidden).length,1);
    panels[1].children[0].setAttribute('aria-busy','true'); observer();
    assert.equal(back.disabled,true); assert.equal(next.disabled,true);
    assert.equal(tabs.every(tab=>tab.disabled),true); next.click();
    assert.equal(panels[1].hidden,false);
    panels[1].children[0].setAttribute('aria-busy','false'); observer();
    assert.equal(next.disabled,false);
    tabs[1].listeners.keydown({key:'End',preventDefault(){}});
    assert.equal(tabs[3].attrs['aria-selected'],'true'); assert.equal(focused,tabs[3]);
    assert.equal(next.textContent,'Close setup'); next.click();
    assert.equal(closed,1); assert.equal(requests,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], cwd=SERVER_DIR.parent.parent, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_setup_github_copy_action_requires_explicit_selection_and_confirmation():
        node = shutil.which("node")
        if not node:
                pytest.skip("Node.js is required for the Setup UI regression")
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/setup.js', 'utf8');
const code = source.slice(source.indexOf('  function github('), source.indexOf('  function subscriptions('));
const controls = new Map();
const buttons = new Map();
const requests = [];
const confirmations = [];
let approved = true;
let failPath = '';
let deviceStatus = 'connected';
const elements = [];
function element(tag, props = {}, children = []) {
    const node = Object.assign({tag, children, isConnected:true, listeners:{}, value:'', dataset:{},
        classList:{add(){}}, setAttribute(name,value){this[name]=value;},
        append(...items){this.children.push(...items);}, appendChild(item){this.children.push(item);},
        replaceChildren(...items){this.children=items;},
        addEventListener(event, handler){this.listeners[event]=handler;}
    }, props);
    elements.push(node);
    return node;
}
function field(label, value = '') {
    const control = element('input', {value});
    controls.set(label, control);
    return {control, node:element('label')};
}
const context = {el:element, field,
    choice(label, values, selected){const entry=field(label, selected); entry.control.options=values.map(([value,text])=>({value,text})); Object.defineProperty(entry.control,'selectedOptions',{get(){return this.options.filter(option=>option.value===this.value);}}); return entry;},
    button(label, action){const node=element('button',{textContent:label,action}); buttons.set(label,node); return node;},
    confirm(message){confirmations.push(message); return approved;},
    setTimeout:()=>1, clearTimeout(){}, AbortSignal,
    request:async(path, method='GET', data)=>{
        requests.push({path,method,data});
        if(path===failPath) throw new Error('Simulated request failure');
        if(path==='/github/oauth-client') return {clientId:data.clientId,configured:true};
        if(path==='/github/device/start') return {flowId:'test-flow',userCode:'TEST-CODE',interval:5};
        if(path==='/github/device/poll') return {status:deviceStatus,message:'Authorization failed'};
        if(path==='/github/discover') return {account:{login:'organization'},owners:[{login:'organization'}],repositories:[{fullName:'organization/portal',owner:'organization',name:'portal',defaultBranch:'main',private:true}]};
        if(path==='/github/runners/probe') return {repository:'organization/portal',checkedAt:Date.now()/1000,'github-hosted':{available:false,state:'unverified'},'self-hosted':{available:false,state:'unavailable',candidates:[]}};
        assert.equal(path,'/github');
        return {enabled:data.enabled,owner:data.owner,repository:data.repository,branch:data.branch,seeded:data.seed};
    }
};
vm.createContext(context);
vm.runInContext(code, context);
(async()=>{
    const status=element('p');
    const body=element('div');
    context.github(body, {github:{enabled:true,owner:'organization',repository:'portal'},githubConnected:true,githubDeviceConfigured:true}, status);
    await new Promise(resolve=>setImmediate(resolve));
    const mode=controls.get('Repository action');
    assert.equal(controls.get('Catalog processing').value,'container');
    assert.equal(controls.get('Catalog processing').options.find(option=>option.value==='github-hosted').disabled,true);
    assert.equal(controls.get('Catalog processing').options.find(option=>option.value==='self-hosted').disabled,true);
    const save=buttons.get('Save GitHub configuration');
    await save.action();
    assert.equal(requests.at(-1).data.seed,false);
    assert.match(status.textContent,/No source files were copied/);
    mode.value='copy'; mode.listeners.change();
    assert.equal(save.textContent,'Copy portal source');
    assert.match(body.dataset.nextAction,/Next:.*Copy portal source/);
    approved=false;
    const before=requests.length;
    await save.action();
    assert.equal(requests.length,before);
    approved=true;
    await save.action();
    const copy=requests.at(-1).data;
    assert.equal(copy.seed,true); assert.equal(copy.create,false); assert.equal(copy.repository,'portal'); assert.equal(copy.confirm,true);
    assert.match(confirmations.at(-1),/organization\/portal on branch main.*Existing code will not be overwritten/);
    assert.match(status.textContent,/Portal source verified in organization\/portal/);
    mode.value='create'; controls.get('GitHub account').value='organization'; controls.get('New repository name').value='new-portal';
    mode.listeners.change();
    assert.equal(save.textContent,'Create repository + copy source');
    assert.match(body.dataset.nextAction,/Next:.*Creation requires your confirmation/);
    await save.action();
    assert.equal(requests.at(-1).data.seed,true); assert.equal(requests.at(-1).data.create,true);
    assert.equal(mode.value,'connect'); assert.equal(controls.get('Repository').value,'organization/new-portal');

    elements.length=0; controls.clear(); buttons.clear();
    context.github(element('div'), {github:{enabled:true},githubConnected:false,githubDeviceConfigured:false}, element('p'));
    const groups=elements.filter(node=>node.tag==='fieldset');
    assert.equal(groups.length,4);
    const locked=()=>groups.map(group=>group.disabled);
    assert.deepEqual(locked(),[false,true,true,true]);
    assert.equal(groups[1].children[2].inert,true);
    const oauth=buttons.get('Save OAuth application');
    controls.get('GitHub OAuth App client ID').value='organization-owned-client';
    failPath='/github/oauth-client';
    await assert.rejects(oauth.action(),/Simulated request failure/);
    assert.deepEqual(locked(),[false,true,true,true]);
    failPath=''; await oauth.action();
    assert.deepEqual(locked(),[false,false,true,true]);
    const connect=buttons.get('Connect GitHub');
    failPath='/github/device/start';
    await assert.rejects(connect.action(),/Simulated request failure/);
    assert.deepEqual(locked(),[false,false,true,true]);
    failPath=''; await connect.action();
    assert.deepEqual(locked(),[true,false,true,true]);
    deviceStatus='error'; await connect.action();
    assert.deepEqual(locked(),[false,false,true,true]);
    deviceStatus='connected'; await connect.action();
    failPath='/github/discover'; await connect.action();
    assert.deepEqual(locked(),[false,false,true,true]);
    failPath=''; await connect.action(); await connect.action();
    assert.deepEqual(locked(),[false,false,false,true]);
    controls.get('Repository').value='organization/portal';
    const repositorySave=buttons.get('Save GitHub configuration');
    failPath='/github'; await assert.rejects(repositorySave.action(),/Simulated request failure/);
    assert.deepEqual(locked(),[false,false,false,true]);
    failPath=''; await repositorySave.action();
    assert.deepEqual(locked(),[false,false,false,false]);
    controls.get('Repository action').value='copy'; controls.get('Repository action').listeners.change();
    assert.deepEqual(locked(),[false,false,false,true]);
    controls.get('GitHub OAuth App client ID').listeners.input();
    assert.deepEqual(locked(),[false,true,true,true]);

    elements.length=0; controls.clear(); buttons.clear();
    const renewalBody=element('div');
    const renewalSettings={github:{enabled:true,owner:'organization',repository:'portal',branch:'release/v1.0',seeded:true},githubConnected:false,githubDeviceConfigured:true};
    context.github(renewalBody, renewalSettings, element('p'));
    assert.match(renewalBody.dataset.nextAction,/reconnect the same GitHub account/);
    const renewalStart=requests.length;
    const renewal=buttons.get('Connect GitHub');
    await renewal.action();
    assert.match(confirmations.at(-1),/previously connected GitHub account.*repository and branch will stay unchanged/);
    await renewal.action();
    assert.equal(controls.get('Repository').value,'organization/portal');
    assert.equal(renewalSettings.github.enabled,true);
    assert.equal(renewalSettings.github.branch,'release/v1.0');
    assert.equal(elements.filter(node=>node.tag==='fieldset')[3].disabled,false);
    assert.equal(requests.slice(renewalStart).some(request=>request.path==='/github'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run([node, "-e", script], cwd=SERVER_DIR.parent.parent, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr


def test_default_source_copy_does_not_require_workflow_scope(configured, monkeypatch):
    import base64
    import setup_github
    files = {"LICENSE": base64.b64encode(b"MIT").decode(), ".github/workflows/portal.yml": base64.b64encode(b"name: example").decode()}
    monkeypatch.setattr(setup_github, "source_seed", lambda: files)
    calls = []
    async def request(path, method="GET", body=None, **kwargs):
        calls.append((path, method, kwargs))
        assert path == "/user" and method == "GET" and not kwargs.get("require_workflow")
        raise ValueError("Stop at account read")
    monkeypatch.setattr(setup_github, "github_request", request)
    with pytest.raises(ValueError, match="Stop at account read"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="organization", repository="portal", create=True, seed=True, confirm=True)))
    assert len(calls) == 1
    assert db.get_setup_setting("github") is None


@pytest.mark.parametrize("scopes,allowed", [("repo, read:org", False), ("", False), ("repo, read:org, workflow", True)])
def test_github_request_checks_actual_token_scope_header(monkeypatch, scopes, allowed):
    import httpx
    import setup_github
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"login": "organization", "id": 1}, headers={"X-OAuth-Scopes": scopes})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(setup_github.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    if allowed:
        result = asyncio.run(setup_github.github_request("/user", token="synthetic-private-token", require_workflow=True))
        assert result["login"] == "organization"
    else:
        with pytest.raises(ValueError, match="workflow permission") as failure:
            asyncio.run(setup_github.github_request("/user", token="synthetic-private-token", require_workflow=True))
        assert "synthetic-private-token" not in str(failure.value)
    assert len(requests) == 1 and requests[0].method == "GET"
    assert asyncio.run(setup_github.github_request("/user", token="synthetic-private-token"))["login"] == "organization"


def test_streamed_seed_error_is_bounded_and_classified(monkeypatch):
    import httpx
    import setup_github
    real_client = httpx.AsyncClient
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":"refusing workflow update without workflow scope; private details"}'
    transport = httpx.MockTransport(lambda request: httpx.Response(403, stream=Stream()))
    monkeypatch.setattr(setup_github.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(httpx.HTTPStatusError) as failure:
        asyncio.run(setup_github.github_request("/test", token="synthetic-token"))
    message = setup_github.seed_failure(failure.value, "updating the repository branch")
    assert "workflow permission" in message
    assert "private details" not in message
    assert "synthetic-token" not in message


def test_unread_seed_response_still_reports_status():
    import httpx
    import setup_github
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"private"
    request = httpx.Request("POST", "https://api.github.com/test")
    response = httpx.Response(403, request=request, stream=Stream())
    failure = httpx.HTTPStatusError("private", request=request, response=response)
    message = setup_github.seed_failure(failure, "creating the source tree")
    assert "HTTP 403" in message and "denied the write" in message


def test_auth_apply_rejects_wrong_host(configured, monkeypatch):
    import setup_auth
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    async def arm(path, **kwargs):
        assert kwargs.get("method", "GET") == "GET"
        return {"properties": {"hostNames": ["actual.example.com"]}}
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    settings = setup_auth.AuthenticationSettings(kind="oidc", clientId="app", issuer="https://id.example.com", discoveryUrl="https://id.example.com/.well-known/openid-configuration", publicUrl="https://wrong.example.com", confirm=True)
    with pytest.raises(ValueError, match="hostname"):
        asyncio.run(setup_auth.apply_provider(settings))


@pytest.mark.parametrize("storage_mode,vault_failure", [("app_service", False), ("key_vault", False), ("key_vault", True)])
def test_entra_registration_secret_never_leaves_selected_storage(configured, monkeypatch, storage_mode, vault_failure):
    import json
    import setup_auth
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    writes = []
    async def arm(path, method="GET", body=None, **kwargs):
        if "/appsettings/list" in path: return {"properties": {"EXISTING": "preserve"}}
        if method == "PUT":
            writes.append(body)
            return {}
        return {"properties": {"hostNames": ["portal.example.com"]}}
    class Credential:
        def get_token(self, *args): return type("Token", (), {"token": "synthetic-test-token"})()
    class Response:
        def __init__(self, body): self.body = body
        def raise_for_status(self): pass
        def json(self): return self.body
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, path, **kwargs):
            if path.endswith("/applications"): return Response({"appId": SUBSCRIPTION, "id": "created-object"})
            if path.endswith("/servicePrincipals"): return Response({"id": "created-principal"})
            return Response({"secretText": "generated-test-secret", "keyId": "test-key-id"})
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    monkeypatch.setattr(setup_auth.azure_deploy, "_get_credential", lambda: Credential())
    monkeypatch.setattr(setup_auth.httpx, "AsyncClient", Client)
    vault_writes = []
    uri = "https://customervault.vault.azure.net/secrets/portal-credential"
    async def target(settings, host): return uri
    async def vault(uri, *, value=None, tags=None):
        if value is None: return None
        if vault_failure:
            raise ValueError("Key Vault write refused")
        vault_writes.append(value)
        return {"id": uri + "/version1", "tags": tags}
    monkeypatch.setattr(setup_auth, "key_vault_target", target)
    monkeypatch.setattr(setup_auth, "key_vault_secret", vault)
    fields = {"secretStorage": storage_mode}
    if storage_mode == "key_vault":
        fields.update({"keyVaultResourceId": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.KeyVault/vaults/customervault", "keyVaultSecretName": "portal-credential"})
    if vault_failure:
        with pytest.raises(ValueError, match="Key Vault write refused"):
            asyncio.run(setup_auth.create_entra_registration(setup_auth.EntraRegistration(displayName="Portal", publicUrl="https://portal.example.com", confirm=True, **fields)))
        assert writes == []
        assert db.get_setup_setting("created_entra_registration")["credentialStored"] is False
        assert "generated-test-secret" not in json.dumps(db.get_setup_setting("created_entra_registration"))
        return
    result = asyncio.run(setup_auth.create_entra_registration(setup_auth.EntraRegistration(displayName="Portal", publicUrl="https://portal.example.com", confirm=True, **fields)))
    expected = "generated-test-secret" if storage_mode == "app_service" else f"@Microsoft.KeyVault(SecretUri={uri}/version1)"
    assert writes[0]["properties"] == {"EXISTING": "preserve", "PORTAL_ORGANIZATION_AUTH_SECRET": expected}
    assert vault_writes == ([] if storage_mode == "app_service" else ["generated-test-secret"])
    assert result["credentialStored"] is True
    assert result["secretStorage"] == storage_mode
    assert "generated-test-secret" not in json.dumps(result)
    assert "generated-test-secret" not in json.dumps(db.get_setup_setting("created_entra_registration"))


def test_github_credential_reference_has_no_secret_value(configured, monkeypatch):
    import setup_auth
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    writes = []
    async def arm(path, method="GET", body=None, **kwargs):
        if "/appsettings/list" in path: return {"properties": {"OTHER": "preserved"}}
        if method == "PUT": writes.append(body)
        return {}
    async def target(settings, host, *, tenant_id=None):
        assert tenant_id is None
        return "https://customervault.vault.azure.net/secrets/github-token"
    async def vault(uri): return {"id": uri + "/version1", "tags": {}}
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    monkeypatch.setattr(setup_auth, "key_vault_target", target)
    monkeypatch.setattr(setup_auth, "key_vault_secret", vault)
    settings = setup_auth.GitHubCredential(secretStorage="key_vault", keyVaultResourceId=f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.KeyVault/vaults/customervault", keyVaultSecretName="github-token", confirm=True)
    result = asyncio.run(setup_auth.configure_github_credential(settings))
    assert writes == [{"properties": {"OTHER": "preserved", "GALLERY_GITHUB_TOKEN": "@Microsoft.KeyVault(SecretUri=https://customervault.vault.azure.net/secrets/github-token/version1)"}}]
    assert result == db.get_setup_setting("github_credential")
    assert result["secretStorage"] == "key_vault"


def test_unresolved_github_reference_is_not_used_as_token(monkeypatch):
    import setup_github
    import github_intake
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "@Microsoft.KeyVault(SecretUri=https://customervault.vault.azure.net/secrets/github-token)")
    assert github_intake._token() == ""
    with pytest.raises(ValueError):
        asyncio.run(setup_github.github_request("/user"))


@pytest.mark.parametrize("problem", [None, "tenant", "endpoint", "identity"])
def test_key_vault_destination_is_verified(configured, monkeypatch, problem):
    import setup_auth
    vault_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.KeyVault/vaults/customervault"
    identity_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.ManagedIdentity/userAssignedIdentities/setup"
    monkeypatch.setenv("AZURE_CLIENT_ID", SUBSCRIPTION)
    settings = setup_auth.EntraRegistration(displayName="Portal", publicUrl="https://portal.example.com", secretStorage="key_vault", keyVaultResourceId=vault_id, keyVaultSecretName="portal-secret")
    host = {"properties": {"keyVaultReferenceIdentity": identity_id}, "identity": {"userAssignedIdentities": {identity_id: {"clientId": SUBSCRIPTION}}}}
    if problem == "identity": host["properties"]["keyVaultReferenceIdentity"] = "SystemAssigned"
    async def arm(path):
        return {"id": vault_id, "properties": {"tenantId": "other-tenant" if problem == "tenant" else TENANT, "vaultUri": "https://untrusted.example" if problem == "endpoint" else "https://customervault.vault.azure.net/"}}
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    if problem:
        with pytest.raises(ValueError): asyncio.run(setup_auth.key_vault_target(settings, host))
    else:
        assert asyncio.run(setup_auth.key_vault_target(settings, host)) == "https://customervault.vault.azure.net/secrets/portal-secret"


def test_key_vault_read_discards_secret_value(monkeypatch):
    import httpx
    import setup_auth
    uri = "https://customervault.vault.azure.net/secrets/credential"
    def respond(request):
        assert request.url.host == "customervault.vault.azure.net"
        return httpx.Response(200, json={"id": uri + "/version1", "value": "secret-not-for-browser", "tags": {"owner": "organization"}, "attributes": {"enabled": True}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(setup_auth.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    class Credential:
        def get_token(self, scope):
            assert scope == "https://vault.azure.net/.default"
            return type("Token", (), {"token": "synthetic"})()
    monkeypatch.setattr(setup_auth.azure_deploy, "_get_credential", lambda: Credential())
    result = asyncio.run(setup_auth.key_vault_secret(uri))
    assert result == {"id": uri + "/version1", "tags": {"owner": "organization"}}


def test_no_secret_oidc_does_not_read_secret_settings(configured, monkeypatch):
    import setup_auth
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    async def arm(path, method="GET", body=None, **kwargs):
        assert "/appsettings" not in path
        if method == "PUT":
            registration = body["properties"]["identityProviders"]["customOpenIdConnectProviders"]["organization"]["registration"]
            assert "clientCredential" not in registration
            return {}
        if "/config/" in path:
            return {"properties": {"platform": {"enabled": True}, "globalValidation": {"requireAuthentication": True}}}
        return {"properties": {"hostNames": ["portal.example.com"]}}
    monkeypatch.setattr(setup_auth.setup_cloud, "arm_request", arm)
    settings = setup_auth.AuthenticationSettings(kind="oidc", clientId="app", issuer="https://id.example.com", discoveryUrl="https://id.example.com/.well-known/openid-configuration", publicUrl="https://portal.example.com", secretStorage="none", confirm=True)
    result = asyncio.run(setup_auth.apply_provider(settings))
    assert result["secretStorage"] == "none"


def test_sync_failure_preserves_catalog(configured, monkeypatch):
    import setup_github
    previous = {"version": "b" * 40, "items": [{"path": "working"}]}
    db.set_setup_setting("catalog_snapshot", previous)
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    async def fail(*args): raise ValueError("network or invalid catalog")
    monkeypatch.setattr(setup_github, "github_request", fail)
    with pytest.raises(ValueError): asyncio.run(setup_github.sync_catalog())
    assert setup_github.catalog_snapshot() == previous


@pytest.mark.parametrize("capacity_status", ["unavailable", "headroom", "unknown"])
@pytest.mark.parametrize("mode", ["container", "github-hosted"])
@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_deployment_always_checks_before_provisioning(configured, monkeypatch, capacity_status, mode, engine):
    import api
    from identity import require_user
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    db.set_setup_setting("catalog_processing", {"mode": mode})
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION, "pricingMode": "retail"}])
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": {"path": "example", "parameters": [], "engines": [engine]}})
    monkeypatch.setattr(api, "_terraform_exec_available", lambda: True)
    scheduled = []
    def schedule(coroutine):
        scheduled.append(True)
        coroutine.close()
    async def regions(identifier):
        return [{"name": "eastus"}]
    async def check(item, subscription, location, parameters, *, fresh=False):
        assert fresh is True and location == "eastus"
        assert db.list_deployments() == [] and not scheduled
        return {"checkedAt": 123.0, "capacity": {"status": capacity_status},
                "price": {"complete": True, "currency": "USD", "totalMonthly": 12, "pricingSource": "retail"}, "priceDetail": ""}
    monkeypatch.setattr(api.az, "schedule", schedule)
    monkeypatch.setattr(api.setup_cloud, "subscription_locations", regions)
    monkeypatch.setattr(api.catalog_processing, "check_region", check)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "User", TENANT)
    response = TestClient(application).post("/api/deployments", json={"useCase": "example", "subscriptionId": SUBSCRIPTION, "location": "eastus", "engine": engine})
    if capacity_status == "unavailable":
        assert response.status_code == 409
        assert not scheduled and db.list_deployments() == []
    else:
        assert response.status_code == 200
        assert scheduled == [True]
        assert response.json()["deployment"]["preflight"]["capacityStatus"] == capacity_status
        assert db.list_deployments()[0]["params"]["preflight"]["price"]["totalMonthly"] == 12
        assert db.list_deployments()[0]["engine"] == engine


@pytest.mark.parametrize("available, requested, detail", [
    ("terraform", "bicep", "this use case has no Bicep template"),
    ("bicep", "terraform", "this use case has no Terraform module"),
])
def test_single_format_deployment_rejects_unavailable_engine_before_cloud_calls(configured, monkeypatch, available, requested, detail):
    import api
    from identity import require_user
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    db.set_setup_setting("subscriptions", [{"subscriptionId": SUBSCRIPTION, "pricingMode": "retail"}])
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": {"path": "example", "parameters": [], "engines": [available]}})
    def unexpected(*args, **kwargs):
        pytest.fail("Unavailable engines must be rejected before cloud calls or scheduling")
    monkeypatch.setattr(api.az, "schedule", unexpected)
    monkeypatch.setattr(api.setup_cloud, "subscription_locations", unexpected)
    monkeypatch.setattr(api.catalog_processing, "check_region", unexpected)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "User", TENANT)
    response = TestClient(application).post("/api/deployments", json={"useCase": "example", "subscriptionId": SUBSCRIPTION, "location": "eastus", "engine": requested})
    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert db.list_deployments() == []


def test_multiple_subscription_selection_reaches_deployment_record(configured, monkeypatch):
    import api
    from identity import require_user
    second = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    items = [setup_config.Subscription(subscriptionId=identifier, tenantId=TENANT, displayName=identifier) for identifier in (SUBSCRIPTION, second)]
    db.set_setup_setting("subscriptions", setup_config.validate_subscriptions(items))
    monkeypatch.setattr(api, "_catalog_items", lambda: {"organization": {"path": "organization", "parameters": [], "engines": ["bicep"]}})
    monkeypatch.setattr(api.az, "schedule", lambda coroutine: coroutine.close())
    async def regions(identifier):
        return [{"name": "eastus"}]
    async def check(item, subscription, location, parameters, *, fresh=False):
        assert fresh and subscription["subscriptionId"] == second
        assert db.list_deployments() == []
        return {"checkedAt": 123.0, "capacity": {"status": "unknown"}, "price": None, "priceDetail": "No cost model"}
    monkeypatch.setattr(api.setup_cloud, "subscription_locations", regions)
    monkeypatch.setattr(api.catalog_processing, "check_region", check)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "User", TENANT)
    client = TestClient(application)
    result = client.post("/api/deployments", json={"useCase": "organization", "subscriptionId": second, "lifetimeHours": 1, "location": "eastus"})
    assert result.status_code == 200
    identifier = result.json()["deployment"]["id"]
    assert db.get_deployment(identifier)["subscription_id"] == second
    assert client.post("/api/deployments", json={"useCase": "organization", "subscriptionId": "44444444-4444-4444-8444-444444444444"}).status_code == 403