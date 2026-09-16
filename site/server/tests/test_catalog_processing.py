"""Offline processing, runner selection and sandbox regression checks."""

import asyncio
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


def test_optional_workflows_target_selected_runners_and_only_reviewed_branches():
    import json
    import catalog_workflows
    for labels in (None, ["self-hosted", "Linux", "X64", "portal"]):
        files = catalog_workflows.workflow_files(labels)
        for content in files.values():
            workflow = json.loads(content)
            assert "pull_request" not in workflow["on"]
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
        assert regions == ["eastus"]
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