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
import pytest


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


def test_retail_cache_expires_and_deployment_can_bypass_it(monkeypatch):
    import httpx
    calls = []
    monkeypatch.setattr(pricing, "_price_cache", {})
    monkeypatch.setattr(pricing, "_price_cached_at", {})
    async def run():
        def response(request):
            calls.append(request.url)
            return httpx.Response(200, json={"Items": [{"retailPrice": len(calls)}], "NextPageLink": None})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            first = await pricing._fetch_rows(client, "eastus", "USD", "Compute", None)
            second = await pricing._fetch_rows(client, "eastus", "USD", "Compute", None)
            assert first == second and len(calls) == 1
            for key in pricing._price_cached_at:
                pricing._price_cached_at[key] -= 301
            await pricing._fetch_rows(client, "eastus", "USD", "Compute", None)
            assert len(calls) == 2
            token = pricing.FRESH_READ.set(True)
            try:
                await pricing._fetch_rows(client, "eastus", "USD", "Compute", None)
                assert len(calls) == 3
            finally:
                pricing.FRESH_READ.reset(token)
    asyncio.run(run())


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


def _replica_cost() -> dict:
    return {"currency": "USD", "region": "eastus2", "components": [
        {"name": "Container Apps CPU", "billing": "usage", "quantityPerMonth": 3942000,
         "quantityParam": "minReplicas", "quantityPerUnit": 1314000, "unitPrice": 0.000024, "unitOfMeasure": "1 Second"},
        {"name": "Container Apps memory", "billing": "usage", "quantityPerMonth": 7884000,
         "quantityParam": "minReplicas", "quantityPerUnit": 2628000, "unitPrice": 0.000003, "unitOfMeasure": "1 GiB Second"},
        {"name": "Log ingestion", "billing": "usage", "quantityPerMonth": 2, "unitPrice": 2.76, "unitOfMeasure": "1 GB"},
    ]}


@pytest.mark.parametrize("replicas", ["0", "1", "3", "5"])
def test_replica_quantity_mapping_scales_only_compute_components(monkeypatch, replicas):
    calls = []
    _patch_rows(monkeypatch, [], calls)
    cost = _replica_cost()
    result = asyncio.run(pricing.estimate(cost, "eastus2", {"minReplicas": replicas}))
    assert result["components"][0]["quantityPerMonth"] == int(replicas) * 1314000
    assert result["components"][1]["quantityPerMonth"] == int(replicas) * 2628000
    assert result["components"][2]["quantityPerMonth"] == 2
    assert result["totalMonthly"] == round(round(int(replicas) * 1314000 * 0.000024, 2) + round(int(replicas) * 2628000 * 0.000003, 2) + 5.52, 2)
    assert result["complete"] and not calls
    assert cost["components"][0]["quantityPerMonth"] == 3942000
    reset = asyncio.run(pricing.estimate(cost, "eastus2", {}))
    assert reset["components"][0]["quantityPerMonth"] == 3942000


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "invalid", True])
def test_invalid_replica_value_does_not_silently_keep_authored_price(value):
    with pytest.raises(ValueError, match="Invalid cost quantity"):
        asyncio.run(pricing.estimate(_replica_cost(), "eastus2", {"minReplicas": value}))


@pytest.mark.parametrize("per_unit", [None, "1314000", -1, float("inf"), True])
def test_invalid_quantity_mapping_fails_closed(per_unit):
    cost = _replica_cost()
    cost["components"][0]["quantityPerUnit"] = per_unit
    with pytest.raises(ValueError, match="Invalid cost quantity"):
        asyncio.run(pricing.estimate(cost, "eastus2", {"minReplicas": "5"}))


