"""Live Azure Retail Prices lookups for the dynamic cost estimate.

Ports the component-pricing heuristic from tools/Build-Catalog.ps1 to Python so
POST /api/estimate can re-price a use case's cost for a chosen region and
parameter overrides. Uses the public retail prices API (no auth).

To stay fast, a component reuses the catalog's precomputed unit price whenever the
region is unchanged and its SKU isn't overridden, so a capacity-only change needs
no network call and a SKU change prices just that one component. Rows are cached
in-process per OData filter.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

_RETAIL_API = "https://prices.azure.com/api/retail/prices"
_MAX_PAGES = 40
_price_cache: dict[str, list[dict[str, Any]]] = {}
_DISCOUNT_RE = re.compile(r"discount|reserv|spot|low priority|dev[ /-]?test", re.I)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _odata(value: Any) -> str:
    # OData escapes a single quote by doubling it.
    return str(value).replace("'", "''")


def _contains(row: dict[str, Any], field: str, needle: str) -> bool:
    return needle.lower() in str(row.get(field) or "").lower()


async def _fetch_rows(
    client: httpx.AsyncClient, region: str, currency: str,
    service_name: str | None, meter_exact: str | None, arm_exact: str | None = None,
) -> list[dict[str, Any]]:
    parts = [f"armRegionName eq '{_odata(region)}'", "priceType eq 'Consumption'"]
    if service_name:
        parts.append(f"serviceName eq '{_odata(service_name)}'")
    if meter_exact:
        parts.append(f"meterName eq '{_odata(meter_exact)}'")
    if arm_exact:
        parts.append(f"armSkuName eq '{_odata(arm_exact)}'")
    flt = " and ".join(parts)
    # Cache key includes currency: the same filter in another currency must not
    # return the previously cached prices.
    cache_key = f"{currency}\n{flt}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    rows: list[dict[str, Any]] = []
    url: str | None = f"{_RETAIL_API}?currencyCode={currency}&$filter={quote(flt, safe='')}"
    page = 0
    while url and page < _MAX_PAGES:
        resp = None
        for attempt in range(5):
            resp = await client.get(url)
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            break
        # Persistent rate-limit: raise so the route returns 502 and the UI keeps
        # the static cost, rather than caching a partial/empty result.
        if resp is None or resp.status_code == 429:
            raise RuntimeError("retail prices API rate-limited after retries")
        data = resp.json()
        rows.extend(data.get("Items") or [])
        url = data.get("NextPageLink")
        page += 1
    # Only cache complete result sets. If we stopped at the page cap (url still
    # set), the rows are truncated and caching them would poison later lookups.
    if url is None:
        _price_cache[cache_key] = rows
    return rows


def _pick_unit_price(rows: list[dict[str, Any]], comp: dict[str, Any], sku: str | None) -> float | None:
    cand = rows
    # A SKU override replaces the authored SKU, so drop meter/skuName filters that
    # encode the default SKU (e.g. "P1 v3") and match on the overriding SKU instead.
    if comp.get("skuNameContains") and not sku:
        cand = [r for r in cand if _contains(r, "skuName", comp["skuNameContains"])]
    if comp.get("productNameContains"):
        cand = [r for r in cand if _contains(r, "productName", comp["productNameContains"])]
    if comp.get("meterNameContains") and not sku:
        cand = [r for r in cand if _contains(r, "meterName", comp["meterNameContains"])]
    arm = sku or comp.get("armSkuName")
    if arm:
        want = _norm(arm)
        cand = [
            r for r in cand
            if want in _norm(" ".join([str(r.get("armSkuName") or ""), str(r.get("skuName") or ""), str(r.get("meterName") or "")]))
        ]
    cand = [
        r for r in cand
        if not _DISCOUNT_RE.search(" ".join([str(r.get("productName") or ""), str(r.get("skuName") or ""), str(r.get("meterName") or "")]))
    ]
    cand = [r for r in cand if (r.get("retailPrice") or 0) > 0]
    if comp.get("meterNameContains") and not sku:
        exact = [r for r in cand if str(r.get("meterName")) == comp["meterNameContains"]]
        if exact:
            cand = exact
    if not cand:
        return None
    return round(min(cand, key=lambda r: r["retailPrice"])["retailPrice"], 5)


async def estimate(cost: dict[str, Any], region: str, parameter_values: dict[str, str]) -> dict[str, Any]:
    currency = cost.get("currency") or "USD"
    base_region = str(cost.get("region") or region or "eastus").lower()
    region = (region or base_region).lower()
    pv = parameter_values or {}
    components = cost.get("components") or []

    out: list[dict[str, Any]] = []
    fixed = usage = 0.0
    complete = True
    client: httpx.AsyncClient | None = None
    try:
        for comp in components:
            qty = comp.get("quantityPerMonth") or 0
            inst_param = comp.get("instanceParam")
            if inst_param and pv.get(inst_param) not in (None, ""):
                try:
                    qty = int(str(pv[inst_param])) * int(comp.get("hoursPerInstance") or 730)
                except ValueError:
                    pass

            # A SKU that changes the *count* of a fixed-price unit (Functions
            # EP1/EP2 = 1/2 vCPUs, etc.) scales quantity via a value->units map
            # rather than swapping the unit price.
            sq_param = comp.get("skuQuantityParam")
            sq_map = comp.get("skuQuantityMap")
            if sq_param and sq_map and pv.get(sq_param) not in (None, ""):
                units = sq_map.get(str(pv[sq_param]))
                if units is not None:
                    qty = float(units) * int(comp.get("hoursPerInstance") or 730)

            # Zone redundancy forces a minimum instance floor (App Service ZR
            # needs >= 3 instances), so toggling it on reprices even when the
            # instance count itself wasn't raised.
            zr_param = comp.get("zoneRedundantParam")
            if zr_param and str(pv.get(zr_param, "")).strip().lower() in ("true", "1", "yes", "on"):
                floor = int(comp.get("zoneRedundantMinInstances") or 3) * int(comp.get("hoursPerInstance") or 730)
                if qty < floor:
                    qty = floor

            sku_param = comp.get("skuParam")
            sku_override = pv.get(sku_param) if sku_param else None
            same_region = region == base_region
            if same_region and not sku_override and comp.get("unitPrice") is not None:
                unit: float | None = comp["unitPrice"]
            else:
                if client is None:
                    client = httpx.AsyncClient(timeout=20, headers={"User-Agent": "techlabs-estimate"})
                # VM SKUs use the real armSkuName (e.g. Standard_D8s_v5); push it
                # server-side so the huge "Virtual Machines" set isn't truncated.
                meter_exact = comp.get("meterNameExact")
                arm_exact = None
                if sku_override and "_" in str(sku_override):
                    arm_exact = str(sku_override)
                    meter_exact = None
                rows = await _fetch_rows(client, region, currency, comp.get("serviceName"), meter_exact, arm_exact)
                unit = _pick_unit_price(rows, comp, sku_override)

            # A tier SKU (SQL General Purpose vs Business Critical) changes the
            # per-unit price of the same meter; apply a multiplier when that
            # param is overridden.
            spm_param = comp.get("skuPriceMultiplierParam")
            spm_map = comp.get("skuPriceMultiplierMap")
            if unit is not None and spm_param and spm_map and pv.get(spm_param) not in (None, ""):
                mult = spm_map.get(str(pv[spm_param]))
                if mult is not None:
                    unit = unit * float(mult)

            monthly = round(unit * qty, 2) if unit is not None else None
            if monthly is None:
                complete = False
            elif comp.get("billing") == "fixed":
                fixed += monthly
            else:
                usage += monthly

            out.append({
                "name": comp.get("name"),
                "billing": comp.get("billing"),
                "unitOfMeasure": comp.get("unitOfMeasure"),
                "quantityPerMonth": qty,
                "unitPrice": unit,
                "monthly": monthly,
                "notes": comp.get("notes"),
            })
    finally:
        if client is not None:
            await client.aclose()

    return {
        "currency": currency,
        "region": region,
        "components": out,
        "fixedMonthly": round(fixed, 2),
        "usageMonthly": round(usage, 2),
        "totalMonthly": round(fixed + usage, 2),
        "complete": complete,
    }
