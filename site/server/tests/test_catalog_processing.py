"""Offline processing, runner selection and sandbox regression checks."""

import asyncio
import time
from datetime import datetime, timezone

import httpx
import pytest

import catalog_processing as processing


@pytest.fixture
def configured(tmp_path, monkeypatch):
    import db
    import setup_github
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "processing.db"))
    monkeypatch.setattr(db, "_CONN", None)
    monkeypatch.setattr(setup_github, "_SYNC_LOCK", asyncio.Lock())
    monkeypatch.setattr(setup_github, "_SYNC_WAKE", asyncio.Event())
    connection = db.init_db()
    yield
    connection.close()


@pytest.mark.parametrize("case", ["disabled", "denied", "empty", "hosted", "failed", "self-hosted"])
def test_runner_probe_fails_closed_without_dispatching(case):
    calls = []

    async def request(path, **kwargs):
        calls.append(path)
        if path.endswith("/permissions"):
            if case == "denied":
                raise httpx.ConnectError("private")
            return {"enabled": case != "disabled"}
        if path.endswith("/runners"):
            return {"runners": [{"os": "linux", "status": "online", "busy": True,
                                  "labels": [{"name": label} for label in ["self-hosted", "Linux", "X64", "portal"]]}] if case == "self-hosted" else []}
        if path.endswith("/runs"):
            return {"workflow_runs": [{"id": 1, "created_at": datetime.now(timezone.utc).isoformat()}] if case in ("hosted", "failed") else []}
        return {"jobs": [{"conclusion": "success" if case == "hosted" else "failure", "runner_id": 1 if case == "hosted" else 0,
                          "runner_group_name": "GitHub Actions", "labels": ["ubuntu-latest"]}]}

    result = asyncio.run(processing.probe_runners("organization/portal", request))
    assert result["github-hosted"]["available"] == (case == "hosted")
    assert result["self-hosted"]["available"] == (case == "self-hosted")
    if case == "denied":
        assert result["github-hosted"]["state"] == "unverified"
        assert "private" not in str(result)
    assert all("dispatch" not in path for path in calls)


def test_runner_selection_requires_exact_verified_labels():
    probe = {"self-hosted": {"available": True, "candidates": [{"labels": ["self-hosted", "Linux", "X64", "portal"]}]}}
    assert not processing.runner_available({"mode": "self-hosted", "runnerLabels": ["self-hosted"]}, probe)
    assert processing.runner_available({"mode": "self-hosted", "runnerLabels": ["self-hosted", "Linux", "X64", "portal"]}, probe)
    with pytest.raises(ValueError):
        processing.ProcessingSettings(runnerLabels=["${{ secrets.TOKEN }}"])


def test_container_builds_bicep_and_validates_terraform_without_cloud_credentials(monkeypatch):
    import json
    calls = []
    async def compiler(work, tool, arguments):
        calls.append((tool, arguments))
        assert (work / "catalog/example/main.bicep").exists()
        return json.dumps({"resources": []}) if tool == "bicep" else ""
    monkeypatch.setattr(processing, "compile_source", compiler)
    blobs = {"catalog/example/metadata.json": b'{"displayName":"Example"}',
             "catalog/example/main.bicep": b"param location string", "catalog/example/terraform/main.tf": b"terraform {}"}
    result = asyncio.run(processing.process_blobs(blobs))
    assert json.loads(result["catalog/example/compiled/dev.json"])["template"] == {"resources": []}
    assert json.loads(result["catalog/example/metadata.json"])["validation"]["checks"] == ["bicep-compiled", "terraform-syntax"]
    assert calls[0][1][-1] == "--no-restore"
    assert calls[1][1][:2] == ["fmt", "-write=false"]


def test_organization_region_picker_matches_capacity_breakdown_without_internal_snapshots():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for region picker tests")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/app.js', 'utf8');
const start = source.indexOf('  function deployRegionField(');
const end = source.indexOf('  function deployBlock(', start);
assert.ok(start >= 0 && end > start);
function element(tag, props = {}, children = []) {
    return {tag, ...props, textContent:props.text || '', listeners:{}, children: (Array.isArray(children) ? children : [children]).filter(Boolean),
        get options(){return this.children;}, getAttribute(key){return this[key];}, addEventListener(event,handler){this.listeners[event]=handler;},
        setAttribute(key, value){this[key] = value;}, appendChild(child){this.children.push(child);},
        replaceChildren(...items){this.children = items;}};
}
function text(node){return [node.text || '', ...node.children.map(text)].join(' ');}
const context = {el: element, state: {me: {customerOperated: true}}, capacityRegions(){throw Error('Internal region data must not be read');}};
vm.runInNewContext(source.slice(start, end), context);
const picker = context.deployRegionField({path: 'example'});
assert.equal(picker.select.disabled, true);
picker.select.appendChild(element('option', {value:'eastus', text:'East US'}));
picker.select.appendChild(element('option', {value:'westus', text:'West US'}));
picker.select.value = 'eastus';
picker.updateCapacity({status:'headroom', checks:[{service:'Compute',status:'headroom',detail:'Quota available'}],
    quotas:[{name:'Regional vCPUs',available:6,current:4,limit:10}]});
