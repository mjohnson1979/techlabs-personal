"""Prepare the organization container before importing the application."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from azure.core.exceptions import AzureError

import db
import distribution
import setup_auth
import setup_cloud
from setup_config import canonical_guid


def prepare_configuration() -> None:
    hostname = os.environ.get("WEBSITE_HOSTNAME", "")
    site_name = os.environ.get("WEBSITE_SITE_NAME", "")
    resource_group = os.environ.get("WEBSITE_RESOURCE_GROUP", "")
    owner = os.environ.get("WEBSITE_OWNER_NAME", "").split("+", 1)[0]
    if not os.environ.get("GALLERY_HOST_RESOURCE_ID"):
        try:
            subscription_id = str(UUID(owner))
            if not UUID(owner).int:
                raise ValueError
        except ValueError:
            raise ValueError("App Service subscription metadata is missing; set GALLERY_HOST_RESOURCE_ID") from None
        if not re.fullmatch(r"[A-Za-z0-9-]+", site_name) or not re.fullmatch(r"[A-Za-z0-9_.()-]+", resource_group):
            raise ValueError("App Service site metadata is missing; set GALLERY_HOST_RESOURCE_ID")
        os.environ["GALLERY_HOST_RESOURCE_ID"] = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Web/sites/{site_name}"
        )
    if not os.environ.get("GALLERY_PUBLIC_URL"):
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.azurewebsites\.net", hostname):
            raise ValueError("App Service hostname is missing; set GALLERY_PUBLIC_URL")
        os.environ["GALLERY_PUBLIC_URL"] = f"https://{hostname.lower()}"
    os.environ.setdefault("GALLERY_DISTRIBUTION", "organization")
    os.environ.setdefault("GALLERY_EASY_AUTH", "true")
    os.environ.setdefault("GALLERY_HTTPS", "true")
    os.environ.setdefault("GALLERY_MANAGED_IDENTITY", "user" if os.environ.get("AZURE_CLIENT_ID", "").strip() else "system")
    os.environ.setdefault("GALLERY_DB_PATH", "/home/data/deployment-portal.db")
    secret = os.environ.pop("GALLERY_SETUP_SECRET", "")
    db.init_db()
    if db.get_setup_setting("installation_complete", False):
        os.environ["GALLERY_INSTALLER_ENABLED"] = "false"
        return
    if not distribution.installer_requested():
        os.environ["GALLERY_INSTALLER_ENABLED"] = "false"
        return
    if not secret:
        return
    if not 32 <= len(secret) <= 128 or secret.startswith("@Microsoft.KeyVault("):
        raise ValueError("GALLERY_SETUP_SECRET must be a resolved random secret of 32 to 128 characters")
    digest = hashlib.sha256(secret.encode()).hexdigest()
    bootstrap = db.get_setup_setting("installation_bootstrap", {})
    if bootstrap.get("digest") != digest:
        used = db.get_setup_setting("installation_used_codes", [])
        if digest in used:
            raise ValueError("This setup secret was already used; provide a new random secret")
        if len(used) >= 100:
            raise ValueError("Installation rotation limit reached; administrator recovery is required")
        bootstrap = {
            "digest": digest,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        db.set_setup_setting("installation_used_codes", [*used, digest])
        db.set_setup_setting("installation_bootstrap", bootstrap)
    os.environ.update({
        "GALLERY_INSTALLER_ENABLED": "true",
        "GALLERY_SETUP_CODE_SHA256": bootstrap["digest"],
        "GALLERY_SETUP_EXPIRES_AT": bootstrap["expiresAt"],
    })


async def verify_host() -> None:
    path = setup_auth.auth_path()
    site_path = path.split("/config/", 1)[0]
    host = await setup_cloud.arm_request(site_path + "?api-version=2024-11-01")
    properties = host.get("properties", {})
    if str(host.get("id", "")).lower() != site_path.lower():
        raise ValueError("App Service resource identity could not be verified")
    if not properties.get("httpsOnly"):
        raise ValueError("Enable HTTPS-only access on this App Service")
    if urlsplit(os.environ["GALLERY_PUBLIC_URL"]).hostname not in properties.get("hostNames", []):
        raise ValueError("The application origin must be a hostname on this App Service")
    if os.environ.get("WEBSITES_ENABLE_APP_SERVICE_STORAGE", "").lower() != "true":
        raise ValueError("Enable persistent App Service storage for installation and application data")
    identity = host.get("identity", {})
    if distribution.uses_system_identity():
        if "SystemAssigned" not in identity.get("type", "") or not identity.get("principalId"):
            raise ValueError("Enable the system-assigned managed identity on this App Service")
    elif not any(
        value.get("clientId", "").lower() == os.environ.get("AZURE_CLIENT_ID", "").lower()
        for value in identity.get("userAssignedIdentities", {}).values()
    ):
        raise ValueError("Attach the configured user-assigned managed identity to this App Service")
    auth = await setup_cloud.arm_request(path)
    auth_properties = auth.get("properties", {})
    if not auth_properties.get("platform", {}).get("enabled") or not auth_properties.get("httpSettings", {}).get("requireHttps"):
        raise ValueError("Enable App Service Authentication and its HTTPS requirement before starting the container")
    validation = auth_properties.get("globalValidation", {})
    if distribution.installer_requested():
        if not db.get_setup_setting("installation_verified") and (
            validation.get("requireAuthentication") is not False
            or validation.get("unauthenticatedClientAction") != "AllowAnonymous"
        ):
            raise ValueError("First-run ingress must allow the protected installer through Easy Auth; keep the platform enabled")
    else:
        if validation.get("requireAuthentication") is not True or validation.get("excludedPaths"):
            raise ValueError("Sign-in-first installations require authenticated ingress on every path")
        provider = auth_properties.get("identityProviders", {}).get("azureActiveDirectory", {})
        if provider.get("enabled") is not True:
            raise ValueError("Configure the organization Entra provider in App Service Authentication first")
        registration = provider.get("registration", {})
        client_id = canonical_guid(str(registration.get("clientId", "")))
        issuer = str(registration.get("openIdIssuer", ""))
        match = re.fullmatch(r"https://login\.microsoftonline\.com/([0-9a-fA-F-]{36})/v2\.0", issuer)
        if not match:
            raise ValueError("Configure a tenant-specific Entra v2 issuer")
        tenant_id = canonical_guid(match.group(1))
        audiences = provider.get("validation", {}).get("allowedAudiences", [])
        if not isinstance(audiences, list) or any(value != client_id for value in audiences):
            raise ValueError("Entra audiences must be restricted to this application's client ID")
        subscription_id = canonical_guid(site_path.split("/")[2])
        subscription = await setup_cloud.arm_request(f"/subscriptions/{subscription_id}?api-version=2022-12-01")
        if (str(subscription.get("subscriptionId", "")).lower() != subscription_id
                or str(subscription.get("tenantId", "")).lower() != tenant_id):
            raise ValueError("The Entra provider must belong to the hosting subscription's tenant")
        for name, value in (("GALLERY_ENTRA_TENANT_ID", tenant_id), ("GALLERY_ENTRA_CLIENT_ID", client_id)):
            configured = os.environ.get(name, "").strip().lower()
            if configured and configured != value:
                raise ValueError("App Service Authentication does not match the organization Entra configuration")
        os.environ["GALLERY_ENTRA_TENANT_ID"] = tenant_id
        os.environ["GALLERY_ENTRA_CLIENT_ID"] = client_id


def main() -> int:
    try:
        prepare_configuration()
        asyncio.run(verify_host())
        distribution.validate_customer_configuration()
    except (ValueError, OSError, httpx.HTTPError, AzureError):
        sys.stderr.write(
            "Container startup blocked: verify App Service HTTPS, Easy Auth, "
            "persistent storage, managed identity/RBAC, organization Entra provider and GALLERY_ADMIN_OIDS. "
            "If explicitly using the code installer, check its unexpired setup secret. "
            "No settings or remote error bodies are printed.\n"
        )
        return 1
    finally:
        if db._CONN is not None:
            db._CONN.close()
            db._CONN = None
    os.execvp("gunicorn", [
        "gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "main:app",
        "--bind", "0.0.0.0:8000", "--access-logfile", "/dev/null",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())