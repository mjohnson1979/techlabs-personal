"""Bounded organization-owned management API operations used by hosted setup."""

from __future__ import annotations

import asyncio
import copy
import os
import re
import time
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

import azure_deploy
from setup_config import Subscription, canonical_guid

_READ_CACHE: dict[str, tuple[float, dict]] = {}
_READ_LOCKS = tuple(asyncio.Lock() for _index in range(8))
FRESH_READ = ContextVar("fresh_arm_read", default=False)


async def cached_read(path: str) -> dict:
    async with _READ_LOCKS[hash(path) % len(_READ_LOCKS)]:
        cached = _READ_CACHE.get(path)
        if not FRESH_READ.get() and cached and cached[0] > time.monotonic():
            return copy.deepcopy(cached[1])
        result = await arm_request(path)
        if len(_READ_CACHE) >= 2048:
            _READ_CACHE.clear()
        _READ_CACHE[path] = (time.monotonic() + 300, copy.deepcopy(result))
        return result


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
    result = await cached_read(f"/subscriptions/{identifier}/locations?api-version=2022-12-01")
    if result.get("nextLink") or not isinstance(result.get("value"), list):
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
    return sorted({item["name"]: item for item in locations}.values(), key=lambda item: item["displayName"])


async def price_sheet(subscription_id: str) -> list[dict]:
    path = f"/subscriptions/{subscription_id}/providers/Microsoft.Consumption/pricesheets/default?api-version=2024-08-01&$top=1000"
    rows = []
    for _page in range(20):
        result = await cached_read(path)
        properties = result.get("properties", {})
        rows.extend(properties.get("pricesheets", []))
        path = properties.get("nextLink")
        if not path:
            return rows
        if urlsplit(path).path.lower() != f"/subscriptions/{subscription_id}/providers/microsoft.consumption/pricesheets/default".lower():
            raise ValueError("Unexpected price-sheet pagination scope")
    raise ValueError("Price sheet exceeds the lookup limit")


async def compute_capacity(subscription_id: str, location: str) -> dict:
    result = await cached_read(
        f"/subscriptions/{subscription_id}/providers/Microsoft.Compute/locations/{location}/usages?api-version=2024-03-01"
    )
    if result.get("nextLink") or not isinstance(result.get("value"), list):
        raise ValueError("Incomplete quota response")
    rows = []
    for entry in result.get("value", []):
        limit, current = entry.get("limit"), entry.get("currentValue")
        rows.append({
            "name": (entry.get("name") or {}).get("localizedValue") or (entry.get("name") or {}).get("value"),
            "key": (entry.get("name") or {}).get("value"),
            "limit": limit, "current": current,
            "available": max(0, limit - current) if type(limit) is int and type(current) is int and min(limit, current) >= 0 else None,
        })
    return {
        "subscriptionId": subscription_id, "location": location, "quotas": rows,
        "scope": "Microsoft.Compute", "physicalCapacity": "unknown",
        "detail": "Compute quota headroom only; physical allocation and other service quotas are not verified.",
    }


