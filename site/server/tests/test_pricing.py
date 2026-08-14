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


def test_odata_escapes_single_quotes():
    assert pricing._odata("a'b") == "a''b"
    assert pricing._odata("Standard_D4s_v5") == "Standard_D4s_v5"


def _sql_cost() -> dict:
    return {
        "currency": "USD",
        "region": "eastus",
        "components": [{
            "name": "Azure SQL",
            "billing": "fixed",
            "serviceName": "SQL Database",
            "productNameContains": "General Purpose - Compute Gen5",
            "meterNameContains": "vCore",
            "meterNameExact": "vCore",
            "quantityPerMonth": 730,
            "hoursPerInstance": 730,
            "skuQuantityParam": "sqlSkuName",
            "skuQuantityMap": {"GP_S_Gen5_1": 1, "GP_Gen5_8": 8, "BC_Gen5_4": 4},
            "skuPriceMultiplierParam": "sqlSkuName",
            "skuPriceMultiplierMap": {"BC_Gen5_4": 2},
            "unitPrice": 0.15,
            "monthly": 109.5,
        }],
    }


def test_sql_vcore_quantity_scales_without_lookup(monkeypatch):
    calls: list[int] = []
    _patch_rows(monkeypatch, [], calls)
    est = asyncio.run(pricing.estimate(_sql_cost(), "eastus", {"sqlSkuName": "GP_Gen5_8"}))
    comp = est["components"][0]
    assert comp["quantityPerMonth"] == 5840  # 8 vCores * 730
    assert comp["monthly"] == 876.0  # 0.15 * 5840
    assert calls == []  # skuQuantity reuses the cached unit price


def test_sql_business_critical_applies_multiplier(monkeypatch):
    calls: list[int] = []
    _patch_rows(monkeypatch, [], calls)
    est = asyncio.run(pricing.estimate(_sql_cost(), "eastus", {"sqlSkuName": "BC_Gen5_4"}))
    comp = est["components"][0]
    assert comp["quantityPerMonth"] == 2920  # 4 vCores * 730
    assert round(comp["unitPrice"], 4) == 0.30  # 0.15 * 2 (Business Critical)
    assert comp["monthly"] == 876.0  # 0.30 * 2920
    assert calls == []


def test_malformed_multiplier_does_not_crash(monkeypatch):
    calls: list[int] = []
    _patch_rows(monkeypatch, [], calls)
    cost = _sql_cost()
    cost["components"][0]["skuPriceMultiplierMap"] = {"BC_Gen5_4": "oops"}
    est = asyncio.run(pricing.estimate(cost, "eastus", {"sqlSkuName": "BC_Gen5_4"}))
    comp = est["components"][0]
    assert comp["unitPrice"] == 0.15  # bad multiplier skipped, base rate kept
    assert comp["monthly"] == 438.0  # 0.15 * 2920
    assert est["complete"] is True
