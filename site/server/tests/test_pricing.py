"""Unit tests for the dynamic cost estimate logic (no network).

`_fetch_rows` is monkeypatched so these assert the override/reuse behaviour
without calling the live retail prices API: a capacity change scales quantity and
reuses the catalog unit price (no lookup), while a SKU change or region change
triggers a lookup.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import pricing  # noqa: E402


def _cost() -> dict:
    return {
        "currency": "USD",
        "region": "eastus",
        "components": [{
            "name": "App Service plan",
            "billing": "fixed",
            "serviceName": "Azure App Service",
            "armSkuName": "P1v3",
            "productNameContains": "Linux",
            "quantityPerMonth": 2190,
            "hoursPerInstance": 730,
            "skuParam": "appServiceSku",
            "instanceParam": "appCapacity",
            "unitPrice": 0.20,
            "monthly": 438.0,
        }],
    }


def _patch_rows(monkeypatch, rows, counter):
    async def fake(_client, _region, _currency, _service, _meter, _arm=None):
        counter.append(1)
        return rows
    monkeypatch.setattr(pricing, "_fetch_rows", fake)


def test_same_region_no_override_reuses_price(monkeypatch):
    calls: list[int] = []
    _patch_rows(monkeypatch, [], calls)
    est = asyncio.run(pricing.estimate(_cost(), "eastus", {}))
    assert est["totalMonthly"] == 438.0
    assert calls == []  # no network lookup


def test_capacity_override_scales_without_lookup(monkeypatch):
    calls: list[int] = []
    _patch_rows(monkeypatch, [], calls)
    est = asyncio.run(pricing.estimate(_cost(), "eastus", {"appCapacity": "1"}))
    comp = est["components"][0]
    assert comp["quantityPerMonth"] == 730
    assert comp["monthly"] == 146.0  # 0.20 * 730
    assert calls == []


def test_sku_override_triggers_lookup(monkeypatch):
    calls: list[int] = []
    rows = [{"armSkuName": "B1", "skuName": "B1", "meterName": "B1 App", "productName": "App Service Linux", "retailPrice": 0.10}]
    _patch_rows(monkeypatch, rows, calls)
    est = asyncio.run(pricing.estimate(_cost(), "eastus", {"appServiceSku": "B1"}))
    comp = est["components"][0]
    assert comp["unitPrice"] == 0.1
    assert comp["monthly"] == 219.0  # 0.10 * 2190 (capacity unchanged)
    assert calls == [1]


def test_region_change_triggers_lookup(monkeypatch):
    calls: list[int] = []
    rows = [{"armSkuName": "P1v3", "skuName": "P1 v3", "meterName": "P1 v3 App", "productName": "App Service Linux", "retailPrice": 0.25}]
    _patch_rows(monkeypatch, rows, calls)
    est = asyncio.run(pricing.estimate(_cost(), "westus2", {}))
    assert est["components"][0]["monthly"] == 547.5  # 0.25 * 2190
    assert calls == [1]
