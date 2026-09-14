"""Hosted setup validation and remote integration tests."""

from __future__ import annotations

import sys
import asyncio
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
    body = {"clientId": "customer-oauth-client", "confirm": True}
    assert client.put(path, json=body).status_code == 403
    headers = {"Origin": "http://testserver"}
    assert client.put(path, json={**body, "confirm": False}, headers=headers).status_code == 400
    response = client.put(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert github_device.client_id() == "customer-oauth-client"
    db.set_setup_setting("github", {"enabled": True})
    assert client.put(path, json={**body, "clientId": "another-client"}, headers=headers).status_code == 409
    assert github_device.client_id() == "customer-oauth-client"


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


def test_github_requires_confirmation_and_no_existing_repo_seed(configured):
    import setup_github
    with pytest.raises(ValueError, match="confirmation"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="customer", repository="portal")))
    with pytest.raises(ValueError, match="newly created"):
        asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="customer", repository="portal", seed=True, confirm=True)))


def test_github_sync_publishes_metadata_without_executing_source(configured, monkeypatch):
    import base64
    import json
    import setup_github
    db.set_setup_setting("github", {"enabled": True, "owner": "customer", "repository": "portal", "branch": "main"})
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
        return {"content": base64.b64encode(json.dumps({"displayName": "Customer example"}).encode()).decode()}
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.sync_catalog())
    assert result["items"][0]["displayName"] == "Customer example"
    assert result["items"][0]["deployReady"] is False
    assert db.get_setup_setting("catalog_snapshot")["version"] == "a" * 40


def test_auth_provider_preserves_bootstrap(configured):
    import setup_auth
    settings = setup_auth.AuthenticationSettings(kind="oidc", providerName="customer", clientId="app", issuer="https://id.example.com", discoveryUrl="https://id.example.com/.well-known/openid-configuration", publicUrl="https://portal.example.com")
    current = {"properties": {"platform": {"enabled": True}, "globalValidation": {"requireAuthentication": True, "redirectToProvider": "azureactivedirectory"}, "identityProviders": {"azureActiveDirectory": {"enabled": True, "registration": {"clientId": "bootstrap"}}}}}
    result = setup_auth.provider_document(current, settings)
    assert result["properties"]["identityProviders"]["azureActiveDirectory"] == current["properties"]["identityProviders"]["azureActiveDirectory"]
    assert result["properties"]["globalValidation"]["redirectToProvider"] == "azureactivedirectory"
    assert "customOpenIdConnectProviders" not in current["properties"]["identityProviders"]
    assert "clientCredential" not in result["properties"]["identityProviders"]["customOpenIdConnectProviders"]["customer"]["registration"]


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
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "customer")
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    db.set_setup_setting("authentication", {"providerName": "customer", "issuer": "https://id.example.com", "clientId": "app"})
    principal = {"auth_typ": "customer", "claims": [{"typ": "iss", "val": "https://id.example.com"}, {"typ": "aud", "val": "app"}, {"typ": "sub", "val": "123"}, {"typ": "email", "val": "admin@example.com"}, {"typ": "oid", "val": "entra-admin"}]}
    request = Request({"type": "http", "headers": [(b"x-ms-client-principal", base64.b64encode(json.dumps(principal).encode()))]})
    identity = entra_auth.identity_from_easy_auth(request)
    assert identity.oid.startswith("oidc:")
    assert db.upsert_user(identity.oid, identity.upn, identity.name) == "guest"
    db.set_user_role(identity.upn, "admin")
    assert db.get_role(identity.oid) == "guest"
    db.set_user_role_by_oid(identity.oid, "admin")
    assert db.get_role(identity.oid) == "admin"


def test_github_seed_initializes_license_before_bulk_commit(configured, monkeypatch):
    import base64
    import setup_github
    files = {"LICENSE": base64.b64encode(b"MIT").decode(), "site/main.py": base64.b64encode(b"app").decode()}
    monkeypatch.setattr(setup_github, "source_seed", lambda: files)
    calls = []
    async def request(path, method="GET", body=None):
        calls.append((path, method, body))
        if path == "/user": return {"login": "customer"}
        if path == "/user/repos": return {"full_name": "customer/portal"}
        if "/contents/" in path: return {"commit": {"sha": "initial"}}
        if path.endswith("/commits"): return {"sha": "seeded"}
        return {"sha": "object"}
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.configure(setup_github.GitHubSettings(owner="customer", repository="portal", create=True, seed=True, confirm=True)))
    assert result["seeded"]
    assert calls[1][2]["private"] is True
    assert calls[2][0].endswith("/contents/LICENSE")
    assert calls[-1][1:] == ("PATCH", {"sha": "seeded", "force": False})
    assert next(body for path, method, body in calls if path.endswith("/commits"))["parents"] == ["initial"]


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
    assert writes[0]["properties"] == {"EXISTING": "preserve", "PORTAL_CUSTOMER_AUTH_SECRET": expected}
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
        return httpx.Response(200, json={"id": uri + "/version1", "value": "secret-not-for-browser", "tags": {"owner": "customer"}, "attributes": {"enabled": True}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(setup_auth.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    class Credential:
        def get_token(self, scope):
            assert scope == "https://vault.azure.net/.default"
            return type("Token", (), {"token": "synthetic"})()
    monkeypatch.setattr(setup_auth.azure_deploy, "_get_credential", lambda: Credential())
    result = asyncio.run(setup_auth.key_vault_secret(uri))
    assert result == {"id": uri + "/version1", "tags": {"owner": "customer"}}


def test_no_secret_oidc_does_not_read_secret_settings(configured, monkeypatch):
    import setup_auth
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-portal/providers/Microsoft.Web/sites/portal")
    async def arm(path, method="GET", body=None, **kwargs):
        assert "/appsettings" not in path
        if method == "PUT":
            registration = body["properties"]["identityProviders"]["customOpenIdConnectProviders"]["customer"]["registration"]
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
    db.set_setup_setting("github", {"enabled": True, "owner": "customer", "repository": "portal", "branch": "main"})
    async def fail(*args): raise ValueError("network or invalid catalog")
    monkeypatch.setattr(setup_github, "github_request", fail)
    with pytest.raises(ValueError): asyncio.run(setup_github.sync_catalog())
    assert setup_github.catalog_snapshot() == previous


def test_multiple_subscription_selection_reaches_deployment_record(configured, monkeypatch):
    import api
    from identity import require_user
    second = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "customer")
    items = [setup_config.Subscription(subscriptionId=identifier, tenantId=TENANT, displayName=identifier) for identifier in (SUBSCRIPTION, second)]
    db.set_setup_setting("subscriptions", setup_config.validate_subscriptions(items))
    monkeypatch.setattr(api, "_catalog_items", lambda: {"customer": {"path": "customer", "parameters": [], "engines": ["bicep"]}})
    monkeypatch.setattr(api.az, "schedule", lambda coroutine: coroutine.close())
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "User", TENANT)
    client = TestClient(application)
    result = client.post("/api/deployments", json={"useCase": "customer", "subscriptionId": second, "lifetimeHours": 1})
    assert result.status_code == 200
    identifier = result.json()["deployment"]["id"]
    assert db.get_deployment(identifier)["subscription_id"] == second
    assert client.post("/api/deployments", json={"useCase": "customer", "subscriptionId": "44444444-4444-4444-8444-444444444444"}).status_code == 403