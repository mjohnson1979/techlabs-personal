"""Non-secret hosted setup configuration and subscription validation."""

from __future__ import annotations

import csv
import io
import os
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

import db


def canonical_guid(value: str) -> str:
    parsed = UUID(value.strip())
    if not parsed.int:
        raise ValueError("a nonzero GUID is required")
    return str(parsed)


class SubscriptionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriptionId: str
    pricingMode: Literal["retail", "negotiated"] = "retail"

    @field_validator("subscriptionId")
    @classmethod
    def guid(cls, value: str) -> str:
        return canonical_guid(value)


class Subscription(SubscriptionSelection):
    tenantId: str
    displayName: str = Field(min_length=1, max_length=100)

    @field_validator("tenantId")
    @classmethod
    def tenant_guid(cls, value: str) -> str:
        return canonical_guid(value)


def validate_subscriptions(items: list[SubscriptionSelection]) -> list[dict[str, str]]:
    if not 1 <= len(items) <= 200:
        raise ValueError("Configure between 1 and 200 subscriptions")
    tenant = canonical_guid(os.environ.get("GALLERY_ENTRA_TENANT_ID", ""))
    seen: set[str] = set()
    for item in items:
        if isinstance(item, Subscription) and item.tenantId != tenant:
            raise ValueError("All subscriptions must belong to the configured Azure tenant")
        if item.subscriptionId in seen:
            raise ValueError("Duplicate subscription IDs are not allowed")
        seen.add(item.subscriptionId)
    return [item.model_dump() for item in items]


def parse_subscriptions_csv(content: bytes) -> list[SubscriptionSelection]:
    if len(content) > 128 * 1024:
        raise ValueError("CSV exceeds 128 KiB")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    required = {"subscriptionId"}
    fields = reader.fieldnames or []
    if not required.issubset(fields) or len(fields) != len(set(fields)):
        raise ValueError("CSV needs a subscriptionId header without duplicate columns")
    if set(fields) - (required | {"pricingMode"}):
        raise ValueError("CSV contains unsupported columns")
    items = []
    for row in reader:
        if len(items) >= 200 or None in row or any(value is None for value in row.values()):
            raise ValueError("Invalid CSV row or too many subscriptions")
        items.append(SubscriptionSelection.model_validate({key: value.strip() for key, value in row.items() if value.strip()}))
    validate_subscriptions(items)
    return items


def subscriptions() -> list[dict[str, str]]:
    saved = db.get_setup_setting("subscriptions")
    if saved is not None:
        return saved
    identifier = os.environ.get("GALLERY_SUBSCRIPTION_ID", "").strip()
    if not identifier:
        return []
    return [{
        "subscriptionId": identifier.lower(),
        "tenantId": os.environ.get("GALLERY_ENTRA_TENANT_ID", "").strip().lower(),
        "displayName": identifier,
        "pricingMode": "retail",
    }]


def subscription_for(identifier: str = "") -> dict[str, str]:
    items = subscriptions()
    requested = identifier.strip().lower()
    if not requested and len(items) == 1:
        return items[0]
    for item in items:
        if item["subscriptionId"] == requested:
            return item
    raise ValueError("Select a configured customer subscription")