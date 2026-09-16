"""Add organization identity providers without replacing bootstrap authentication."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

import azure_deploy
import db
from distribution import uses_system_identity
import setup_cloud
from setup_config import canonical_guid


def public_https(value: str) -> str:
    value = value.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.port not in (None, 443):
        raise ValueError("Use an HTTPS public URL without credentials, query or fragment")
    hostname = parsed.hostname.lower()
    if "." not in hostname or hostname.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Use a public identity-provider hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return value
    raise ValueError("IP address identity providers are not supported")


class SecretStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secretStorage: Literal["none", "app_service", "key_vault"] = "none"
    secretSettingName: str = Field(default="", pattern=r"^[A-Z0-9_]*$", max_length=100)
    keyVaultResourceId: str = Field(default="", max_length=500)
    keyVaultSecretName: str = Field(default="", pattern=r"^[A-Za-z0-9-]*$", max_length=127)

    @model_validator(mode="after")
    def storage_fields(self) -> "SecretStorage":
        if self.secretStorage == "key_vault":
            if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.KeyVault/vaults/[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]", self.keyVaultResourceId):
                raise ValueError("Provide the existing organization Key Vault resource ID")
            if not self.keyVaultSecretName:
                raise ValueError("Provide a Key Vault secret name")
        elif self.keyVaultResourceId or self.keyVaultSecretName:
            raise ValueError("Key Vault fields require Key Vault storage")
        if self.secretStorage == "none" and self.secretSettingName:
            raise ValueError("No-secret authentication cannot specify a secret setting")
        return self


class AuthenticationSettings(SecretStorage):
    kind: Literal["entra", "oidc"] = "entra"
    providerName: str = Field(default="organization", pattern=r"^[a-z][a-z0-9_]{1,29}$")
    clientId: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=400)
    discoveryUrl: str = Field(min_length=1, max_length=500)
    publicUrl: str = Field(min_length=1, max_length=300)
    confirm: bool = False

    @field_validator("issuer", "discoveryUrl", "publicUrl")
    @classmethod
    def url(cls, value: str) -> str:
        return public_https(value)


class EntraRegistration(SecretStorage):
    secretStorage: Literal["app_service", "key_vault"] = "app_service"
    displayName: str = Field(min_length=1, max_length=100)
    publicUrl: str
    providerName: str = Field(default="organization", pattern=r"^[a-z][a-z0-9_]{1,29}$")
    confirm: bool = False

    @field_validator("publicUrl")
    @classmethod
    def url(cls, value: str) -> str:
        return public_https(value)


class GitHubCredential(SecretStorage):
    secretStorage: Literal["app_service", "key_vault"] = "app_service"
    secretSettingName: Literal["GALLERY_GITHUB_TOKEN"] = "GALLERY_GITHUB_TOKEN"
    confirm: bool = False


def auth_path() -> str:
    resource = os.environ.get("GALLERY_HOST_RESOURCE_ID", "").strip()
    if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.Web/sites/[A-Za-z0-9-]+", resource):
        raise ValueError("Set GALLERY_HOST_RESOURCE_ID to this App Service resource ID")
    return resource + "/config/authsettingsV2?api-version=2024-11-01"


async def key_vault_target(settings: SecretStorage, host: dict, *, tenant_id: str | None = None) -> str:
    reference_identity = str(host.get("properties", {}).get("keyVaultReferenceIdentity", ""))
    identities = host.get("identity", {}).get("userAssignedIdentities", {})
    identity = next((value for key, value in identities.items() if key.lower() == reference_identity.lower()), {})
    system_reference = (
        uses_system_identity() and reference_identity.lower() in ("", "none", "systemassigned")
        and bool(host.get("identity", {}).get("principalId"))
    )
    user_reference = (
        not uses_system_identity() and bool(identity.get("clientId"))
        and identity["clientId"].lower() == os.environ.get("AZURE_CLIENT_ID", "").strip().lower()
    )
    if not system_reference and not user_reference:
        raise ValueError("Configure this host's Key Vault reference identity to the attached setup managed identity first; setup will not change other references")
    vault = await setup_cloud.arm_request(settings.keyVaultResourceId + "?api-version=2023-07-01")
    if str(vault.get("id", "")).lower() != settings.keyVaultResourceId.lower():
        raise ValueError("Key Vault resource identity could not be verified")
    expected_tenant = tenant_id if tenant_id is not None else os.environ.get("GALLERY_ENTRA_TENANT_ID", "").strip()
    if str(vault.get("properties", {}).get("tenantId", "")).lower() != expected_tenant.lower():
        raise ValueError("Key Vault must belong to the organization Azure tenant")
    vault_name = settings.keyVaultResourceId.rsplit("/", 1)[-1].lower()
    uri = str(vault.get("properties", {}).get("vaultUri", "")).rstrip("/").lower()
    if uri != f"https://{vault_name}.vault.azure.net":
        raise ValueError("Unsupported Key Vault endpoint; Azure public-cloud vaults are required")
    return f"{uri}/secrets/{settings.keyVaultSecretName}"


async def key_vault_secret(uri: str, *, value: str | None = None, tags: dict[str, str] | None = None) -> dict | None:
    if not re.fullmatch(r"https://[a-z][a-z0-9-]{1,22}[a-z0-9]\.vault\.azure\.net/secrets/[A-Za-z0-9-]{1,127}", uri):
        raise ValueError("Invalid Key Vault secret destination")
    token = await asyncio.to_thread(azure_deploy._get_credential().get_token, "https://vault.azure.net/.default")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.request(
            "GET" if value is None else "PUT", uri + "?api-version=7.4",
            headers={"Authorization": f"Bearer {token.token}"},
            json=None if value is None else {"value": value, "tags": tags or {}},
        )
        if value is None and response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    identifier = str(data.get("id", ""))
    if not re.fullmatch(re.escape(uri) + r"/[a-zA-Z0-9]+", identifier):
        raise ValueError("Key Vault returned an unexpected secret reference")
    attributes = data.get("attributes", {})
    now = datetime.now(timezone.utc).timestamp()
    if attributes.get("enabled") is False or attributes.get("exp", now + 1) <= now or attributes.get("nbf", 0) > now:
        raise ValueError("The Key Vault secret is disabled, expired or not yet valid")
    return {"id": identifier, "tags": data.get("tags", {})}


async def bind_existing_secret(settings: SecretStorage, site_path: str, host: dict, *, tenant_id: str | None = None) -> None:
    setting_path = site_path + "/config/appsettings"
    app_settings = await setup_cloud.arm_request(setting_path + "/list?api-version=2024-11-01", method="POST")
    properties = app_settings.get("properties", {})
    existing_value = properties.get(settings.secretSettingName, "")
    if settings.secretStorage == "app_service":
        if not existing_value or str(existing_value).startswith("@Microsoft.KeyVault("):
            raise ValueError("Selected App Service storage requires an existing secret value, not a Key Vault reference")
        return
    if settings.secretStorage != "key_vault":
        raise ValueError("Select a credential storage destination")
    uri = await key_vault_target(settings, host, tenant_id=tenant_id)
    metadata = await key_vault_secret(uri)
    if metadata is None:
        raise ValueError("The selected Key Vault secret does not exist")
    reference = f"@Microsoft.KeyVault(SecretUri={metadata['id']})"
    if existing_value in (reference, f"@Microsoft.KeyVault(SecretUri={uri})"):
        return
    if existing_value:
        raise ValueError("The target app setting is already in use; setup does not overwrite or migrate existing credentials")
    properties[settings.secretSettingName] = reference
    await setup_cloud.arm_request(setting_path + "?api-version=2024-11-01", method="PUT", body={"properties": properties}, etag=app_settings.get("etag"))


async def configure_github_credential(settings: GitHubCredential) -> dict:
    if not settings.confirm:
        raise ValueError("Confirm the GitHub credential storage choice")
    site_path = auth_path().split("/config/", 1)[0]
    host = await setup_cloud.arm_request(site_path + "?api-version=2024-11-01")
    await bind_existing_secret(settings, site_path, host)
    saved = settings.model_dump(exclude={"confirm"})
    db.set_setup_setting("github_credential", saved)
    return saved


async def store_github_device_token(token: str, expires_at: float, account: dict, storage: dict | None = None) -> None:
    settings = GitHubCredential.model_validate(storage or db.get_setup_setting("github_credential", {"secretStorage": "app_service"}))
    site_path = auth_path().split("/config/", 1)[0]
    host = await setup_cloud.arm_request(site_path + "?api-version=2024-11-01")
    setting_path = site_path + "/config/appsettings"
    app_settings = await setup_cloud.arm_request(setting_path + "/list?api-version=2024-11-01", method="POST")
    properties = app_settings.get("properties", {})
    existing_value = properties.get("GALLERY_GITHUB_TOKEN", "")
    if settings.secretStorage == "key_vault":
        uri = await key_vault_target(settings, host)
        existing = await key_vault_secret(uri)
        allowed_values = ("", f"@Microsoft.KeyVault(SecretUri={uri})", f"@Microsoft.KeyVault(SecretUri={existing['id']})" if existing else "")
        if existing_value not in allowed_values:
            raise ValueError("The host token points to another store; reconcile credential storage before reconnecting")
        if existing and existing.get("tags", {}).get("portalPurpose") != "github-device-token":
            raise ValueError("Choose a dedicated Key Vault secret name for device sign-in; setup will not replace an unrelated secret")
        metadata = await key_vault_secret(uri, value=token, tags={"portalPurpose": "github-device-token"})
        if metadata is None:
            raise ValueError("Key Vault did not confirm token storage")
        properties["GALLERY_GITHUB_TOKEN"] = f"@Microsoft.KeyVault(SecretUri={metadata['id']})"
    else:
        if str(existing_value).startswith("@Microsoft.KeyVault("):
            raise ValueError("The host token uses Key Vault; configure the matching storage before connecting")
        properties["GALLERY_GITHUB_TOKEN"] = token
    properties["GALLERY_GITHUB_TOKEN_EXPIRES_AT"] = str(expires_at)
    await setup_cloud.arm_request(setting_path + "?api-version=2024-11-01", method="PUT", body={"properties": properties}, etag=app_settings.get("etag"))
    db.set_setup_setting("github_connection", {"login": account["login"], "id": account["id"], "expiresAt": expires_at or None, "method": "device"})
    db.set_setup_setting("github_credential", settings.model_dump(exclude={"confirm"}))
    os.environ["GALLERY_GITHUB_TOKEN"] = token
    os.environ["GALLERY_GITHUB_TOKEN_EXPIRES_AT"] = str(expires_at)


def provider_document(current: dict, settings: AuthenticationSettings) -> dict:
    if settings.providerName in ("aad", "azureactivedirectory", "facebook", "google", "github", "twitter", "apple"):
        raise ValueError("Choose a unique custom provider name; bootstrap providers cannot be replaced")
    if urlsplit(settings.publicUrl).path:
        raise ValueError("Public URL must be the application's origin")
    if urlsplit(settings.discoveryUrl).hostname != urlsplit(settings.issuer).hostname:
        raise ValueError("Discovery URL and issuer must share a hostname")
    if settings.kind == "entra":
        tenant = canonical_guid(os.environ.get("GALLERY_ENTRA_TENANT_ID", ""))
        if settings.issuer != f"https://login.microsoftonline.com/{tenant}/v2.0":
            raise ValueError("Entra issuer must match the organization Azure tenant")
        canonical_guid(settings.clientId)
        if settings.secretStorage == "none":
            raise ValueError("Entra web authentication requires a client credential; select its storage")
    if settings.secretStorage != "none" and not settings.secretSettingName:
        raise ValueError("Provide the App Service setting used by the identity provider")
    if settings.secretStorage == "app_service" and not os.environ.get(settings.secretSettingName):
        raise ValueError("The named secret app setting is missing")
    result = copy.deepcopy(current)
    properties = result.setdefault("properties", {})
    if not properties.get("platform", {}).get("enabled") or not properties.get("globalValidation", {}).get("requireAuthentication"):
        raise ValueError("Authenticated bootstrap ingress must be configured first")
    providers = properties.setdefault("identityProviders", {}).setdefault("customOpenIdConnectProviders", {})
    if settings.providerName in providers:
        raise ValueError("Provider already exists; use the hosting portal to change existing providers")
    registration = {
        "clientId": settings.clientId,
        "openIdConnectConfiguration": {"wellKnownOpenIdConfiguration": settings.discoveryUrl},
    }
    if settings.secretSettingName:
        registration["clientCredential"] = {"method": "ClientSecretPost", "clientSecretSettingName": settings.secretSettingName}
    providers[settings.providerName] = {
        "enabled": True, "registration": registration,
        "login": {"scopes": ["openid", "profile", "email"], "nameClaimType": "name"},
    }
    properties["globalValidation"]["excludedPaths"] = []
    properties.setdefault("httpSettings", {})["requireHttps"] = True
    return {"properties": properties}


async def apply_provider(settings: AuthenticationSettings) -> dict:
    if not settings.confirm:
        raise ValueError("Confirm adding this provider; the existing sign-in provider will be retained")
    existing = db.get_setup_setting("authentication", {})
    if existing:
        raise ValueError("An additional provider is already configured; manage existing providers in the hosting portal")
    path = auth_path()
    site_path = path.split("/config/", 1)[0]
    host = await setup_cloud.arm_request(site_path + "?api-version=2024-11-01")
    allowed_hosts = host.get("properties", {}).get("hostNames", [])
    if urlsplit(settings.publicUrl).hostname not in allowed_hosts:
        raise ValueError("Public application URL must match a hostname on this App Service")
    current = await setup_cloud.arm_request(path)
    document = provider_document(current, settings)
    if settings.secretStorage != "none":
        await bind_existing_secret(settings, site_path, host)
    await setup_cloud.arm_request(path, method="PUT", body=document, etag=current.get("etag"))
    saved = settings.model_dump(exclude={"confirm"})
    saved["loginUrl"] = f"{settings.publicUrl}/.auth/login/{settings.providerName}"
    saved["callbackUrl"] = saved["loginUrl"] + "/callback"
    saved["status"] = "configured-test-sign-in-required"
    db.set_setup_setting("authentication", saved)
    return saved


async def create_entra_registration(settings: EntraRegistration) -> dict:
    if not settings.confirm or urlsplit(settings.publicUrl).path:
        raise ValueError("Confirm registration creation and provide the application's HTTPS origin")
    host_path = auth_path().split("/config/", 1)[0]
    host = await setup_cloud.arm_request(host_path + "?api-version=2024-11-01")
    if urlsplit(settings.publicUrl).hostname not in host.get("properties", {}).get("hostNames", []):
        raise ValueError("Public URL must match this App Service hostname")
    existing = db.get_setup_setting("created_entra_registration")
    if existing and (existing.get("publicUrl") != settings.publicUrl or existing.get("providerName") != settings.providerName):
        raise ValueError("An application was already created for different settings; manage that registration in Entra before creating another")
    setting_name = settings.secretSettingName or f"PORTAL_{settings.providerName.upper()}_AUTH_SECRET"
    storage = {
        "secretStorage": settings.secretStorage, "secretSettingName": setting_name,
        "keyVaultResourceId": settings.keyVaultResourceId, "keyVaultSecretName": settings.keyVaultSecretName,
    }
    if existing and existing.get("credentialKeyId"):
        previous = {"secretStorage": "app_service", "keyVaultResourceId": "", "keyVaultSecretName": "", **existing}
        if any(previous.get(key) != value for key, value in storage.items()):
            raise ValueError("A credential already exists for a different storage choice; setup will not silently migrate it")
    if existing and existing.get("credentialStored"):
        return existing
    vault_uri = await key_vault_target(settings, host) if settings.secretStorage == "key_vault" else ""
    vault_secret = await key_vault_secret(vault_uri) if vault_uri else None
    if vault_secret and (not existing or vault_secret["tags"].get("portalCredentialKeyId") != existing.get("credentialKeyId")):
        raise ValueError("The selected Key Vault secret already exists; choose a new name for the generated credential")
    token = await asyncio.to_thread(azure_deploy._get_credential().get_token, "https://graph.microsoft.com/.default")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        headers = {"Authorization": f"Bearer {token.token}"}
        if not existing:
            response = await client.post("https://graph.microsoft.com/v1.0/applications", headers=headers, json={
                "displayName": settings.displayName, "signInAudience": "AzureADMyOrg",
                "web": {"redirectUris": [f"{settings.publicUrl}/.auth/login/{settings.providerName}/callback"]},
            })
            response.raise_for_status()
            data = response.json()
            tenant = canonical_guid(os.environ.get("GALLERY_ENTRA_TENANT_ID", ""))
            existing = {"clientId": data["appId"], "objectId": data["id"], "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0", "discoveryUrl": f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration", "publicUrl": settings.publicUrl, "providerName": settings.providerName}
            db.set_setup_setting("created_entra_registration", existing)
        if existing.get("publicUrl") != settings.publicUrl or existing.get("providerName") != settings.providerName:
            raise ValueError("An application was already created for different settings; manage that registration in Entra before creating another")
        if not existing.get("servicePrincipalId"):
            response = await client.post("https://graph.microsoft.com/v1.0/servicePrincipals", headers=headers, json={"appId": existing["clientId"]})
            response.raise_for_status()
            existing["servicePrincipalId"] = response.json()["id"]
            db.set_setup_setting("created_entra_registration", existing)
        app_settings_path = host_path + "/config/appsettings"
        app_settings = await setup_cloud.arm_request(app_settings_path + "/list?api-version=2024-11-01", method="POST")
        properties = app_settings.get("properties", {})
        expected_reference = f"@Microsoft.KeyVault(SecretUri={vault_secret['id']})" if vault_secret else ""
        if existing.get("credentialKeyId") and existing.get("secretSettingName") == setting_name:
            if properties.get(setting_name) and (not vault_uri or properties[setting_name] == expected_reference):
                existing["credentialStored"] = True
                db.set_setup_setting("created_entra_registration", existing)
                return existing
            if vault_secret:
                if properties.get(setting_name):
                    raise ValueError("The target app setting is already in use")
                properties[setting_name] = expected_reference
                await setup_cloud.arm_request(app_settings_path + "?api-version=2024-11-01", method="PUT", body={"properties": properties}, etag=app_settings.get("etag"))
                existing["credentialStored"] = True
                db.set_setup_setting("created_entra_registration", existing)
                return existing
            if properties.get(setting_name):
                raise ValueError("The target app setting is already in use")
            removal = await client.post(f"https://graph.microsoft.com/v1.0/applications/{existing['objectId']}/removePassword", headers=headers, json={"keyId": existing["credentialKeyId"]})
            removal.raise_for_status()
        if setting_name in properties and not existing.get("secretSettingName"):
            raise ValueError("The generated secret-setting name is already in use; choose another provider name")
        expiry = (datetime.now(timezone.utc) + timedelta(days=180)).isoformat()
        response = await client.post(f"https://graph.microsoft.com/v1.0/applications/{existing['objectId']}/addPassword", headers=headers, json={"passwordCredential": {"displayName": "Deployment Portal authentication", "endDateTime": expiry}})
        response.raise_for_status()
        password = response.json()
        existing.update({**storage, "credentialExpiresAt": expiry, "credentialKeyId": password["keyId"], "credentialStored": False})
        db.set_setup_setting("created_entra_registration", existing)
        if vault_uri:
            metadata = await key_vault_secret(vault_uri, value=password["secretText"], tags={"portalCredentialKeyId": password["keyId"], "portalApplicationId": existing["clientId"]})
            if metadata is None:
                raise ValueError("Key Vault did not confirm the secret write")
            properties[setting_name] = f"@Microsoft.KeyVault(SecretUri={metadata['id']})"
        else:
            properties[setting_name] = password["secretText"]
        await setup_cloud.arm_request(app_settings_path + "?api-version=2024-11-01", method="PUT", body={"properties": properties}, etag=app_settings.get("etag"))
        existing["credentialStored"] = True
        db.set_setup_setting("created_entra_registration", existing)
    result = existing
    return result