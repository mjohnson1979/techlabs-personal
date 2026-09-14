"""Bounded customer-owned management API operations used by hosted setup."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

import azure_deploy
from setup_config import Subscription, canonical_guid


async def arm_request(path: str, *, method: str = "GET", body: dict | None = None, etag: str | None = None) -> dict[str, Any]:
    url = path if path.startswith("https://") else "https://management.azure.com" + path
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "management.azure.com":
        raise ValueError("Invalid Azure management destination")
    credential = azure_deploy._get_credential()
    token = await asyncio.to_thread(credential.get_token, "https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token.token}"}
    if etag:
        headers["If-Match"] = etag
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        async with client.stream(method, url, json=body, headers=headers) as response:
            response.raise_for_status()
            response_etag = response.headers.get("etag")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("Azure response exceeds the setup limit")
    import json
    result = json.loads(content) if content else {}
    if not isinstance(result, dict):
        raise ValueError("Invalid Azure response")
    if response_etag:
        result["etag"] = response_etag
    return result


async def verify_subscription(item: dict[str, str]) -> dict[str, str]:
    identifier = canonical_guid(item["subscriptionId"])
    result = await arm_request(f"/subscriptions/{identifier}?api-version=2022-12-01")
    if canonical_guid(str(result.get("subscriptionId", ""))) != identifier:
        raise ValueError("Azure returned a different subscription")
    tenant = canonical_guid(os.environ.get("GALLERY_ENTRA_TENANT_ID", ""))
    if str(result.get("tenantId", "")).lower() != tenant:
        raise ValueError("Subscription tenant could not be verified")
    if str(result.get("state", "")).lower() != "enabled":
        raise ValueError("Subscription is not enabled")
    name = result.get("displayName")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Azure did not return a subscription display name")
    return Subscription(
        subscriptionId=identifier, tenantId=tenant, displayName=name.strip(),
        pricingMode=item.get("pricingMode", "retail"),
    ).model_dump()


async def subscription_locations(subscription_id: str) -> list[dict[str, str]]:
    identifier = canonical_guid(subscription_id)
    result = await arm_request(f"/subscriptions/{identifier}/locations?api-version=2022-12-01")
    if not isinstance(result.get("value"), list):
        raise ValueError("Azure did not return a locations list")
    locations = []
    for entry in result["value"]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]{2,30}", name):
            continue
        if (entry.get("metadata") or {}).get("regionType") == "Logical":
            continue
        locations.append({"name": name, "displayName": str(entry.get("displayName") or name)})
    return sorted(locations, key=lambda item: item["displayName"])


async def price_sheet(subscription_id: str) -> list[dict]:
    path = f"/subscriptions/{subscription_id}/providers/Microsoft.Consumption/pricesheets/default?api-version=2024-08-01&$top=1000"
    rows = []
    for _page in range(20):
        result = await arm_request(path)
        properties = result.get("properties", {})
        rows.extend(properties.get("pricesheets", []))
        path = properties.get("nextLink")
        if not path:
            return rows
        if urlsplit(path).path.lower() != f"/subscriptions/{subscription_id}/providers/microsoft.consumption/pricesheets/default".lower():
            raise ValueError("Unexpected price-sheet pagination scope")
    raise ValueError("Price sheet exceeds the lookup limit")


async def compute_capacity(subscription_id: str, location: str) -> dict:
    result = await arm_request(
        f"/subscriptions/{subscription_id}/providers/Microsoft.Compute/locations/{location}/usages?api-version=2024-03-01"
    )
    rows = []
    for entry in result.get("value", []):
        limit, current = entry.get("limit"), entry.get("currentValue")
        rows.append({
            "name": (entry.get("name") or {}).get("localizedValue") or (entry.get("name") or {}).get("value"),
            "limit": limit, "current": current,
            "available": max(0, limit - current) if isinstance(limit, (int, float)) and isinstance(current, (int, float)) else None,
        })
    return {
        "subscriptionId": subscription_id, "location": location, "quotas": rows,
        "scope": "Microsoft.Compute", "physicalCapacity": "unknown",
        "detail": "Compute quota headroom only; physical allocation and other service quotas are not verified.",
    }