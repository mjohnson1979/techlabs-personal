"""Offline first-run ownership, credential and activation checks."""

import base64
import asyncio
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app
import db
import installer
import container_start

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
OWNER = "33333333-3333-4333-8333-333333333333"
HOST = f"/subscriptions/{TENANT}/resourceGroups/test/providers/Microsoft.Web/sites/portal"
CODE = "synthetic-installation-code-for-offline-tests"
ORIGIN = "https://portal.example.com"


@pytest.fixture
def installation(monkeypatch):
    for key, value in {
        "GALLERY_DISTRIBUTION": "organization", "GALLERY_INSTALLER_ENABLED": "true",
        "GALLERY_SETUP_CODE_SHA256": hashlib.sha256(CODE.encode()).hexdigest(),
        "GALLERY_SETUP_EXPIRES_AT": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "GALLERY_PUBLIC_URL": ORIGIN, "GALLERY_HOST_RESOURCE_ID": HOST,
        "GALLERY_EASY_AUTH": "true", "GALLERY_HTTPS": "true", "AZURE_CLIENT_ID": CLIENT,
        "GALLERY_MANAGED_IDENTITY": "user",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID", "GALLERY_ADMIN_OIDS"):
        monkeypatch.delenv(key, raising=False)
    db.init_db()
    cloud = {"auth": {"properties": {"platform": {"enabled": True}}}, "settings": {"properties": {}}}

    async def arm(path, *, method="GET", body=None, etag=None):
        if "/config/authsettingsV2?" in path:
            key = "auth"
        elif "/config/appsettings" in path:
            key = "settings"
        elif path.startswith(HOST + "?"):
            return {"id": HOST, "properties": {"hostNames": ["portal.example.com"]}}
        elif path.startswith(f"/subscriptions/{TENANT}?"):
            return {"subscriptionId": TENANT, "tenantId": TENANT}
        else:
            raise AssertionError(path)
        if method == "PUT":
            cloud[key] = copy.deepcopy(body)
        return copy.deepcopy(cloud[key])

    monkeypatch.setattr(installer.setup_cloud, "arm_request", arm)
    client = TestClient(app.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    yield client, cloud
    client.close()


def unlock(client):
    response = client.post("/api/install/unlock", json={"code": CODE})
    assert response.status_code == 200, response.text
    return response


def connection():
    return {"tenantId": TENANT, "clientId": CLIENT, "secretStorage": "app_service", "clientSecret": "synthetic-client-credential"}


def principal(**overrides):
    claims = {"tid": TENANT, "oid": OWNER, "aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", **overrides}
    return {"x-ms-client-principal": base64.b64encode(json.dumps({"auth_typ": "aad", "claims": [{"typ": key, "val": value} for key, value in claims.items()]}).encode()).decode()}


def test_installer_locks_normal_routes_even_with_identity(installation):
    client, _ = installation
    for path in ("/api/me", "/api/admin/setup", "/assets/app.js", "/server/installer.py"):
        assert client.get(path, headers=principal()).status_code == 503
    assert client.get("/", follow_redirects=False).headers["location"] == "/install"
    assert client.post("/api/install/entra", json=connection(), headers=principal()).status_code == 401
    assert client.get("/install").status_code == 200
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/assets/install.js").status_code == 200


def test_unlock_is_origin_bound_single_use_and_secret_safe(installation):
    client, _ = installation
    assert client.post("/api/install/unlock", headers={"Origin": "https://other.example"}, json={"code": CODE}).status_code == 403
    response = unlock(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert CODE not in response.text
    assert CODE not in json.dumps(db.get_setup_setting("installation_session"))
    assert client.post("/api/install/unlock", json={"code": CODE}).status_code == 409
    assert client.get("/api/install/status").status_code == 200
    client.cookies.clear()
    assert client.get("/api/install/status").status_code == 401


def test_unlock_expiry_rotation_and_rate_limit(installation, monkeypatch):
    client, _ = installation
    for _ in range(10):
        assert client.post("/api/install/unlock", json={"code": "x" * 32}).status_code == 401
    assert client.post("/api/install/unlock", json={"code": CODE}).status_code == 429
    db.set_setup_setting("installation_attempts", [])
    unlock(client)
    monkeypatch.setenv("GALLERY_SETUP_CODE_SHA256", hashlib.sha256(b"rotated").hexdigest())
    assert client.get("/api/install/status").status_code == 401
    monkeypatch.setenv("GALLERY_SETUP_EXPIRES_AT", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert client.post("/api/install/unlock", json={"code": CODE}).status_code == 410


def test_primary_entra_activation_requires_cookie_and_matching_signin(installation):
    client, cloud = installation
    unlock(client)
    response = client.post("/api/install/entra", json=connection())
    assert response.status_code == 200, response.text
    assert "synthetic-client-credential" not in response.text
    assert "clientSecret" not in db.get_setup_setting("installation_pending")
    assert cloud["settings"]["properties"]["PORTAL_ENTRA_CLIENT_SECRET"] == "synthetic-client-credential"
    assert client.get("/api/me", headers=principal()).status_code == 503
    assert client.post("/api/install/finish").status_code == 403
    assert client.post("/api/install/finish", headers=principal(aud=TENANT)).status_code == 403
    response = client.post("/api/install/finish", headers=principal())
    assert response.status_code == 200, response.text
    assert cloud["auth"]["properties"]["globalValidation"]["requireAuthentication"] is True
    values = cloud["settings"]["properties"]
    assert values["GALLERY_ADMIN_OIDS"] == OWNER
    assert values["GALLERY_INSTALLER_ENABLED"] == "false"
    assert "GALLERY_SETUP_CODE_SHA256" not in values
    assert db.get_setup_setting("installation_complete") is True
    assert client.post("/api/install/unlock", json={"code": CODE}).status_code == 410


@pytest.mark.parametrize("change", [{"tenantId": CLIENT}, {"clientId": "not-a-guid"}, {"secretStorage": "none"}])
def test_invalid_connection_cannot_modify_cloud(installation, change):
    client, cloud = installation
    unlock(client)
    before = copy.deepcopy(cloud)
    response = client.post("/api/install/entra", json={**connection(), **change})
    assert response.status_code in (400, 422)
    assert "synthetic-client-credential" not in response.text
    assert cloud == before


def test_oversized_secret_and_existing_provider_are_rejected(installation):
    client, cloud = installation
    unlock(client)
    assert client.post("/api/install/entra", json={**connection(), "clientSecret": "x" * 17000}).status_code == 413
    cloud["auth"]["properties"]["identityProviders"] = {"azureActiveDirectory": {"registration": {"clientId": CLIENT}}}
    assert client.post("/api/install/entra", json=connection()).status_code == 400


def test_existing_non_entra_provider_is_not_replaced(installation):
    client, cloud = installation
    unlock(client)
    cloud["auth"]["properties"]["identityProviders"] = {"twitter": {"registration": {"consumerKey": "synthetic-existing-client"}}}
    before = copy.deepcopy(cloud)
    assert client.post("/api/install/entra", json=connection()).status_code == 400
    assert cloud == before


def test_cloud_failure_never_completes_installation(installation, monkeypatch):
    client, _ = installation
    unlock(client)

    async def failed(*args, **kwargs):
        raise httpx.ConnectError("must-not-echo-this")

    monkeypatch.setattr(installer.setup_cloud, "arm_request", failed)
    response = client.post("/api/install/entra", json=connection())
    assert response.status_code == 502
    assert "must-not-echo-this" not in response.text
    assert not db.get_setup_setting("installation_complete")


def test_setup_session_survives_restart_without_storing_plaintext_cookie(installation):
    client, _ = installation
    unlock(client)
    raw_cookie = client.cookies.get(installer.COOKIE_NAME)
    assert raw_cookie not in json.dumps(db.get_setup_setting("installation_session"))
    db._CONN.close()
    db._CONN = None
    db.init_db()
    assert client.get("/api/install/status").status_code == 200


def test_missing_cookie_or_changed_provider_cannot_finish(installation):
    client, cloud = installation
    unlock(client)
    assert client.post("/api/install/entra", json=connection()).status_code == 200
    cloud["auth"]["properties"]["identityProviders"]["azureActiveDirectory"]["registration"]["clientId"] = OWNER
    assert client.post("/api/install/finish", headers=principal()).status_code == 400
    client.cookies.clear()
    assert client.post("/api/install/finish", headers=principal()).status_code == 401
    assert not db.get_setup_setting("installation_complete")


def test_disabled_and_completed_installation_cannot_be_reopened(installation, monkeypatch):
    client, _ = installation
    monkeypatch.setenv("GALLERY_INSTALLER_ENABLED", "false")
    assert client.get("/api/install/status").status_code == 401
    monkeypatch.setenv("GALLERY_INSTALLER_ENABLED", "true")
    db.set_setup_setting("installation_complete", True)
    assert client.get("/install").status_code == 410
    assert client.post("/api/install/unlock", json={"code": CODE}).status_code == 410


def test_existing_credential_is_reused_after_interrupted_configuration(installation, monkeypatch):
    client, cloud = installation
    unlock(client)
    original = installer.setup_cloud.arm_request

    async def interrupted(path, **kwargs):
        if "/config/authsettingsV2?" in path and kwargs.get("method") == "PUT":
            raise httpx.ConnectError("interrupted")
        return await original(path, **kwargs)

    monkeypatch.setattr(installer.setup_cloud, "arm_request", interrupted)
    assert client.post("/api/install/entra", json=connection()).status_code == 502
    assert cloud["settings"]["properties"]["PORTAL_ENTRA_CLIENT_SECRET"]
    monkeypatch.setattr(installer.setup_cloud, "arm_request", original)
    response = client.post("/api/install/entra", json={**connection(), "clientSecret": ""})
    assert response.status_code == 200, response.text


def test_key_vault_storage_sends_only_reference_to_host(installation, monkeypatch):
    client, cloud = installation
    unlock(client)
    stored = []

    async def target(settings, host, *, tenant_id=None):
        assert tenant_id == TENANT
        return "https://test-vault.vault.azure.net/secrets/entra"

    async def secret(uri, *, value=None, tags=None):
        if value is None:
            return None
        stored.append(value)
        return {"id": uri + "/version1"}

    monkeypatch.setattr(installer.setup_auth, "key_vault_target", target)
    monkeypatch.setattr(installer.setup_auth, "key_vault_secret", secret)
    response = client.post("/api/install/entra", json={
        **connection(), "secretStorage": "key_vault", "keyVaultSecretName": "entra",
        "keyVaultResourceId": f"/subscriptions/{TENANT}/resourceGroups/test/providers/Microsoft.KeyVault/vaults/test-vault",
    })
    assert response.status_code == 200, response.text
    assert stored == ["synthetic-client-credential"]
    assert "synthetic-client-credential" not in json.dumps(cloud)
    assert cloud["settings"]["properties"]["PORTAL_ENTRA_CLIENT_SECRET"].startswith("@Microsoft.KeyVault(")


def test_interrupted_activation_can_only_finish_as_verified_owner(installation, monkeypatch):
    client, _ = installation
    unlock(client)
    assert client.post("/api/install/entra", json=connection()).status_code == 200
    original = installer.setup_cloud.arm_request

    async def interrupted(path, **kwargs):
        if "/config/appsettings?" in path and kwargs.get("method") == "PUT":
            raise httpx.ConnectError("interrupted")
        return await original(path, **kwargs)

    monkeypatch.setattr(installer.setup_cloud, "arm_request", interrupted)
    assert client.post("/api/install/finish", headers=principal()).status_code == 502
    assert not db.get_setup_setting("installation_complete")
    monkeypatch.setattr(installer.setup_cloud, "arm_request", original)
    assert client.post("/api/install/finish", headers=principal(oid=CLIENT)).status_code == 403
    assert client.post("/api/install/finish", headers=principal()).status_code == 200


def test_container_derives_host_settings_and_keeps_original_expiry(installation, monkeypatch):
    for name in ("GALLERY_HOST_RESOURCE_ID", "GALLERY_PUBLIC_URL", "GALLERY_SETUP_CODE_SHA256", "GALLERY_SETUP_EXPIRES_AT", "GALLERY_INSTALLER_ENABLED", "AZURE_CLIENT_ID"):
        monkeypatch.delenv(name)
    for name, value in {
        "WEBSITE_HOSTNAME": "portal.azurewebsites.net", "WEBSITE_SITE_NAME": "portal",
        "WEBSITE_RESOURCE_GROUP": "test", "WEBSITE_OWNER_NAME": f"{TENANT}+test-CentralUSwebspace",
        "GALLERY_SETUP_SECRET": CODE,
        "GALLERY_INSTALLER_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GALLERY_MANAGED_IDENTITY", "system")
    container_start.prepare_configuration()
    assert os.environ["GALLERY_HOST_RESOURCE_ID"] == HOST
    assert os.environ["GALLERY_PUBLIC_URL"] == "https://portal.azurewebsites.net"
    assert os.environ["GALLERY_INSTALLER_ENABLED"] == "true"
    assert "GALLERY_SETUP_SECRET" not in os.environ
    bootstrap = db.get_setup_setting("installation_bootstrap")
    assert CODE not in json.dumps(bootstrap)
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    container_start.prepare_configuration()
    assert db.get_setup_setting("installation_bootstrap") == bootstrap
    bootstrap["expiresAt"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.set_setup_setting("installation_bootstrap", bootstrap)
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    container_start.prepare_configuration()
    assert installer.distribution.installer_configuration_errors()


def test_container_completed_installation_ignores_remaining_secret(installation, monkeypatch):
    db.set_setup_setting("installation_complete", True)
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    container_start.prepare_configuration()
    assert os.environ["GALLERY_INSTALLER_ENABLED"] == "false"
    assert not db.get_setup_setting("installation_bootstrap")


def test_container_rejects_untrusted_host_metadata(installation, monkeypatch):
    monkeypatch.delenv("GALLERY_HOST_RESOURCE_ID")
    monkeypatch.setenv("WEBSITE_OWNER_NAME", "missing-subscription")
    with pytest.raises(ValueError, match="subscription metadata"):
        container_start.prepare_configuration()


def test_container_rotated_secrets_cannot_be_reused(installation, monkeypatch):
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    container_start.prepare_configuration()
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE + "-rotated")
    container_start.prepare_configuration()
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    with pytest.raises(ValueError, match="already used"):
        container_start.prepare_configuration()


@pytest.mark.parametrize("failure", [None, "https", "easy_auth", "storage", "identity", "redirect"])
def test_container_verifies_authenticated_host_before_listening(installation, monkeypatch, failure):
    monkeypatch.setenv("GALLERY_MANAGED_IDENTITY", "system")
    monkeypatch.delenv("AZURE_CLIENT_ID")
    monkeypatch.setenv("WEBSITES_ENABLE_APP_SERVICE_STORAGE", "false" if failure == "storage" else "true")

    async def arm(path, **kwargs):
        if "/config/authsettingsV2?" in path:
            return {"properties": {
                "platform": {"enabled": failure != "easy_auth"}, "httpSettings": {"requireHttps": True},
                "globalValidation": {"requireAuthentication": failure == "redirect", "unauthenticatedClientAction": "AllowAnonymous"},
            }}
        return {
            "id": HOST, "identity": {"type": "SystemAssigned", "principalId": "" if failure == "identity" else OWNER},
            "properties": {"httpsOnly": failure != "https", "publicNetworkAccess": "Enabled", "hostNames": ["portal.example.com"]},
        }

    monkeypatch.setattr(container_start.setup_cloud, "arm_request", arm)
    if failure:
        with pytest.raises(ValueError):
            asyncio.run(container_start.verify_host())
    else:
        installer.distribution.validate_customer_configuration()
        asyncio.run(container_start.verify_host())


def test_system_identity_never_falls_back_to_developer_credentials(installation, monkeypatch):
    import azure.identity
    import azure_deploy
    monkeypatch.setattr(azure_deploy, "_credential", None)
    monkeypatch.setenv("GALLERY_MANAGED_IDENTITY", "system")
    monkeypatch.delenv("AZURE_CLIENT_ID")
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", lambda **kwargs: {"managed": kwargs})
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda **kwargs: pytest.fail("Developer credential fallback"))
    assert azure_deploy._get_credential() == {"managed": {}}
    monkeypatch.setattr(azure_deploy, "_credential", None)
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT)
    with pytest.raises(RuntimeError, match="conflicting"):
        azure_deploy._get_credential()


def test_container_defaults_to_signin_even_with_leftover_setup_secret(installation, monkeypatch):
    monkeypatch.delenv("GALLERY_INSTALLER_ENABLED")
    monkeypatch.setenv("GALLERY_SETUP_SECRET", CODE)
    container_start.prepare_configuration()
    assert os.environ["GALLERY_INSTALLER_ENABLED"] == "false"
    assert "GALLERY_SETUP_SECRET" not in os.environ
    assert not db.get_setup_setting("installation_bootstrap")


@pytest.fixture
def signin_host(installation, monkeypatch):
    monkeypatch.setenv("GALLERY_INSTALLER_ENABLED", "false")
    monkeypatch.setenv("GALLERY_ADMIN_OIDS", OWNER)
    monkeypatch.setenv("GALLERY_MANAGED_IDENTITY", "system")
    monkeypatch.delenv("AZURE_CLIENT_ID")
    monkeypatch.setenv("WEBSITES_ENABLE_APP_SERVICE_STORAGE", "true")
    for name in ("GALLERY_SETUP_SECRET", "GALLERY_SETUP_CODE_SHA256", "GALLERY_SETUP_EXPIRES_AT"):
        monkeypatch.delenv(name, raising=False)
    cloud = {
        "host": {"id": HOST, "identity": {"type": "SystemAssigned", "principalId": OWNER}, "properties": {
            "httpsOnly": True, "publicNetworkAccess": "Disabled", "hostNames": ["portal.example.com"],
        }},
        "auth": {"properties": {
            "platform": {"enabled": True}, "httpSettings": {"requireHttps": True},
            "globalValidation": {"requireAuthentication": True, "excludedPaths": [], "unauthenticatedClientAction": "RedirectToLoginPage"},
            "identityProviders": {"azureActiveDirectory": {
                "enabled": True, "registration": {"clientId": CLIENT, "openIdIssuer": f"https://login.microsoftonline.com/{TENANT}/v2.0"},
                "validation": {"allowedAudiences": [CLIENT]},
            }},
        }},
        "subscription": {"subscriptionId": TENANT, "tenantId": TENANT},
    }

    async def arm(path, *, method="GET", **kwargs):
        assert method == "GET", "Sign-in-first startup must not modify Azure"
        if path == HOST + "?api-version=2024-11-01":
            return copy.deepcopy(cloud["host"])
        if path == HOST + "/config/authsettingsV2?api-version=2024-11-01":
            return copy.deepcopy(cloud["auth"])
        if path == f"/subscriptions/{TENANT}?api-version=2022-12-01":
            return copy.deepcopy(cloud["subscription"])
        raise AssertionError(path)

    monkeypatch.setattr(container_start.setup_cloud, "arm_request", arm)
    return cloud


@pytest.mark.parametrize("network_access", ["Enabled", "Disabled", None])
def test_signin_startup_discovers_customer_entra_without_setup_code(signin_host, network_access):
    signin_host["host"]["properties"]["publicNetworkAccess"] = network_access
    container_start.prepare_configuration()
    asyncio.run(container_start.verify_host())
    installer.distribution.validate_customer_configuration()
    assert os.environ["GALLERY_ENTRA_TENANT_ID"] == TENANT
    assert os.environ["GALLERY_ENTRA_CLIENT_ID"] == CLIENT
    assert os.environ["GALLERY_INSTALLER_ENABLED"] == "false"
    assert not db.get_setup_setting("installation_bootstrap")


@pytest.mark.parametrize("failure", ["disabled", "anonymous", "excluded", "common", "foreign_tenant", "audience", "client_mismatch", "tenant_mismatch"])
def test_signin_startup_rejects_untrusted_configuration(signin_host, monkeypatch, failure):
    properties = signin_host["auth"]["properties"]
    provider = properties["identityProviders"]["azureActiveDirectory"]
    if failure == "disabled":
        provider["enabled"] = False
    elif failure == "anonymous":
        properties["globalValidation"]["requireAuthentication"] = False
    elif failure == "excluded":
        properties["globalValidation"]["excludedPaths"] = ["/install"]
    elif failure == "common":
        provider["registration"]["openIdIssuer"] = "https://login.microsoftonline.com/common/v2.0"
    elif failure == "foreign_tenant":
        signin_host["subscription"]["tenantId"] = OWNER
    elif failure == "audience":
        provider["validation"]["allowedAudiences"].append(OWNER)
    elif failure == "client_mismatch":
        monkeypatch.setenv("GALLERY_ENTRA_CLIENT_ID", OWNER)
    else:
        monkeypatch.setenv("GALLERY_ENTRA_TENANT_ID", OWNER)
    with pytest.raises(ValueError):
        asyncio.run(container_start.verify_host())


def test_signin_startup_still_requires_designated_admin(signin_host, monkeypatch):
    monkeypatch.delenv("GALLERY_ADMIN_OIDS")
    asyncio.run(container_start.verify_host())
    with pytest.raises(ValueError, match="GALLERY_ADMIN_OIDS"):
        installer.distribution.validate_customer_configuration()


def test_signin_setup_requires_designated_admin_not_first_tenant_member(signin_host):
    asyncio.run(container_start.verify_host())
    installer.distribution.validate_customer_configuration()
    with TestClient(app.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert client.get("/api/admin/setup").status_code == 401
        assert client.get("/api/admin/setup", headers=principal(tid=CLIENT)).status_code == 401
        assert db.list_users() == []
        member = principal(oid=CLIENT)
        assert client.get("/api/admin/setup", headers=member).status_code == 403
        assert db.get_role(CLIENT) == "guest"
        assert client.get("/api/admin/setup", headers=principal()).status_code == 200
        profile = client.get("/api/me", headers=principal()).json()
        assert profile["isAdmin"] is True and profile["setupRequired"] is True
        assert db.get_role(OWNER) == "admin"
        assert client.get("/api/admin/setup", headers=member).status_code == 403
        assert client.post("/api/install/unlock", json={"code": CODE}, headers=principal()).status_code == 410
        assert not db.get_setup_setting("installation_bootstrap")


def test_signin_startup_accepts_implicit_client_audience(signin_host):
    signin_host["auth"]["properties"]["identityProviders"]["azureActiveDirectory"]["validation"]["allowedAudiences"] = []
    asyncio.run(container_start.verify_host())
    assert os.environ["GALLERY_ENTRA_CLIENT_ID"] == CLIENT


def test_signin_entrypoint_discovers_entra_before_configuration_validation(signin_host, monkeypatch):
    started = []
    monkeypatch.setattr(container_start.os, "execvp", lambda program, args: started.append((program, args)))
    assert container_start.main() == 0
    assert started[0][0] == "gunicorn"
    assert os.environ["GALLERY_ENTRA_TENANT_ID"] == TENANT
    assert os.environ["GALLERY_INSTALLER_ENABLED"] == "false"