def test_pricing_coverage_reports_unmapped_changes_not_unchanged_defaults():
    cost = _replica_cost()
    selected = {"minReplicas": "5", "maxReplicas": "10", "agentModelName": "Different", "workloadName": "demo"}
    assert pricing.unpriced_parameters(cost, selected, {"minReplicas": "3", "maxReplicas": "5", "workloadName": "demo"}) == ["agentModelName", "maxReplicas"]
    assert pricing.unpriced_parameters(cost, {"maxReplicas": "5"}, {"maxReplicas": "5"}) == []


def test_replica_scaling_uses_negotiated_rates_without_scaling_ingestion(monkeypatch):
    import setup_cloud
    cost = _replica_cost()
    cost["profile"] = "Active-usage model; agent charges excluded."
    for index, component in enumerate(cost["components"]):
        component["meterId"] = f"meter-{index}"
    async def sheet(subscription):
        return [{"meterId": component["meterId"], "currencyCode": "USD", "unitOfMeasure": component["unitOfMeasure"], "unitPrice": component["unitPrice"] / 2} for component in cost["components"]]
    monkeypatch.setattr(setup_cloud, "price_sheet", sheet)
    result = asyncio.run(pricing.estimate_for_subscription(cost, "eastus2", {"minReplicas": "5"}, {"subscriptionId": "synthetic", "pricingMode": "negotiated"}))
    assert result["pricingSource"] == "negotiated"
    assert result["components"][0]["quantityPerMonth"] == 6570000
    assert result["components"][1]["quantityPerMonth"] == 13140000
    assert result["components"][2]["quantityPerMonth"] == 2
    assert result["profile"] == cost["profile"]


def test_saved_replica_selection_updates_region_price_with_defaults_and_coverage(monkeypatch):
    import catalog_processing
    import setup_cloud
    cost = _replica_cost()
    item = {"cost": cost, "parameters": [{"name": "minReplicas", "type": "int", "default": 3}, {"name": "maxReplicas", "type": "int", "default": 5}]}
    async def rows(client, region, currency, service, meter, arm=None):
        rates = {component["name"]: component["unitPrice"] for component in cost["components"]}
        return [{"retailPrice": rates[meter], "meterName": meter}]
    for component in cost["components"]:
        component["meterNameExact"] = component["name"]
    async def capacity(*args): return {"status": "unknown", "checks": []}
    monkeypatch.setattr(pricing, "_fetch_rows", rows)
    monkeypatch.setattr(setup_cloud, "workload_capacity", capacity)
    async def check():
        subscription = {"subscriptionId": "synthetic", "pricingMode": "retail"}
        baseline = await catalog_processing.check_region(item, subscription, "eastus2", {})
        changed = await catalog_processing.check_region(item, subscription, "eastus2", {"minReplicas": "5", "maxReplicas": "10"})
        reset = await catalog_processing.check_region(item, subscription, "eastus2", {})
        assert changed["price"]["totalMonthly"] > baseline["price"]["totalMonthly"]
        assert changed["price"]["components"][0]["quantityPerMonth"] == 6570000
        assert changed["price"]["unpricedParameters"] == ["maxReplicas"]
        assert "Not adjusted for: maxReplicas" in changed["priceDetail"]
        assert baseline["price"]["unpricedParameters"] == []
        assert reset["price"]["totalMonthly"] == baseline["price"]["totalMonthly"]
    asyncio.run(check())