assert.equal(picker.select['data-status'], 'green');
assert.match(text(picker.note), /Reported quota headroom in eastus/);
assert.match(text(picker.note), /Regional vCPUs: 6 free \(4 used \/ 10 limit\)/);
assert.match(text(picker.note), /do not guarantee/);
picker.select.listeners.focus();
assert.match(picker.select.options[1].textContent, /East US.*Reported quota headroom/);
assert.match(picker.select.options[2].textContent, /West US.*Not verified/);
picker.select.listeners.blur();
assert.equal(picker.select.options[1].textContent, 'East US');
picker.updateCapacity({status:'offered', checks:[{service:'<img src=x>',status:'offered',detail:'Available service'}]});
assert.equal(picker.select['data-status'], 'yellow');
assert.match(text(picker.note), /<img src=x>/);
assert.ok(!('html' in picker.note));
picker.updateCapacity({status:'unavailable'});
assert.equal(picker.select['data-status'], 'red');
picker.updateCapacity(null, 'Checking selected region...');
assert.equal(picker.select['data-status'], 'unknown');
assert.doesNotMatch(text(picker.note), /Quota available|Regional vCPUs/);
assert.match(text(picker.note), /Checking selected region/);
picker.refreshAll();
assert.ok(picker.select.options.every(option => option['data-status']==='unknown'));
"""
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_container_validation_failure_leaves_inputs_unchanged(monkeypatch):
    async def compiler(*args):
        raise ValueError("Invalid source")
    monkeypatch.setattr(processing, "compile_source", compiler)
    blobs = {"catalog/example/metadata.json": b'{"displayName":"Example"}', "catalog/example/main.bicep": b"invalid"}
    before = dict(blobs)
    with pytest.raises(ValueError):
        asyncio.run(processing.process_blobs(blobs))
    assert blobs == before


def test_container_capacity_uses_verified_subscriptions_and_read_only_arm(monkeypatch):
    identifier = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(processing.setup_config, "subscriptions", lambda: [{"subscriptionId": identifier}])
    calls = []
    async def verify(value):
        assert value["subscriptionId"] == identifier
    async def arm(path):
        calls.append(path)
        return {"value": [{"name": {"value": "cores"}, "limit": 20, "currentValue": 3}]}
    monkeypatch.setattr(processing.setup_cloud, "verify_subscription", verify)
    monkeypatch.setattr(processing.setup_cloud, "arm_request", arm)
    result = asyncio.run(processing.refresh_capacity(["eastus"]))
    assert result["subscriptions"][identifier]["eastus"][0]["available"] == 17
    assert result["physicalCapacity"] == "unknown"
    assert len(calls) == 1


def test_capacity_discovers_all_regions_for_each_saved_subscription(monkeypatch):
    import httpx
    identifiers = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    monkeypatch.setattr(processing.setup_config, "subscriptions", lambda: [{"subscriptionId": identifier} for identifier in identifiers])
    calls = []
    async def verify(item):
        assert item["subscriptionId"] in identifiers
    async def locations(identifier):
        return [{"name": f"region{index}"} for index in range(60)] if identifier == identifiers[0] else [{"name": f"other{index}"} for index in range(60)]
    async def arm(path):
        calls.append(path)
        if "/region0/" in path:
            raise httpx.ConnectError("Synthetic region failure")
        return {"value": [{"name": {"value": "cores"}, "limit": 10, "currentValue": 2}]}
    monkeypatch.setattr(processing.setup_cloud, "verify_subscription", verify)
    monkeypatch.setattr(processing.setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(processing.setup_cloud, "arm_request", arm)
    result = asyncio.run(processing.refresh_capacity([]))
    assert len(calls) == 120
    assert len(result["subscriptions"][identifiers[0]]) == 60
    assert len(result["subscriptions"][identifiers[1]]) == 60
    assert result["subscriptions"][identifiers[0]]["region0"] == []
    assert result["subscriptions"][identifiers[1]]["other59"][0]["available"] == 8


def test_previous_region_selection_does_not_limit_automatic_discovery(configured):
    import db
    db.set_setup_setting("catalog_processing", {"mode": "container", "capacityRegions": ["eastus"]})
    assert processing.settings()["capacityRegions"] == []


def test_scheduled_regions_refresh_daily_resume_and_filter_subscriptions(configured, monkeypatch):
    import api
    import db
    import json
    now = {"value": 1_900_000_000.0}
    monkeypatch.setattr(processing.time, "time", lambda: now["value"])
    subscription = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "pricingMode": "retail"}
    item = {"path": "example", "parameters": [{"name": "sku", "type": "string", "default": "B1"}]}
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": item})
    monkeypatch.setattr(processing.setup_config, "subscriptions", lambda: [subscription])
    calls = []
    async def verify(selected):
        assert selected == subscription
    async def locations(identifier):
        return [{"name": "eastus"}, {"name": "westus"}]
    async def check(workload, selected, region, parameters):
        assert parameters == {"sku": "B1"}
        calls.append(region)
        return {"subscriptionId": selected["subscriptionId"], "location": region, "checkedAt": now["value"],
                "price": {"complete": True, "totalMonthly": 15, "currency": "USD", "pricingSource": "retail"},
                "priceDetail": "", "capacity": {"status": "offered", "checks": [], "quotas": [], "physicalCapacity": "unknown"}}
    monkeypatch.setattr(processing.setup_cloud, "verify_subscription", verify)
    monkeypatch.setattr(processing.setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(processing, "check_region", check)
    asyncio.run(processing.refresh_regional_schedule())
    assert calls == ["eastus", "westus"]
    assert db.get_setup_setting("regional_job")["status"] == "succeeded"
    asyncio.run(processing.refresh_regional_schedule())
    assert len(calls) == 2
    db.set_setup_setting("regional_job", {"status": "interrupted"})
    asyncio.run(processing.refresh_regional_schedule())
    assert len(calls) == 2
    now["value"] += 86401
    assert all(row["stale"] for row in processing.regional_results([item], [subscription])["rows"])
    asyncio.run(processing.refresh_regional_schedule())
    assert len(calls) == 4
    assert processing.regional_results([item], [])["rows"] == []
    changed = json.loads(json.dumps(item))
    changed["parameters"][0]["default"] = "P1v3"
    assert processing.regional_results([changed], [subscription])["rows"] == []


def test_regional_scheduler_runs_without_a_manual_trigger(configured, monkeypatch):
    calls = []
    async def run():
        stop = asyncio.Event()
        async def refresh():
            calls.append("automatic")
            stop.set()
        monkeypatch.setattr(processing, "refresh_regional_schedule", refresh)
        await processing.run_regional_schedule(stop)
    asyncio.run(run())
    assert calls == ["automatic"]


@pytest.mark.parametrize("available", [True, False])
def test_regional_schedule_uses_runners_or_container_fallback(configured, monkeypatch, available):
    import api
    import db
    import setup_github
    db.set_setup_setting("catalog_processing", {"mode": "github-hosted"})
    monkeypatch.setattr(api, "_catalog_items", lambda: {"example": {"path": "example"}})
    monkeypatch.setattr(processing.setup_config, "subscriptions", lambda: [{"subscriptionId": "11111111-1111-4111-8111-111111111111"}])
    called = []
    async def probe():
        return {"github-hosted": {"available": available}}
    async def collect(items, subscriptions, previous, on_batch):
        called.append("container")
        value = {"rows": [], "errors": {}}
        on_batch(value)
        return value
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    monkeypatch.setattr(processing, "collect_regional_snapshot", collect)
    asyncio.run(processing.refresh_regional_schedule())
    assert called == ([] if available else ["container"])
    assert db.get_setup_setting("regional_job")["status"] == ("runner" if available else "succeeded")


def test_failed_regional_refresh_preserves_last_success(configured, monkeypatch):
    subscription = {"subscriptionId": "11111111-1111-4111-8111-111111111111"}
    item = {"path": "example"}
    key = processing.regional_input_key(item, subscription)
    previous = {"rows": [{"subscriptionId": subscription["subscriptionId"], "useCase": "example", "location": "eastus",
                           "inputKey": key, "checkedAt": 1.0, "price": {"complete": True}, "capacity": {"status": "headroom"}}]}
    async def verify(selected): pass
    async def locations(identifier): return [{"name": "eastus"}]
    async def check(*args):
        return {"subscriptionId": subscription["subscriptionId"], "location": "eastus", "checkedAt": processing.time.time(),
                "price": None, "priceDetail": "Unavailable", "capacity": {"status": "unknown"}}
    monkeypatch.setattr(processing.setup_cloud, "verify_subscription", verify)
    monkeypatch.setattr(processing.setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(processing, "check_region", check)
    result = asyncio.run(processing.collect_regional_snapshot([item], [subscription], previous))
    assert result["rows"][0]["price"] is None
    assert result["rows"][0]["lastSuccessful"]["checkedAt"] == 1.0


def test_live_region_check_bypasses_caches_and_restores_context(monkeypatch):
    async def price(cost, region, parameters, subscription):
        assert processing.pricing.FRESH_READ.get()
        return {"complete": True, "totalMonthly": 10}
    async def capacity(identifier, region, item, parameters):
        assert processing.setup_cloud.FRESH_READ.get()
        return {"status": "unknown"}
    monkeypatch.setattr(processing.pricing, "estimate_for_subscription", price)
    monkeypatch.setattr(processing.setup_cloud, "workload_capacity", capacity)
    async def run():
        result = await processing.check_region({"cost": {"components": [{}]}}, {"subscriptionId": "example"}, "eastus", {}, fresh=True)
        assert result["price"]["totalMonthly"] == 10
        assert not processing.pricing.FRESH_READ.get()
        assert not processing.setup_cloud.FRESH_READ.get()
    asyncio.run(run())


def test_runner_snapshot_binding_retains_retail_label_for_negotiated_subscription(configured):
    import db
    subscription = {"subscriptionId": "11111111-1111-4111-8111-111111111111", "pricingMode": "retail"}
    item = {"path": "example"}
    row = {"subscriptionId": subscription["subscriptionId"], "useCase": "example", "location": "eastus", "checkedAt": processing.time.time(),
           "inputKey": processing.regional_input_key(item, subscription),
           "price": {"totalMonthly": 10, "complete": True, "currency": "USD", "pricingSource": "retail"},
           "capacity": {"status": "unknown", "checks": [], "quotas": [], "physicalCapacity": "unknown"}}
    snapshot = {"schemaVersion": 1, "source": "runner", "rows": [row]}
    db.set_setup_setting("regional_snapshot", processing.validate_runner_snapshot(snapshot))
    result = processing.regional_results([item], [{**subscription, "pricingMode": "negotiated"}])
    assert result["rows"][0]["price"]["pricingSource"] == "retail"
    assert "public retail" in result["rows"][0]["price"]["fallbackReason"]
    assert processing.regional_results([item], [])["rows"] == []
    row["price"]["pricingSource"] = "negotiated"
    with pytest.raises(ValueError, match="public retail"):
        processing.validate_runner_snapshot(snapshot)


def test_optional_workflows_target_selected_runners_and_only_reviewed_branches():
    import json
    import catalog_workflows
    for labels in (None, ["self-hosted", "Linux", "X64", "portal"]):
        files = catalog_workflows.workflow_files(labels)
        for content in files.values():
            workflow = json.loads(content)
            assert "pull_request" not in workflow["on"]
            assert workflow["on"]["schedule"]
            if "capacity" in workflow["name"]:
                assert any("site/requirements.txt" in step.get("run", "") for step in workflow["jobs"]["refresh"]["steps"])
            for job in workflow["jobs"].values():
                assert job["runs-on"] == (labels or ["ubuntu-latest"])
                assert job["timeout-minutes"] <= 20


@pytest.mark.parametrize("mode", ["container", "github-hosted", "self-hosted"])
def test_sync_falls_back_to_container_and_preserves_last_good(configured, monkeypatch, mode):
    import base64
    import json
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    db.set_setup_setting("catalog_processing", {"mode": mode, "capacityRegions": ["eastus"]})
    blob_data = {"meta": b'{"displayName":"Example"}', "source": b"param location string"}
    calls = []
    async def request(path, **kwargs):
        calls.append(path)
        if "/git/ref/" in path: return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path: return {"tree": {"sha": "root"}}
        if path.endswith("/root"): return {"tree": [{"path": "catalog", "sha": "catalog", "type": "tree"}]}
        if path.endswith("/catalog"): return {"tree": [{"path": "example", "sha": "example", "type": "tree"}]}
        if path.endswith("/example"): return {"tree": [{"path": name, "sha": sha, "type": "blob", "mode": "100644"} for name, sha in [("metadata.json", "meta"), ("main.bicep", "source")]]}
        return {"content": base64.b64encode(blob_data[path.rsplit("/", 1)[1]]).decode()}
    async def probe(**kwargs):
        return {"github-hosted": {"available": False}, "self-hosted": {"available": False}}
    async def compiler(*args):
        return json.dumps({"resources": []})
    async def capacity(regions):
        assert regions == []
        return None
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    monkeypatch.setattr(processing, "compile_source", compiler)
    monkeypatch.setattr(processing, "refresh_capacity", capacity)
    snapshot = asyncio.run(setup_github.sync_catalog())
    assert snapshot["processingMode"] == "container"
    assert snapshot["sourceCommit"] == "a" * 40
    assert snapshot["items"][0]["deployReady"] is True
    assert db.get_setup_setting("processing_effective")["fallback"] == (mode != "container")
    compiled_path = setup_github.snapshot_root(snapshot["version"]) / "_templates/example/dev.json"
    before = compiled_path.read_bytes()
    async def broken(*args):
        raise ValueError("invalid source")
    monkeypatch.setattr(processing, "compile_source", broken)
    with pytest.raises(ValueError):
        asyncio.run(setup_github.sync_catalog(force=True))
    assert setup_github.catalog_snapshot() == snapshot
    assert compiled_path.read_bytes() == before


@pytest.mark.parametrize("failure", [False, True])
def test_background_job_recovers_interrupted_work_and_retains_snapshot(configured, monkeypatch, failure):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    db.set_setup_setting("catalog_job", {"status": "running"})
    db.set_setup_setting("catalog_snapshot", {"previous": True})
    async def run():
        stop = asyncio.Event()
        async def sync(force=False):
            assert force is True
            assert db.get_setup_setting("catalog_job")["status"] == "running"
            stop.set()
            if failure:
                raise ValueError("private compiler error")
            return {"version": "a" * 40, "items": [], "processingMode": "container"}
        monkeypatch.setattr(setup_github, "sync_catalog", sync)
        await setup_github.run_catalog_sync(stop)
    asyncio.run(run())
    job = db.get_setup_setting("catalog_job")
    assert job["status"] == ("failed" if failure else "succeeded")
    assert "private" not in str(job)
    assert db.get_setup_setting("catalog_snapshot") == {"previous": True}


@pytest.mark.parametrize("kind,category", [("http", "http_error"), ("timeout", "timeout"),
    ("storage", "storage_error"), ("connection", "connection_error"), ("value", "invalid_response")])
def test_processing_failure_retains_stage_without_private_details(configured, monkeypatch, kind, category):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    db.set_setup_setting("catalog_snapshot", {"items": [{"path": "previous"}]})
    errors = {
        "http": httpx.HTTPStatusError("secret upstream response", request=httpx.Request("GET", "https://private.test/secret"), response=httpx.Response(403)),
        "timeout": TimeoutError("secret"), "storage": PermissionError("secret path"),
        "connection": httpx.ConnectError("secret host"), "value": ValueError("secret data"),
    }
    async def run():
        stop = asyncio.Event()
        async def sync(force=False):
            setup_github._catalog_stage("capacity")
            stop.set()
            raise errors[kind]
        monkeypatch.setattr(setup_github, "sync_catalog", sync)
        await setup_github.run_catalog_sync(stop)
    asyncio.run(run())
    job = db.get_setup_setting("catalog_job")
    assert job["stage"] == "capacity" and job["code"] == category
    assert job["startedAt"] is not None
    assert job["httpStatus"] == (403 if kind == "http" else None)
    assert "secret" not in str(job) and "private.test" not in str(job)
    assert db.get_setup_setting("catalog_snapshot")["items"] == [{"path": "previous"}]


@pytest.mark.parametrize("credential", ["missing", "expired", "unresolved", "malformed_expiry"])
def test_github_unusable_credentials_require_reconnection_without_requests(configured, monkeypatch, credential):
    import setup_github
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "" if credential == "missing" else "@Microsoft.KeyVault(SecretUri=private)" if credential == "unresolved" else "test-only-token")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "invalid" if credential == "malformed_expiry" else "1" if credential == "expired" else "0")
    def client(**kwargs):
        pytest.fail("Unavailable credentials must not send a GitHub request")
    monkeypatch.setattr(setup_github.httpx, "AsyncClient", client)
    with pytest.raises(setup_github.GitHubConnectionError, match="Reconnect GitHub"):
        asyncio.run(setup_github.github_request("/user"))


@pytest.mark.parametrize("status", [401, 403])
def test_github_rejected_credentials_distinguished_from_permission_errors(configured, monkeypatch, status):
    import db
    import setup_github
    client = httpx.AsyncClient
    monkeypatch.setattr(setup_github.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"message": "private secret"})), **kwargs))
    expected = setup_github.GitHubConnectionError if status == 401 else httpx.HTTPStatusError
    with pytest.raises(expected) as error:
        asyncio.run(setup_github.github_request("/user", token="test-only-token"))
    db.set_setup_setting("github", {"enabled": True})
    db.set_setup_setting("catalog_job", {"status": "running", "stage": "repository"})
    failure = setup_github._catalog_failure(error.value)
    assert failure["code"] == ("github_reconnect_required" if status == 401 else "http_error")
    assert "private" not in failure["detail"] and "secret" not in failure["detail"]
    db.set_setup_setting("catalog_job", failure)
    public = setup_github.catalog_refresh_status()
    assert public.get("code") == ("github_reconnect_required" if status == 401 else None)
    assert "detail" not in public and "stage" not in public and "httpStatus" not in public
    db.set_setup_setting("catalog_job", {"status": "running", "code": "github_reconnect_required"})
    assert "code" not in setup_github.catalog_refresh_status()


def test_processing_failure_exposes_only_allowlisted_validation_messages(configured):
    import db
    import setup_github
    db.set_setup_setting("catalog_job", {"status": "running", "stage": "validation_pricing", "startedAt": 100})
    detail = setup_github._catalog_failure(ValueError("Retail prices are incomplete; the previous catalog remains active"))["detail"]
    assert "Retail prices are incomplete" in detail
    assert "private suffix" not in setup_github._catalog_failure(ValueError("Retail prices are incomplete; the previous catalog remains active private suffix"))["detail"]
    db.set_setup_setting("catalog_job", {"stage": "private source"})
    assert "private source" not in str(setup_github._catalog_failure(ValueError("private error")))


def test_compiler_failure_uses_only_sanitized_sarif_codes(tmp_path, monkeypatch):
    import json
    async def create(*arguments, **options):
        assert arguments[-2:] == ("--diagnostics-format", "sarif")
        options["stderr"].write(json.dumps({"runs": [{"results": [
            {"level": "error", "ruleId": "BCP091", "message": {"text": "private source and secret"}},
            {"level": "error", "ruleId": "private secret"},
            {"level": "warning", "ruleId": "BCP062"},
        ]}]}).encode())
        class Process:
            returncode = 1
            async def wait(self): return 1
        return Process()
    monkeypatch.setattr(processing.asyncio, "create_subprocess_exec", create)
    with pytest.raises(processing.CatalogValidationError) as error:
        asyncio.run(processing.compile_source(tmp_path, "bicep", ["build", "catalog/example/main.bicep", "--stdout", "--no-restore"]))
    assert "BCP091" in str(error.value)
    assert "catalog/example/main.bicep" in str(error.value)
    assert "required local modules" in str(error.value)
    assert "private" not in str(error.value) and "BCP062" not in str(error.value)
    assert processing._bicep_error_codes(b"raw private stderr") == []
    assert processing._bicep_error_codes(b"{}" * 600000) == []
    assert processing._bicep_error_codes(b'{"runs":[null,{"results":{}}]}') == []
    assert "/secret" not in str(processing.CatalogValidationError("unknown", "/secret/token", ["injected"]))


def test_background_job_reports_missing_module_validation(configured, monkeypatch):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    db.set_setup_setting("catalog_snapshot", {"items": [{"path": "previous"}]})
    async def run():
        stop = asyncio.Event()
        async def sync(force=False):
            stop.set()
            raise processing.CatalogValidationError("bicep", "catalog/example/main.bicep", ["BCP091"])
        monkeypatch.setattr(setup_github, "sync_catalog", sync)
        await setup_github.run_catalog_sync(stop)
    asyncio.run(run())
    job = db.get_setup_setting("catalog_job")
    assert job["code"] == "catalog_validation_failed"
    assert "BCP091" in job["detail"] and "required local modules" in job["detail"]
    assert db.get_setup_setting("github_sync_error") == job["detail"]
    assert db.get_setup_setting("catalog_snapshot")["items"] == [{"path": "previous"}]


def test_page_refresh_coalesces_checks_and_throttles_completed_work(configured, monkeypatch):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    monkeypatch.setattr(setup_github.time, "time", lambda: 1000)
    queued = setup_github.queue_catalog_refresh(force=False)
    assert queued["force"] is False
    assert setup_github._SYNC_WAKE.is_set()
    assert setup_github.queue_catalog_refresh(force=False) == queued
    assert setup_github.queue_catalog_refresh()["force"] is True
    db.set_setup_setting("catalog_job", {"status": "running", "startedAt": 1000})
    assert setup_github.queue_catalog_refresh(force=False)["status"] == "running"
    db.set_setup_setting("catalog_job", {"status": "succeeded", "completedAt": 995})
    assert setup_github.queue_catalog_refresh(force=False) == {"status": "throttled", "retryAfter": 5}
    assert db.get_setup_setting("catalog_job")["status"] == "succeeded"
    monkeypatch.setattr(setup_github.time, "time", lambda: 1005)
    assert setup_github.queue_catalog_refresh(force=False)["status"] == "queued"


def test_page_refresh_wakes_scheduler_without_forcing_rebuild(configured, monkeypatch):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    setup_github.queue_catalog_refresh(force=False)
    async def run():
        stop = asyncio.Event()
        async def sync(force=False):
            assert force is False
            stop.set()
            return {"version": "a" * 40, "items": []}
        monkeypatch.setattr(setup_github, "sync_catalog", sync)
        await setup_github.run_catalog_sync(stop)
    asyncio.run(run())
    assert db.get_setup_setting("catalog_job")["status"] == "succeeded"


def test_page_refresh_during_running_sync_gets_one_followup_check(configured, monkeypatch):
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True})
    calls = []
    async def run():
        stop = asyncio.Event()
        async def sync(force=False):
            calls.append(force)
            if len(calls) == 1:
                for _request in range(3):
                    assert setup_github.queue_catalog_refresh(force=False)["status"] == "running"
            else:
                stop.set()
            return {"version": "a" * 40, "items": []}
        monkeypatch.setattr(setup_github, "sync_catalog", sync)
        await asyncio.wait_for(setup_github.run_catalog_sync(stop), timeout=2)
    asyncio.run(run())
    assert calls == [False, False]
    assert db.get_setup_setting("catalog_job")["status"] == "succeeded"


def test_page_refresh_unchanged_commit_does_not_rebuild(configured, monkeypatch):
    import json
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    snapshot = {"sourceCommit": "a" * 40, "version": "b" * 40, "repository": "organization/portal",
                "items": [], "processingKey": json.dumps(["container", [], [], "template-parameters-v1"]), "syncedAt": time.time()}
    db.set_setup_setting("catalog_snapshot", snapshot)
    calls = []
    async def request(path, **kwargs):
        calls.append(path)
        assert path == "/repos/organization/portal/git/ref/heads/main"
        return {"object": {"sha": "a" * 40}}
    monkeypatch.setattr(setup_github, "github_request", request)
    assert asyncio.run(setup_github.sync_catalog(force=False)) == snapshot
    assert len(calls) == 1


def test_catalog_rebuilds_old_parameter_metadata_on_unchanged_commit(configured, monkeypatch):
    import json
    import db
    import setup_github
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    db.set_setup_setting("catalog_snapshot", {"sourceCommit": "a" * 40, "version": "b" * 40, "repository": "organization/portal",
        "items": [], "processingKey": json.dumps(["container", [], []]), "syncedAt": time.time()})
    calls = []
    async def request(path, **kwargs):
        calls.append(path)
        if "/git/ref/" in path:
            return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        return {"tree": []}
    monkeypatch.setattr(setup_github, "github_request", request)
    result = asyncio.run(setup_github.sync_catalog(force=False))
    assert len(calls) == 3
    assert json.loads(result["processingKey"])[-1] == "template-parameters-v1"


def test_catalog_refresh_endpoints_allow_guests_require_origin_and_hide_details(configured, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api
    import db
    from identity import CurrentUser, require_user
    application = FastAPI()
    application.include_router(api.build_api_router())
    client = TestClient(application)
    path = "/api/catalog/sync"
    assert client.get(path).status_code in (401, 403)
    assert client.post(path).status_code in (401, 403)
    application.dependency_overrides[require_user] = lambda: CurrentUser("guest", "", "", "", "guest")
    monkeypatch.setattr(api, "is_customer_distribution", lambda: True)
    assert client.post(path).status_code == 403
    assert client.post(path, headers={"Origin": "https://elsewhere.test"}).status_code == 403
    headers = {"Origin": "http://testserver"}
    assert client.post(path, headers=headers).json()["status"] == "not_configured"
    db.set_setup_setting("github", {"enabled": True})
    response = client.post(path, headers=headers)
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert db.get_setup_setting("catalog_job")["force"] is False
    assert client.post(path, headers=headers).status_code == 202
    assert client.get(path).json()["status"] == "queued"
    db.set_setup_setting("catalog_job", {"status": "failed", "completedAt": time.time(), "detail": "private failure"})
    response = client.get(path)
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert client.post(path, headers=headers).json()["status"] == "throttled"


def test_browser_catalog_refresh_updates_cards_preserves_filters_and_retains_failures():
        import shutil
        import subprocess
        from pathlib import Path
        node = shutil.which("node")
        if not node:
                pytest.skip("Node.js is required for the catalog refresh regression")
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/app.js', 'utf8');
const filters = source.slice(source.indexOf('  function uniqueValues('), source.indexOf('  // Persist a <details>'));
const refresh = source.slice(source.indexOf('  let catalogInitialized'), source.indexOf('  function init(catalog)'));
function fixture(replies, me = {customerOperated:true}) {
    const elements = new Map();
    const calls = [];
    const state = {me, items:[{displayName:'Existing',category:'Compute'}], q:'existing', category:new Set(['Compute']), industry:new Set(), compliance:new Set()};
    const context = {state, Promise, Date, Set, Number, Math, Error, Array,
        catalogReady:Promise.resolve(), AbortSignal:{timeout:()=>undefined},
        setTimeout:resolve=>queueMicrotask(resolve), render(){},
        $(selector) {
            if (!elements.has(selector)) elements.set(selector, {children:[], textContent:'', hidden:true, appendChild(child){this.children.push(child)}, set innerHTML(value){this.children=[]}});
            return elements.get(selector);
        },
        el(tag, props, children=[]) {return {tag,...props,children,checked:false,addEventListener(){}}},
        async fetch(path, options) {
            calls.push({path,method:options.method});
            assert.equal(options.cache, 'no-store');
            assert.equal(options.credentials, 'same-origin');
            const reply = replies.shift();
            assert.ok(reply, 'Unexpected request');
            return {ok:!reply.httpError, json:async()=>reply};
        },
    };
    vm.createContext(context);
    vm.runInContext(filters + refresh + '\ncatalogInitialized=true;globalThis.sync=synchronizeCatalog;globalThis.reload=reloadPublishedCatalog;', context);
    return {context, state, elements, calls};
}
(async () => {
    const next = {items:[{displayName:'Existing',category:'Compute'},{displayName:'Merged',category:'Data'}]};
    const success = fixture([{status:'throttled',retryAfter:1},{status:'queued'},{status:'running'},{status:'succeeded'},next]);
    const pending = success.context.sync();
    assert.equal(success.context.sync(), pending);
    await pending;
    assert.equal(success.state.items.length, 2);
    assert.equal(success.state.q, 'existing');
    assert.ok(success.state.category.has('Compute'));
    const options = success.elements.get('#filter-category').children;
    assert.equal(options.find(label=>label.children[0].value==='Compute').children[0].checked, true);
    assert.equal(options.find(label=>label.children[0].value==='Data').children[0].checked, false);
    assert.deepEqual(success.calls.map(call=>call.method), ['POST','POST','GET','GET','GET']);
    assert.equal(success.calls.at(-1).path, 'catalog.json');
    assert.equal(success.elements.get('#catalog-sync-status').textContent, 'Use cases are up to date.');
    const racing = fixture([]);
    const reads = [];
    racing.context.fetch = async()=>new Promise(resolve=>reads.push(resolve));
    const older = racing.context.reload();
    await new Promise(resolve=>setImmediate(resolve));
    const newer = racing.context.reload();
    await new Promise(resolve=>setImmediate(resolve));
    reads[1]({ok:true,json:async()=>({items:[]})});
    await newer;
    reads[0]({ok:true,json:async()=>next});
    await older;
    assert.equal(racing.state.items.length,0);
    for (const replies of [[{status:'failed'}],[{httpError:true}],[{status:'succeeded'},{unexpected:true}]]) {
        const failure = fixture(replies);
        await failure.context.sync();
        assert.equal(failure.state.items[0].displayName, 'Existing');
        assert.match(failure.elements.get('#catalog-sync-status').textContent, /Could not sync/);
    }
    for (const isAdmin of [true, false]) {
        const reconnect = fixture([{status:'queued'},{status:'failed',code:'github_reconnect_required',detail:'private upstream error'}], {customerOperated:true,isAdmin});
        await reconnect.context.sync();
        const message = reconnect.elements.get('#catalog-sync-status').textContent;
        assert.match(message, /GitHub authorization is unavailable/);
        assert.match(message, isAdmin ? /Setup > GitHub/ : /Ask a portal administrator/);
        assert.doesNotMatch(message, /private|Could not sync/);
        assert.equal(reconnect.state.items[0].displayName, 'Existing');
        assert.deepEqual(reconnect.calls.map(call=>call.method), ['POST','GET']);
    }
    const unknownFailure = fixture([{status:'failed',code:'private unknown code',detail:'private upstream error'}]);
    await unknownFailure.context.sync();
    assert.match(unknownFailure.elements.get('#catalog-sync-status').textContent, /Could not sync/);
    assert.doesNotMatch(unknownFailure.elements.get('#catalog-sync-status').textContent, /private/);
    const disconnected = fixture([{status:'not_configured'}]);
    await disconnected.context.sync();
    assert.equal(disconnected.calls.length, 1);
    assert.match(disconnected.elements.get('#catalog-sync-status').textContent, /not connected/);
    for (const me of [null,{customerOperated:false},{customerOperated:true,readOnlyPreview:true}]) {
        const inactive = fixture([], me);
        await inactive.context.sync();
        assert.equal(inactive.calls.length, 0);
    }
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr


def test_processing_endpoints_require_admin_origin_and_verified_runners(configured, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import db
    import setup_github
    import setup_routes
    from identity import CurrentUser, require_admin
    application = FastAPI()
    application.include_router(setup_routes.build_setup_router())
    client = TestClient(application)
    path = "/api/admin/setup/github/processing"
    assert client.put(path, json={"confirm": True}).status_code in (401, 403)
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "", "", "", "admin")
    assert client.put(path, json={"confirm": True}).status_code == 403
    headers = {"Origin": "http://testserver"}
    assert client.put(path, headers=headers, json={}).status_code == 400
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    async def probe(**kwargs):
        return {"github-hosted": {"available": False}, "self-hosted": {"available": False}}
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    assert client.put(path, headers=headers, json={"mode": "github-hosted", "confirm": True}).status_code == 400
    assert client.put(path, headers=headers, json={"mode": "container", "capacityRegions": ["eastus"], "confirm": True}).status_code == 200
    assert client.post("/api/admin/setup/github/sync", headers=headers).json()["status"] == "queued"
    assert client.get(path).json()["job"]["status"] == "queued"
    assert client.post("/api/admin/setup/github/runners/probe", headers=headers).status_code == 200
    assert client.post("/api/admin/setup/github/runners/probe", headers=headers).status_code == 429
    with pytest.raises(ValueError, match="needs no workflows"):
        asyncio.run(setup_github.create_workflow_pr(True))


def test_container_pricing_discards_authored_price_values(monkeypatch):
    import json
    async def estimate(cost, region, parameters):
        assert "unitPrice" not in cost["components"][0]
        return {"complete": True, "totalMonthly": 20, "currency": "USD", "region": region, "components": [{"unitPrice": 2, "monthly": 20}]}
    monkeypatch.setattr(processing.pricing, "estimate", estimate)
    metadata = {"displayName": "Example", "cost": {"region": "eastus", "components": [{"quantityPerMonth": 10, "unitPrice": 900}]}}
    blobs = {"catalog/example/metadata.json": json.dumps(metadata).encode()}
    result = asyncio.run(processing.process_blobs(blobs))
    assert json.loads(result["catalog/example/pricing.json"])["cost"]["totalMonthly"] == 20