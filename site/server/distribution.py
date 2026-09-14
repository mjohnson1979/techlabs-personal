"""Customer distribution configuration and fail-closed startup checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

CUSTOMER_MARKER = Path(__file__).resolve().parent.parent / "distribution.json"


def is_customer_distribution() -> bool:
    return CUSTOMER_MARKER.is_file() or os.environ.get(
        "GALLERY_DISTRIBUTION", "internal"
    ).strip().lower() == "customer"


def brand_name() -> str:
    return os.environ.get("GALLERY_BRAND_NAME", "Deployment Portal").strip()[:80] or "Deployment Portal"


def admin_oids() -> set[str]:
    return {
        value.strip().lower()
        for value in os.environ.get("GALLERY_ADMIN_OIDS", "").split(",")
        if value.strip()
    }


def uses_system_identity() -> bool:
    return os.environ.get("GALLERY_MANAGED_IDENTITY", "user").strip().lower() == "system"


def installer_requested() -> bool:
    return is_customer_distribution() and os.environ.get(
        "GALLERY_INSTALLER_ENABLED", "false"
    ).strip().lower() in ("1", "true", "yes")


def installer_configuration_errors() -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[a-f0-9]{64}", os.environ.get("GALLERY_SETUP_CODE_SHA256", "")):
        errors.append("GALLERY_SETUP_CODE_SHA256 must contain a SHA-256 setup-code digest")
    try:
        expires = datetime.fromisoformat(os.environ.get("GALLERY_SETUP_EXPIRES_AT", ""))
        remaining = (expires - datetime.now(timezone.utc)).total_seconds()
        if not 0 < remaining <= 86400:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("GALLERY_SETUP_EXPIRES_AT must be a future timezone-aware time within 24 hours")
    try:
        public_url = urlsplit(os.environ.get("GALLERY_PUBLIC_URL", ""))
        if (public_url.scheme != "https" or not public_url.hostname
                or public_url.username or public_url.password or public_url.port not in (None, 443)
                or public_url.path or public_url.query or public_url.fragment):
            raise ValueError
    except ValueError:
        errors.append("GALLERY_PUBLIC_URL must be the HTTPS application origin")
    if not re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.Web/sites/[A-Za-z0-9-]+",
        os.environ.get("GALLERY_HOST_RESOURCE_ID", ""),
    ):
        errors.append("GALLERY_HOST_RESOURCE_ID must identify this App Service")
    return errors


def customer_configuration_errors() -> list[str]:
    errors: list[str] = []
    if not is_customer_distribution():
        errors.append(
            "Customer mode is not enabled; use a customer export or set "
            "GALLERY_DISTRIBUTION=customer"
        )
    installing = installer_requested()
    if installing:
        errors.extend(installer_configuration_errors())
    required_ids = ("AZURE_CLIENT_ID",) if installing else (
        "GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID", "AZURE_CLIENT_ID",
    )
    if os.environ.get("GALLERY_MANAGED_IDENTITY", "user").strip().lower() not in ("system", "user"):
        errors.append("GALLERY_MANAGED_IDENTITY must be system or user")
    if uses_system_identity():
        required_ids = tuple(name for name in required_ids if name != "AZURE_CLIENT_ID")
        if os.environ.get("AZURE_CLIENT_ID", "").strip():
            errors.append("AZURE_CLIENT_ID must be empty for the system-assigned identity")
    for name in required_ids:
        try:
            value = UUID(os.environ.get(name, "").strip())
            if value.int == 0:
                raise ValueError
        except ValueError:
            errors.append(f"{name} must be a nonzero GUID from your customer configuration")
    if os.environ.get("GALLERY_SUBSCRIPTION_ID", "").strip():
        try:
            if not UUID(os.environ["GALLERY_SUBSCRIPTION_ID"].strip()).int:
                raise ValueError
        except ValueError:
            errors.append("GALLERY_SUBSCRIPTION_ID must be a nonzero GUID or left empty for hosted setup")
    if os.environ.get("GALLERY_ENTRA_CLIENT_ID", "").strip().lower() == "04b07795-8ddb-461a-bbee-02f9e1bf7b46":
        errors.append("Use your own Entra app registration, not the Azure CLI client")
    for name in ("GALLERY_EASY_AUTH", "GALLERY_HTTPS"):
        if os.environ.get(name, "").strip().lower() not in ("1", "true", "yes"):
            errors.append(f"{name}=true is required for customer installations")
    if not installing and not admin_oids():
        errors.append("GALLERY_ADMIN_OIDS must explicitly identify an administrator")
    for oid in admin_oids():
        try:
            if UUID(oid).int == 0:
                raise ValueError
        except ValueError:
            errors.append("GALLERY_ADMIN_OIDS must contain nonzero object GUIDs")
            break
    tenant = os.environ.get("GALLERY_ENTRA_TENANT_ID", "").strip().lower()
    subscription = os.environ.get("GALLERY_SUBSCRIPTION_ID", "").strip().lower()
    for name, expected in (("ARM_TENANT_ID", tenant), ("ARM_SUBSCRIPTION_ID", subscription)):
        configured = os.environ.get(name, "").strip().lower()
        if configured and configured != expected:
            errors.append(f"{name} must match the customer installation")
    for name in ("GALLERY_INVITE_GUEST", "GALLERY_TERRAFORM_AUTO_INSTALL"):
        if os.environ.get(name, "false").strip().lower() in ("1", "true", "yes"):
            errors.append(f"{name} is disabled in customer installations")
    return errors


def validate_customer_configuration() -> None:
    if not is_customer_distribution():
        return
    errors = customer_configuration_errors()
    if errors:
        raise ValueError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check customer environment settings locally, without starting the app or contacting Azure."
    )
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    errors = customer_configuration_errors()
    limitation = (
        "Local configuration only: resource ownership, tenant membership, "
        "Easy Auth ingress, RBAC and release approvals have not been verified."
    )
    if args.as_json:
        sys.stdout.write(json.dumps({
            "scope": "local-configuration-only",
            "passed": not errors,
            "errors": errors,
            "limitations": [limitation],
        }, indent=2) + "\n")
    else:
        heading = "Customer configuration failed:" if errors else "Customer configuration passed."
        sys.stdout.write(heading + "\n")
        for error in errors:
            sys.stdout.write(f"- {error}\n")
        sys.stdout.write(limitation + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())