"""Offline checks for the organization-operated distribution boundary."""

from __future__ import annotations

import sys
import hashlib
import importlib.util
import json
import os
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import db
import distribution
import github_intake


def _tool_module(filename: str):
    root = SERVER_DIR.parent.parent
    spec = importlib.util.spec_from_file_location(
        Path(filename).stem, root / "tools" / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exporter():
    return _tool_module("build_distribution.py")


@pytest.fixture
def preview_server(tmp_path: Path) -> Iterator[ThreadingHTTPServer]:
    archive_path = tmp_path / "organization.zip"
    _exporter().build_archive(SERVER_DIR.parent.parent, archive_path)
    root = tmp_path / "export"
    with ZipFile(archive_path) as archive:
        archive.extractall(root)
    server = _tool_module("preview_portal.py").create_preview_server(root, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("route", ["/", "/assets/app.js", "/api/me", "/api/admin/telemetry?days=30"])
def test_preview_serves_exported_ui_and_synthetic_data(
    preview_server: ThreadingHTTPServer, route: str,
) -> None:
    connection = HTTPConnection("127.0.0.1", preview_server.server_port, timeout=5)
    try:
        connection.request("GET", route)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("X-Preview-Mode") == "read-only"
        assert response.read()
    finally:
        connection.close()
    assert preview_server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("method,route,status", [
    ("POST", "/api/deployments", 405),
    ("POST", "/api/use-cases/upload", 405),
    ("POST", "/api/admin/users", 405),
    ("PUT", "/api/deployments", 405),
    ("PATCH", "/api/deployments", 405),
    ("DELETE", "/api/deployments/preview", 405),
    ("GET", "/server/.env.example", 404),
    ("GET", "/main.py", 404),
    ("GET", "/../LICENSE", 404),
    ("GET", "/%2e%2e/server/db.py", 404),
])
def test_preview_rejects_writes_and_nonpublic_files(
    preview_server: ThreadingHTTPServer, method: str, route: str, status: int,
) -> None:
    connection = HTTPConnection("127.0.0.1", preview_server.server_port, timeout=5)
    try:
        connection.request(method, route)
        response = connection.getresponse()
        assert response.status == status
        assert json.loads(response.read())["detail"]
    finally:
        connection.close()


def test_preview_rejects_other_host_headers(preview_server: ThreadingHTTPServer) -> None:
    connection = HTTPConnection("127.0.0.1", preview_server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/me", headers={"Host": "untrusted.example"})
        assert connection.getresponse().status == 403
    finally:
        connection.close()


def test_preview_requires_customer_export(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="extracted organization export"):
        _tool_module("preview_portal.py").create_preview_server(tmp_path, port=0)


def test_workflow_installer_is_idempotent_and_preserves_conflicts(tmp_path):
    installer = _tool_module("portal_workflows.py")
    installer.install(tmp_path)
    installer.install(tmp_path)
    assert len(list((tmp_path / ".github/workflows").glob("*.yml"))) == 2
    target = tmp_path / "tools/portal_catalog.py"
    target.write_text("organization changes")
    with pytest.raises(ValueError, match="differs"):
        installer.install(tmp_path)
    assert target.read_text() == "organization changes"


def test_catalog_builder_refreshes_prices_and_compiles_without_cloud_writes(tmp_path, monkeypatch):
    import asyncio
    import pricing
    builder = _tool_module("portal_catalog.py")
    root = tmp_path / "catalog/example"
    (root / "parameters").mkdir(parents=True)
    (root / "main.bicep").write_text("param location string = 'eastus'")
    (root / "metadata.json").write_text(json.dumps({"displayName": "Example", "cost": {"region": "eastus", "components": [{"name": "Compute", "billing": "fixed", "quantityPerMonth": 10, "unitPrice": 99, "meterId": "example"}]}}))
    template = {"parameters": {}, "resources": []}
    calls = []
    def command(arguments, cwd):
        calls.append(arguments)
        if arguments[0] == "git":
            return "a" * 40
        assert arguments[:3] == ["az", "bicep", "build"]
        return json.dumps(template)
    async def estimate(cost, region, parameters):
        assert "unitPrice" not in cost["components"][0]
        return {"complete": True, "components": [{"unitPrice": 2, "monthly": 20}], "totalMonthly": 20}
    monkeypatch.setattr(builder, "command", command)
    monkeypatch.setattr(pricing, "estimate", estimate)
    asyncio.run(builder.build(tmp_path))
    import base64
    files = json.loads((tmp_path / "runs/portal-generated/manifest.json").read_bytes())["files"]
    assert json.loads(base64.b64decode(files["catalog/example/compiled/dev.json"]))["template"] == template
    assert files["catalog/example/compiled/prod.json"] is None
    priced = json.loads(base64.b64decode(files["catalog/example/pricing.json"]))
    assert priced["cost"]["totalMonthly"] == 20
    assert priced["cost"]["components"][0]["meterId"] == "example"
    assert len(calls) == 2


def test_workflow_publisher_rejects_stale_or_unsafe_outputs_before_writing(tmp_path, monkeypatch):
    builder = _tool_module("portal_catalog.py")
    monkeypatch.setattr(builder, "command", lambda arguments, cwd: "a" * 40 if arguments[1] == "rev-parse" else "")
    target = tmp_path / "runs/portal-generated/manifest.json"
    target.parent.mkdir(parents=True)
    for manifest, message in [({"sourceCommit": "b" * 40, "files": {}}, "Branch changed"),
                              ({"sourceCommit": "a" * 40, "files": {"../escape.json": None}}, "allowlisted")]:
        target.write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match=message):
            builder.publish(tmp_path)
    assert not (tmp_path / "site").exists()


def test_workflow_publication_is_allowlisted_and_noop_safe(tmp_path, monkeypatch):
    import base64
    builder = _tool_module("portal_catalog.py")
    destination = tmp_path / "catalog/example/compiled/prod.json"
    destination.parent.mkdir(parents=True)
    destination.write_text('{"old": true}')
    unrelated = tmp_path / "organization-notes.txt"
    unrelated.write_text("Keep this")
    manifest = tmp_path / "runs/portal-generated/manifest.json"
    manifest.parent.mkdir(parents=True)
    content = b'{"subscriptions": {}}'
    manifest.write_text(json.dumps({"sourceCommit": "a" * 40, "files": {"site/capacity.json": base64.b64encode(content).decode(), "catalog/example/compiled/prod.json": None}}))
    calls = []
    def command(arguments, root):
        calls.append(arguments)
        return "a" * 40 if arguments[1] == "rev-parse" else ""
    monkeypatch.setattr(builder, "command", command)
    builder.publish(tmp_path)
    assert (tmp_path / "site/capacity.json").read_bytes() == content
    assert not destination.exists()
    assert unrelated.read_text() == "Keep this"
    assert ["git", "add", "--", "site/capacity.json", "catalog/example/compiled/prod.json"] in calls
    assert calls[-1] == ["git", "push", "origin", "HEAD"]
    calls.clear()
    builder.publish(tmp_path)
    assert len(calls) == 2


def test_capacity_collector_uses_only_scoped_reads(tmp_path, monkeypatch):
    import base64
    builder = _tool_module("portal_catalog.py")
    tenant = "11111111-1111-4111-8111-111111111111"
    subscription = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("AZURE_TENANT_ID", tenant)
    monkeypatch.setenv("AZURE_CLIENT_ID", "33333333-3333-4333-8333-333333333333")
    monkeypatch.setenv("PORTAL_SUBSCRIPTION_IDS", subscription)
    monkeypatch.setenv("PORTAL_CAPACITY_REGIONS", "eastus westus")
    calls = []
    def command(arguments, root):
        if arguments[0] == "git":
            return "a" * 40
        calls.append(arguments)
        assert arguments[:5] == ["az", "rest", "--method", "get", "--url"]
        assert arguments[5].startswith(f"https://management.azure.com/subscriptions/{subscription}")
        if "/usages?" not in arguments[5]:
            return json.dumps({"tenantId": tenant, "subscriptionId": subscription, "state": "Enabled"})
        return json.dumps({"value": [{"name": {"value": "cores"}, "limit": 10, "currentValue": 2}]})
    monkeypatch.setattr(builder, "command", command)
    builder.capacity(tmp_path)
    assert len(calls) == 3
    files = json.loads((tmp_path / "runs/portal-generated/manifest.json").read_bytes())["files"]
    snapshot = json.loads(base64.b64decode(files["site/capacity.json"]))
    assert snapshot["subscriptions"][subscription]["westus"][0]["available"] == 8
    assert snapshot["physicalCapacity"] == "unknown"
    monkeypatch.setenv("PORTAL_CAPACITY_REGIONS", "eastus;injection")
    with pytest.raises(ValueError):
        builder.capacity_inputs()


@pytest.mark.skipif(os.environ.get("PORTAL_TEST_BICEP") != "1", reason="Explicit local compiler contract check")
def test_local_bicep_parameter_wrapper_contract(tmp_path):
    import asyncio
    import base64
    builder = _tool_module("portal_catalog.py")
    root = tmp_path / "catalog/example"
    (root / "parameters").mkdir(parents=True)
    (root / "main.bicep").write_text("param location string = 'eastus'\noutput selected string = location\n")
    (root / "parameters/dev.bicepparam").write_text("using '../main.bicep'\nparam location = 'westus'\n")
    (root / "metadata.json").write_text('{"displayName":"Compiler contract"}')
    original = builder.command
    builder.command = lambda arguments, cwd: "a" * 40 if arguments[0] == "git" else original(arguments, cwd)
    asyncio.run(builder.build(tmp_path))
    files = json.loads((tmp_path / "runs/portal-generated/manifest.json").read_bytes())["files"]
    wrapper = json.loads(base64.b64decode(files["catalog/example/compiled/dev.json"]))
    assert wrapper["parameters"]["location"] == {"value": "westus"}
    assert wrapper["template"]["outputs"]["selected"]["type"] == "string"


@pytest.mark.parametrize("mode", ["organization", "customer"])
def test_distribution_mode_accepts_existing_installations(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", tmp_path / "absent.json")
    monkeypatch.setenv("GALLERY_DISTRIBUTION", mode)
    assert distribution.is_customer_distribution()


def test_renamed_export_paths_exclude_customer_filenames():
    exported = _exporter().collect_files(SERVER_DIR.parent.parent)
    assert not any("customer" in name.lower() for name in exported)
    assert {"tools/portal_source.py", "tools/portal_workflows.py", "tools/preview_portal.py"} <= set(exported)
    assert b"GALLERY_FOOTER_TEXT" in exported["site/server/.env.example"]
    assert b"data-brand-footer" in exported["site/index.html"]


def test_customer_export_contains_only_platform_source() -> None:
    files = _exporter().collect_files(SERVER_DIR.parent.parent)
    assert json.loads(files["site/catalog.json"]) == {"items": []}
    assert json.loads(files["site/distribution.json"])["distribution"] == "organization"
    assert b".portal/" in files[".gitignore"].splitlines()
    assert not any(name.startswith(".github/workflows/") for name in files)
    assert "site/server/catalog_workflows.py" in files
    for name in files:
        assert not name.startswith(("catalog/", "modules/", ".git/", ".portal/", "applog/", "src/", "samples/"))
        assert not name.endswith((".bicep", ".bicepparam", ".tf", ".tfvars", ".db", ".log"))
        assert "/_templates/" not in name and "/_tf/" not in name
        assert "azure-icons" not in name
    manifest = json.loads(files["MANIFEST.json"])
    assert set(manifest["files"]) == set(files) - {"MANIFEST.json"}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert b'THE SOFTWARE IS PROVIDED "AS IS"' in files["LICENSE"]


def test_customer_export_can_rebuild_itself_and_container_context(tmp_path: Path) -> None:
    root = SERVER_DIR.parent.parent
    exported = _exporter().collect_files(root)
    archive_path = tmp_path / "organization.zip"
    _exporter().build_archive(root, archive_path)
    cloned = tmp_path / "private-clone"
    with ZipFile(archive_path) as archive:
        archive.extractall(cloned)
    rebuilt = _exporter().collect_files(cloned)
    assert rebuilt == exported
    context = (cloned / ".dockerignore").read_text().splitlines()
    assert context[0] == "**"
    for directory in ("docs", "tools", "site", "site/assets", "site/server"):
        assert context[context.index(f"!{directory}/") + 1] == f"{directory}/**"
    for relative in _exporter().SOURCE_FILES:
        assert "!" + relative in context
    assert not any(line in context for line in ("!site/server/tests/", "!.git/", "!runs/", "!site/server/data/"))
    dockerfile = (cloned / "Dockerfile").read_text()
    assert "COPY --from=source /export/site/" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "server/container_start.py"]' in dockerfile
    assert "tools/Deploy-Portal.ps1" in exported
    assert "tools/Initialize-Portal.ps1" in exported
    assert "tools/Portal-Foundation.ps1" in exported
    assert not any(name.endswith(".Tests.ps1") for name in exported)
    assert "--mount=type=secret,id=pip_config,target=/etc/pip.conf" in dockerfile
    assert "ARG PIP_INDEX_URL" not in dockerfile
    assert "ENV PIP_INDEX_URL" not in dockerfile


def test_customer_checkout_has_empty_snapshots_and_no_bundled_workloads() -> None:
    root = SERVER_DIR.parent.parent
    exported = _exporter().collect_files(root)
    for filename in ("catalog.json", "registry.json", "capacity.json", "updates.json", "distribution.json"):
        assert json.loads((root / "site" / filename).read_text(encoding="utf-8")) == json.loads(exported[f"site/{filename}"])
    for relative in ("catalog", "modules", "src", "samples", "applog", "site/server/_templates", "site/server/_tf"):
        assert not (root / relative).exists(), relative
    assert not list((root / ".github/workflows").glob("*.yml"))
    assert not (root / "site/assets/azure-icons.js").exists()


def test_customer_export_blocks_known_internal_content(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text('destination = "mcaps-microsoft/TechLabs"', encoding="utf-8")
    with pytest.raises(ValueError, match="content check"):
        _exporter()._read_source(tmp_path, "source.py")


def test_export_scans_and_packages_the_same_source_bytes(monkeypatch) -> None:
    exporter = _exporter()
    original = exporter._read_source
    reads = []

    def read_once(root, relative):
        assert relative not in reads
        reads.append(relative)
        return original(root, relative)

    monkeypatch.setattr(exporter, "_read_source", read_once)
    files = exporter.collect_files(SERVER_DIR.parent.parent)
    assert set(reads) == set(exporter.SOURCE_FILES) | {"docs/OPERATIONS.md"}
    assert "tools/build_distribution.py" not in files


def test_customer_example_contains_no_configured_resource_ids() -> None:
    settings = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in (SERVER_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    for name in ("GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID", "GALLERY_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "ARM_TENANT_ID", "GALLERY_ADMIN_OIDS", "GALLERY_GITHUB_REPO", "GALLERY_GITHUB_TOKEN"):
        assert settings[name] == ""
    assert settings["GALLERY_EASY_AUTH"] == settings["GALLERY_HTTPS"] == "true"
    assert settings["GALLERY_INVITE_GUEST"] == "false"


@pytest.fixture
def organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    for name in (
        "GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID",
        "GALLERY_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "GALLERY_ADMIN_OIDS",
    ):
        monkeypatch.setenv(name, "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    monkeypatch.setenv("GALLERY_HTTPS", "true")
    for name in ("ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID", "GALLERY_INVITE_GUEST", "GALLERY_TERRAFORM_AUTO_INSTALL"):
        monkeypatch.delenv(name, raising=False)


def test_customer_configuration_accepts_explicit_settings(organization: None) -> None:
    distribution.validate_customer_configuration()


def test_installer_can_start_without_entra_only_with_bounded_host_configuration(
    organization: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID", "GALLERY_ADMIN_OIDS"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("GALLERY_INSTALLER_ENABLED", "true")
    assert distribution.customer_configuration_errors()
    monkeypatch.setenv("GALLERY_SETUP_CODE_SHA256", hashlib.sha256(b"synthetic-setup-code").hexdigest())
    monkeypatch.setenv("GALLERY_SETUP_EXPIRES_AT", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    monkeypatch.setenv("GALLERY_PUBLIC_URL", "https://portal.example.com")
    monkeypatch.setenv("GALLERY_HOST_RESOURCE_ID", "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/test/providers/Microsoft.Web/sites/portal")
    distribution.validate_customer_configuration()
    monkeypatch.setenv("GALLERY_SETUP_EXPIRES_AT", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert any("GALLERY_SETUP_EXPIRES_AT" in error for error in distribution.customer_configuration_errors())
    monkeypatch.setenv("GALLERY_SETUP_EXPIRES_AT", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
    assert distribution.installer_configuration_errors()
    monkeypatch.setenv("GALLERY_INSTALLER_ENABLED", "false")
    assert any("GALLERY_ENTRA_CLIENT_ID" in error for error in distribution.customer_configuration_errors())


def test_preflight_reports_all_missing_settings_without_side_effects(
    tmp_path: Path,
) -> None:
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("GALLERY_", "ARM_", "AZURE_"))
    }
    environment["GALLERY_DISTRIBUTION"] = "organization"
    environment["GALLERY_DB_PATH"] = str(tmp_path / "must-not-exist.db")
    result = subprocess.run(
        [sys.executable, "-S", str(SERVER_DIR / "distribution.py"), "--check", "--json"],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["passed"] is False
    assert len(report["errors"]) == 6
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_preflight_succeeds_with_standard_library_only(
    organization: None, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(SERVER_DIR / "distribution.py"), "--check", "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["passed"] is True
    assert report["scope"] == "local-configuration-only"
    assert report["errors"] == []
    assert "11111111-1111-4111-8111-111111111111" not in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_preflight_does_not_print_invalid_values(
    organization: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GALLERY_ENTRA_CLIENT_ID", "sensitive-value-must-not-be-printed")
    monkeypatch.setenv("GALLERY_ADMIN_OIDS", "another-sensitive-value")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "private-token-must-not-be-printed")
    monkeypatch.setattr(sys, "argv", ["distribution.py", "--check"])
    assert distribution.main() == 1
    output = capsys.readouterr().out
    assert "GALLERY_ENTRA_CLIENT_ID" in output
    assert "GALLERY_ADMIN_OIDS" in output
    assert "sensitive-value" not in output
    assert "private-token" not in output


def test_preflight_cannot_succeed_in_internal_mode(
    organization: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", tmp_path / "missing.json")
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "internal")
    assert distribution.customer_configuration_errors() == [
        "Organization mode is not enabled; use an organization export or set GALLERY_DISTRIBUTION=organization"
    ]
    distribution.validate_customer_configuration()


@pytest.mark.parametrize("setting,value", [
    ("GALLERY_ENTRA_TENANT_ID", "organizations"),
    ("GALLERY_ENTRA_TENANT_ID", ""),
    ("GALLERY_ENTRA_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46"),
    ("GALLERY_SUBSCRIPTION_ID", "00000000-0000-0000-0000-000000000000"),
    ("AZURE_CLIENT_ID", ""),
    ("GALLERY_ADMIN_OIDS", ""),
    ("GALLERY_ADMIN_OIDS", "not-a-guid"),
    ("GALLERY_EASY_AUTH", "false"),
    ("GALLERY_HTTPS", "false"),
    ("ARM_TENANT_ID", "22222222-2222-4222-8222-222222222222"),
    ("ARM_SUBSCRIPTION_ID", "22222222-2222-4222-8222-222222222222"),
    ("GALLERY_INVITE_GUEST", "true"),
    ("GALLERY_TERRAFORM_AUTO_INSTALL", "true"),
])
def test_customer_configuration_fails_closed(
    organization: None, monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        distribution.validate_customer_configuration()


def test_packaged_customer_mode_cannot_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "distribution.json"
    marker.write_text('{"distribution":"organization"}', encoding="utf-8")
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", marker)
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "internal")
    assert distribution.is_customer_distribution()


def test_first_visitor_is_not_customer_admin(
    organization: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "organization.db"))
    monkeypatch.setattr(db, "_CONN", None)
    connection = db.init_db()
    try:
        assert db.upsert_user("visitor", "visitor@example.com", "Visitor") == "guest"
        assert db.upsert_user("11111111-1111-4111-8111-111111111111", "admin@example.com", "Admin") == "admin"
    finally:
        connection.close()


def test_no_implicit_github_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GALLERY_GITHUB_REPO", raising=False)
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "test-only")
    assert not github_intake.configured()


def test_customer_blocks_legacy_auth_routes(organization: None) -> None:
    from app import app

    client = TestClient(app)
    for route in ("external/start", "external/poll", "device/start", "device/poll", "start", "photo"):
        response = client.post(f"/api/auth/entra/{route}")
        assert response.status_code == 404
    assert client.get("/api/me").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_customer_rejects_external_deploy_before_catalog_lookup(organization: None) -> None:
    import api
    from identity import CurrentUser, require_user

    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser(
        "user", "user@example.com", "User", "tenant"
    )
    response = TestClient(application).post("/api/deployments", json={
        "useCase": "organization-code", "externalSubscription": True,
    })
    assert response.status_code == 403


def test_customer_executor_uses_only_explicit_managed_identity(
    organization: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import azure.identity
    import azure_deploy

    monkeypatch.setattr(azure_deploy, "_credential", None)
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", lambda **kwargs: kwargs)
    assert azure_deploy._get_credential() == {
        "client_id": "11111111-1111-4111-8111-111111111111"
    }
    with pytest.raises(RuntimeError, match="outside"):
        azure_deploy._client("different-subscription")
    with pytest.raises(RuntimeError, match="managed identity"):
        azure_deploy._credential_for_deployment({"_delegated_arm_token": "test-only"})


def test_customer_terraform_uses_managed_identity(
    organization: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import terraform_exec

    monkeypatch.setenv("ARM_CLIENT_SECRET", "test-only")
    monkeypatch.setenv("ARM_USE_CLI", "true")
    env = terraform_exec._run_env({
        "subscription_id": "11111111-1111-4111-8111-111111111111"
    })
    assert "ARM_CLIENT_SECRET" not in env
    assert env["ARM_USE_CLI"] == "false"
    assert env["ARM_USE_MSI"] == "true"
    assert env["ARM_CLIENT_ID"] == "11111111-1111-4111-8111-111111111111"
    with pytest.raises(RuntimeError, match="outside"):
        terraform_exec._run_env({"subscription_id": "different"})


def test_customer_does_not_invite_guests_or_grant_access_by_default(
    organization: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import azure_deploy

    monkeypatch.delenv("GALLERY_GRANT_VIEW_ACCESS", raising=False)
    assert not azure_deploy._view_grant_enabled()
    assert not azure_deploy._invite_enabled()
    monkeypatch.setenv("GALLERY_INVITE_GUEST", "true")
    assert not azure_deploy._invite_enabled()