def test_cost_display_updates_totals_and_warns_about_unpriced_options():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for cost display tests")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/app.js', 'utf8');
const start = source.indexOf('  function costBlock(');
const end = source.indexOf('\n  function ', start + 10);
assert.ok(start >= 0 && end > start);
function element(tag, props = {}, children = []) {
    return {tag, ...props, textContent:props.text || '', children:(Array.isArray(children)?children:[children]).filter(Boolean),
        classList:{add(){},remove(){}}, appendChild(child){this.children.push(child);},
        set innerHTML(value){this.children=[];}, setAttribute(key,value){this[key]=value;}};
}
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
const context = {el:element,fmtMoney(value,currency){return `${currency} ${value.toFixed(2)}`;}};
vm.runInNewContext(source.slice(start,end),context);
const baseline={currency:'USD',totalMonthly:123.78,components:[{name:'CPU',quantityPerMonth:3942000,monthly:94.61}]};
const control={}; const view=context.costBlock({cost:baseline},control);
assert.match(text(view),/USD 123.78/);
control.apply({...baseline,totalMonthly:202.62,profile:'Agent charges excluded.',unpricedParameters:['maxReplicas'],components:[{name:'CPU',quantityPerMonth:6570000,monthly:157.68}]},{forSelection:true});
assert.match(text(view),/USD 202.62/);
assert.match(text(view),/6570000/);
assert.match(text(view),/Not adjusted for: maxReplicas/);
assert.match(text(view),/some options not priced/);
assert.match(text(view),/Agent charges excluded/);
assert.doesNotMatch(text(view),/estimated live for your current selections/);
control.reset();
assert.match(text(view),/USD 123.78/);
assert.doesNotMatch(text(view),/maxReplicas|6570000/);
"""
    result = subprocess.run([node, "-e", script], cwd=SERVER_DIR.parent.parent, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_advanced_save_request_reprices_replicas_and_reports_unmapped_changes(monkeypatch):
    import api
    import db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import CurrentUser, require_user
    db.init_db()
    subscription = "11111111-1111-4111-8111-111111111111"
    db.set_setup_setting("subscriptions", [{"subscriptionId": subscription, "pricingMode": "retail"}])
    cost = _replica_cost()
    item = {"path": "example", "cost": cost, "parameters": [{"name": "minReplicas", "type": "int", "default": 3}, {"name": "maxReplicas", "type": "int", "default": 5}]}
    for component in cost["components"]:
        component["meterNameExact"] = component["name"]
    async def rows(client, region, currency, service, meter, arm=None):
        component = next(entry for entry in cost["components"] if entry["name"] == meter)
        return [{"retailPrice": component["unitPrice"], "meterName": meter}]
    async def locations(identifier):
        assert identifier == subscription
        return [{"name": "eastus2"}]
    async def capacity(*args): return {"status": "unknown", "checks": []}
    monkeypatch.setattr(pricing, "_fetch_rows", rows)
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": item})
    monkeypatch.setattr(api.setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(api.setup_cloud, "workload_capacity", capacity)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "", "User", "tenant", "guest")
    client = TestClient(application)
    payload = {"useCase": "example", "subscriptionId": subscription, "location": "eastus2"}
    headers = {"Origin": "http://testserver"}
    baseline = client.post("/api/regions/check", headers=headers, json=payload).json()["price"]
    changed = client.post("/api/regions/check", headers=headers, json={**payload, "parameterValues": {"minReplicas": "5", "maxReplicas": "10"}})
    assert changed.status_code == 200
    assert baseline["totalMonthly"] == 123.78
    assert changed.json()["price"]["totalMonthly"] == 202.62
    assert changed.json()["price"]["unpricedParameters"] == ["maxReplicas"]
    reset = client.post("/api/regions/check", headers=headers, json=payload).json()["price"]
    assert reset["totalMonthly"] == baseline["totalMonthly"] and not reset["unpricedParameters"]
    for component in cost["components"]:
        component.pop("quantityParam", None)
        component.pop("quantityPerUnit", None)
    unmapped = client.post("/api/regions/check", headers=headers, json={**payload, "parameterValues": {"minReplicas": "5"}}).json()
    assert unmapped["price"]["totalMonthly"] == baseline["totalMonthly"]
    assert unmapped["price"]["unpricedParameters"] == ["minReplicas"]
    assert "Not adjusted for: minReplicas" in unmapped["priceDetail"]
    assert db.list_deployments() == []


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


@pytest.mark.parametrize("unit", [0.000024, 0.000003, 0.00000036])
def test_retail_unit_rates_keep_precision_until_monthly_total(unit):
    assert pricing._pick_unit_price([{"retailPrice": unit}], {}, None) == unit


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