async def workload_capacity(subscription_id: str, location: str, item: dict, parameters: dict[str, str]) -> dict:
    from azure.core.exceptions import AzureError

    checked_at = time.time()
    try:
        compute = await compute_capacity(subscription_id, location)
    except (ValueError, httpx.HTTPError, AzureError, TimeoutError):
        compute = {"quotas": [], "detail": "Compute quota lookup unavailable; verify managed identity access."}
    checks = []
    quota = item.get("quota")
    requirements = quota.get("requirements", []) if isinstance(quota, dict) else []
    if not isinstance(requirements, list):
        requirements = []
    for requirement in requirements[:30]:
        if not isinstance(requirement, dict):
            checks.append({"service": "Service", "status": "unknown", "detail": "Invalid capacity requirement metadata."})
            continue
        label = str(requirement.get("service") or requirement.get("provider") or "Service")
        check = {"service": label, "status": "unknown", "detail": "No supported capacity check for this service."}
        try:
            if requirement.get("kind") == "model-tpm":
                embedding = "embed" in str(requirement.get("model", "")).lower()
                model = parameters.get("embeddingModel" if embedding else "chatModelName") or parameters.get("embeddingModel" if embedding else "chatModel") or requirement.get("model")
                version = parameters.get("embeddingModelVersion" if embedding else "chatModelVersion") or requirement.get("modelVersion")
                sku = parameters.get("embeddingModelSku" if embedding else "chatModelSku") or requirement.get("sku") or "Standard"
                if any(not isinstance(value, str) or not value or len(value) > 128 for value in (model, version, sku)):
                    raise ValueError("Exact model version is required")
                query = urlencode({"api-version": "2024-10-01", "modelFormat": "OpenAI", "modelName": model, "modelVersion": version})
                capacities = await cached_read(f"/subscriptions/{subscription_id}/providers/Microsoft.CognitiveServices/modelCapacities?{query}")
                models = await cached_read(f"/subscriptions/{subscription_id}/providers/Microsoft.CognitiveServices/locations/{location}/models?api-version=2024-10-01")
                if capacities.get("nextLink") or models.get("nextLink"):
                    raise ValueError("Incomplete model response")
                lifecycle = [entry["model"] for entry in models.get("value", []) if entry.get("model", {}).get("name") == model and entry.get("model", {}).get("version") == version]
                if len(lifecycle) != 1:
                    raise ValueError("Model lifecycle could not be verified")
                lifecycle_status = str(lifecycle[0].get("lifecycleStatus", "")).lower()
                offered_skus = [entry for entry in lifecycle[0].get("skus", []) if str(entry.get("name", "")).lower() == sku.lower()]
                if not lifecycle_status or len(offered_skus) != 1:
                    raise ValueError("Exact model lifecycle and SKU required")
                if offered_skus[0].get("deprecationDate"):
                    from datetime import datetime, timezone
                    if datetime.fromisoformat(offered_skus[0]["deprecationDate"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                        lifecycle_status = "deprecated"
                if lifecycle_status in ("deprecating", "deprecated"):
                    check.update(status="unavailable", detail=f"{model} {version} is deprecating or deprecated for new deployments.")
                else:
                    matches = [entry.get("properties", {}) for entry in capacities.get("value", [])
                               if str(entry.get("location", "")).lower() == location and str(entry.get("properties", {}).get("skuName", "")).lower() == sku.lower()]
                    if len(matches) != 1 or type(matches[0].get("availableCapacity")) is not int or matches[0]["availableCapacity"] < 0:
                        raise ValueError("Model capacity not reported")
                    available = matches[0]["availableCapacity"]
                    check.update(status="headroom" if available > 0 else "unavailable",
                                 detail=f"{model} {version} / {sku}: {available} K TPM reported available; requested size and allocation still need validation.")
            else:
                provider = str(requirement.get("provider", ""))
                if not re.fullmatch(r"Microsoft\.[A-Za-z]+/[A-Za-z]+(?:/[A-Za-z]+)*", provider):
                    checks.append(check)
                    continue
                namespace, resource_type = provider.split("/", 1)
                resource = await cached_read(f"/subscriptions/{subscription_id}/providers/{namespace}?api-version=2021-04-01")
                resource_types = [entry for entry in resource.get("resourceTypes", []) if str(entry.get("resourceType", "")).lower() == resource_type.lower()]
                if len(resource_types) != 1 or not isinstance(resource_types[0].get("locations"), list):
                    raise ValueError("Regional availability unknown")
                offered = location in {re.sub(r"[^a-z0-9]", "", name.lower()) for name in resource_types[0]["locations"]}
                if not offered:
                    check.update(status="unavailable", detail="Resource type is not listed in this region by Azure.")
                else:
                    check.update(status="offered", detail="Resource type offered; SKU, service quota and physical allocation are not verified.")
                    if provider.lower() == "microsoft.compute/virtualmachines":
                        regional = next((row for row in compute["quotas"] if str(row.get("key", "")).lower() == "cores"), None)
                        if regional and regional["available"] is not None:
                            check.update(status="headroom" if regional["available"] > 0 else "unavailable",
                                         detail=f"{regional['available']} regional vCPUs free; selected VM family/SKU and requested quantity are not verified.")
        except (ValueError, TypeError, KeyError, httpx.HTTPError, AzureError, TimeoutError):
            check.update(status="unknown", detail="Capacity lookup unavailable or incomplete; check access and the workload's exact model/SKU metadata.")
        checks.append(check)
    if len(requirements) > 30:
        checks.append({"service": "Additional services", "status": "unknown", "detail": "Workload exceeds the 30-service per-region check limit."})
    overall = "unavailable" if any(row["status"] == "unavailable" for row in checks) else "unknown" if not checks or any(row["status"] == "unknown" for row in checks) else "headroom" if all(row["status"] == "headroom" for row in checks) else "offered"
    return {**compute, "checks": checks, "status": overall, "checkedAt": checked_at, "physicalCapacity": "unknown"}