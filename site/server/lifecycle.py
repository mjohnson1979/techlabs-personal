"""Public Azure lifecycle snapshots shared by the portal and reviewed runners."""

from __future__ import annotations

import asyncio
import calendar
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit

import httpx

FEED_URL = "https://www.microsoft.com/releasecommunications/api/v2/azure"
SOURCE_URL = "https://azure.microsoft.com/updates"
INTERVAL = 86400
MAX_PAGES = 200
MAX_BYTES = 16 * 1024 * 1024
BUCKETS = ("retirements", "previews", "launched")
FEED_BACKEND = "relcomms-prod-dagnegedescbeefs.b02.azurefd.net"
ALIASES = {
    "openai": ("azure openai",), "search": ("azure ai search", "cognitive search", "azure search"),
    "cosmos": ("cosmos db",), "sql": ("sql database", "sql managed instance", "azure sql"),
    "appservice": ("app service", "web app"), "containerapps": ("container apps",),
    "aks": ("kubernetes service",), "acr": ("container registry",), "functions": ("azure functions", "function app"),
    "logicapps": ("logic apps",), "apim": ("api management",), "appgateway": ("application gateway",),
    "firewall": ("azure firewall", "web application firewall"), "bastion": ("azure bastion",),
    "frontdoor": ("front door",), "expressroute": ("expressroute",), "vnet": ("virtual network",),
    "vm": ("virtual machines", "virtual machine scale sets"), "avd": ("azure virtual desktop",),
    "avs": ("vmware solution",), "storage": ("blob storage", "azure files", "storage accounts", "azure storage"),
    "keyvault": ("key vault",), "servicebus": ("service bus",), "eventhubs": ("event hubs",),
    "streamanalytics": ("stream analytics",), "datafactory": ("data factory",), "databricks": ("databricks",),
    "monitor": ("azure monitor", "application insights"), "loganalytics": ("log analytics",),
}


def service_key(product: str) -> str:
    normalized = product.strip().lower()
    for key, aliases in ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return key
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:100] or "other"


def month(value: object) -> str | None:
    if not value:
        return None
    text = str(value)[:7]
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", text):
        raise ValueError("Invalid lifecycle month")
    return text


def classify(item: dict) -> tuple[str, str | None] | None:
    tags = item.get("tags") or []
    availabilities = item.get("availabilities") or []
    if not isinstance(tags, list) or not isinstance(availabilities, list):
        raise ValueError("Invalid lifecycle fields")
    retiring = "retirements" in [str(tag).lower() for tag in tags]
    retirement = None
    for availability in availabilities:
        if not isinstance(availability, dict):
            raise ValueError("Invalid lifecycle availability")
        if str(availability.get("ring", "")).lower() == "retirement":
            retiring = True
            year, number = availability.get("year"), availability.get("month")
            if year and number:
                names = {name.lower(): index for index, name in enumerate(calendar.month_name) if index}
                names.update({name.lower(): index for index, name in enumerate(calendar.month_abbr) if index})
                number = names.get(str(number).lower(), number)
                retirement = month(f"{int(year):04d}-{int(number):02d}")
    if retiring:
        return "retirements", retirement
    status = str(item.get("status", "")).lower()
    if status == "launched":
        return "launched", month(item.get("generalAvailabilityDate"))
    if status == "in preview":
        return "previews", month(item.get("previewAvailabilityDate"))
    if item.get("generalAvailabilityDate"):
        return "launched", month(item["generalAvailabilityDate"])
    if item.get("previewAvailabilityDate"):
        return "previews", month(item["previewAvailabilityDate"])
    return None


def project(items: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    current = now.strftime("%Y-%m")
    cutoff = f"{now.year - 1:04d}-{now.month:02d}"
    services: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid lifecycle entry")
        classified = classify(item)
        if not classified:
            continue
        bucket, date = classified
        if date and ((bucket == "retirements" and date < current) or (bucket != "retirements" and date < cutoff)):
            continue
        identifier = str(item.get("id", ""))
        title = item.get("title")
        products = item.get("products") or []
        if not identifier or len(identifier) > 200 or not isinstance(title, str) or not title.strip() or len(title) > 1000 or not isinstance(products, list):
            raise ValueError("Invalid lifecycle identity or products")
        entry = {"id": identifier, "title": title.strip(), "date": date, "url": SOURCE_URL + "?id=" + quote(identifier, safe="")}
        for product in products:
            if not isinstance(product, str) or not product.strip() or len(product) > 200:
                raise ValueError("Invalid lifecycle product")
            key = service_key(product)
            service = services.setdefault(key, {"label": product.strip(), **{name: [] for name in BUCKETS}})
            if not any(previous["id"] == identifier or previous["title"].casefold() == entry["title"].casefold() for previous in service[bucket]):
                service[bucket].append(entry)
    for service in services.values():
        for bucket in BUCKETS:
            service[bucket] = sorted(service[bucket], key=lambda entry: (entry["date"] is None, entry["date"] or "", entry["title"])) if bucket == "retirements" else sorted(service[bucket], key=lambda entry: entry["date"] or "", reverse=True)
            service[bucket] = service[bucket][:50]
    return {"schemaVersion": 1, "generated": now.isoformat(), "source": SOURCE_URL, "services": services}


def validate_snapshot(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or value.get("source") != SOURCE_URL:
        raise ValueError("Invalid lifecycle snapshot")
    generated = datetime.fromisoformat(value.get("generated", ""))
    if generated.tzinfo is None or generated.timestamp() > time.time() + 300:
        raise ValueError("Invalid lifecycle snapshot timestamp")
    services = value.get("services")
    if not isinstance(services, dict) or len(services) > 500:
        raise ValueError("Invalid lifecycle services")
    clean = {}
    for key, service in services.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", key) or not isinstance(service, dict):
            raise ValueError("Invalid lifecycle service")
        label = service.get("label")
        if not isinstance(label, str) or not 1 <= len(label) <= 200:
            raise ValueError("Invalid lifecycle label")
        clean[key] = {"label": label}
        for bucket in BUCKETS:
            entries = service.get(bucket)
            if not isinstance(entries, list) or len(entries) > 50:
                raise ValueError("Invalid lifecycle bucket")
            clean[key][bucket] = []
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not 1 <= len(entry["id"]) <= 200:
                    raise ValueError("Invalid lifecycle notice")
                if not isinstance(entry.get("title"), str) or not 1 <= len(entry["title"]) <= 1000:
                    raise ValueError("Invalid lifecycle title")
                if entry.get("url") != SOURCE_URL + "?id=" + quote(entry["id"], safe=""):
                    raise ValueError("Invalid lifecycle link")
                date = entry.get("date")
                if date is not None and (not isinstance(date, str) or month(date) != date):
                    raise ValueError("Invalid lifecycle date")
                clean[key][bucket].append({name: entry.get(name) for name in ("id", "title", "url", "date")})
    return {"schemaVersion": 1, "generated": generated.isoformat(), "source": SOURCE_URL, "services": clean}


async def fetch_snapshot() -> dict:
    url = FEED_URL + "?$select=id,title,products,tags,status,availabilities,generalAvailabilityDate,previewAvailabilityDate"
    visited = set()
    items = []
    total = 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for _page in range(MAX_PAGES):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc != "www.microsoft.com" or parsed.path != urlsplit(FEED_URL).path or parsed.fragment or url in visited:
                raise ValueError("Unexpected lifecycle pagination destination")
            visited.add(url)
            async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    content.extend(chunk)
                    if total > MAX_BYTES or len(content) > 2 * 1024 * 1024:
                        raise ValueError("Lifecycle feed exceeds response limit")
            document = json.loads(content)
            if not isinstance(document, dict) or not isinstance(document.get("value"), list):
                raise ValueError("Invalid lifecycle feed")
            items.extend(document["value"])
            if len(items) > 20000:
                raise ValueError("Lifecycle feed exceeds item limit")
            next_link = document.get("@odata.nextLink")
            if not next_link:
                if not items:
                    raise ValueError("Empty lifecycle feed; keeping the previous snapshot")
                return validate_snapshot(project(items))
            if not isinstance(next_link, str):
                raise ValueError("Invalid lifecycle pagination")
            next_url = urlsplit(urljoin(url, next_link))
            public_path = next_url.netloc == "www.microsoft.com" and next_url.path.rstrip("/") == urlsplit(FEED_URL).path
            backend_path = next_url.netloc == FEED_BACKEND and next_url.path.rstrip("/") == "/api/v2/azure"
            if next_url.scheme != "https" or next_url.fragment or not (public_path or backend_path):
                raise ValueError("Unexpected lifecycle pagination destination")
            url = FEED_URL + ("?" + next_url.query if next_url.query else "")
    raise ValueError("Lifecycle feed pagination incomplete; keeping the previous snapshot")


def snapshot() -> dict:
    import db

    value = db.get_setup_setting("lifecycle_snapshot")
    job = db.get_setup_setting("lifecycle_job", {"status": "pending", "detail": "Waiting for the first scheduled refresh."})
    return {**(value or {"services": {}, "source": SOURCE_URL, "generated": None}), "job": job,
            "serviceAliases": ALIASES,
            "stale": not value or time.time() - datetime.fromisoformat(value["generated"]).timestamp() >= INTERVAL}


def import_runner(content: bytes) -> None:
    import db

    try:
        value = validate_snapshot(json.loads(content))
    except (ValueError, TypeError, KeyError):
        db.set_setup_setting("lifecycle_job", {"status": "failed", "source": "runner",
                             "detail": "Runner lifecycle feed is invalid. Last successful data retained; review the lifecycle workflow."})
        return
    previous = db.get_setup_setting("lifecycle_snapshot")
    if previous and datetime.fromisoformat(value["generated"]) < datetime.fromisoformat(previous["generated"]):
        return
    db.set_setup_setting("lifecycle_snapshot", value)
    db.set_setup_setting("lifecycle_job", {"status": "succeeded", "source": "runner", "completedAt": time.time(),
                         "detail": "Lifecycle feed imported from the scheduled workflow on the reviewed branch."})


async def refresh() -> None:
    import db
    import catalog_processing
    import setup_github

    mode = catalog_processing.settings()
    if mode["mode"] != "container" and db.get_setup_setting("github", {}).get("enabled"):
        if catalog_processing.runner_available(mode, await setup_github.processing_probe()):
            if db.get_setup_setting("lifecycle_job", {}).get("source") != "runner":
                db.set_setup_setting("lifecycle_job", {"status": "runner", "source": "runner", "detail": "Waiting for the daily lifecycle workflow on the reviewed branch."})
            return
    current = db.get_setup_setting("lifecycle_snapshot")
    job = db.get_setup_setting("lifecycle_job", {})
    now = time.time()
    if job.get("nextAttemptAt", 0) > now:
        return
    if current and now - datetime.fromisoformat(current["generated"]).timestamp() < INTERVAL:
        return
    db.set_setup_setting("lifecycle_job", {"status": "running", "startedAt": now, "detail": "Fetching the public Azure Updates lifecycle feed."})
    try:
        value = await asyncio.wait_for(fetch_snapshot(), timeout=300)
        db.set_setup_setting("lifecycle_snapshot", value)
        db.set_setup_setting("lifecycle_job", {"status": "succeeded", "completedAt": time.time(), "nextRunAt": time.time() + INTERVAL,
                             "detail": "Azure lifecycle feed refreshed successfully."})
    except asyncio.CancelledError:
        db.set_setup_setting("lifecycle_job", {"status": "interrupted", "detail": "Refresh interrupted; the last successful feed is retained."})
        raise
    except (ValueError, TypeError, KeyError, httpx.HTTPError, TimeoutError):
        db.set_setup_setting("lifecycle_job", {"status": "failed", "completedAt": time.time(), "nextAttemptAt": time.time() + 600,
                             "detail": "Lifecycle feed refresh failed or was incomplete. Last successful data retained; automatic retry in ten minutes."})


async def run_schedule(stop: asyncio.Event) -> None:
    import db
    from azure.core.exceptions import AzureError

    while not stop.is_set():
        try:
            await refresh()
        except (ValueError, TypeError, KeyError, httpx.HTTPError, AzureError, OSError):
            db.set_setup_setting("lifecycle_job", {"status": "failed", "nextAttemptAt": time.time() + 600,
                                 "detail": "Lifecycle scheduling unavailable; saved feed retained. Check processing configuration and access."})
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except TimeoutError:
            pass