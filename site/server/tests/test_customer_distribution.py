"""Offline checks for the customer-operated distribution boundary."""

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
    return _tool_module("build_customer_distribution.py")


@pytest.fixture
def preview_server(tmp_path: Path) -> Iterator[ThreadingHTTPServer]:
    archive_path = tmp_path / "customer.zip"
    _exporter().build_archive(SERVER_DIR.parent.parent, archive_path)
    root = tmp_path / "export"
    with ZipFile(archive_path) as archive:
        archive.extractall(root)
    server = _tool_module("preview_customer.py").create_preview_server(root, port=0)
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
    with pytest.raises(ValueError, match="extracted customer export"):
        _tool_module("preview_customer.py").create_preview_server(tmp_path, port=0)


def test_customer_export_contains_only_platform_source() -> None:
    files = _exporter().collect_files(SERVER_DIR.parent.parent)
    assert json.loads(files["site/catalog.json"]) == {"items": []}
    assert json.loads(files["site/distribution.json"])["distribution"] == "customer"
    for name in files:
        assert not name.startswith(("catalog/", "modules/", ".github/", ".git/", "applog/", "src/", "samples/"))
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
    archive_path = tmp_path / "customer.zip"
    _exporter().build_archive(root, archive_path)
    cloned = tmp_path / "private-clone"
    with ZipFile(archive_path) as archive:
        archive.extractall(cloned)
    rebuilt = _exporter().collect_files(cloned)
    assert rebuilt == exported
    context = (cloned / ".dockerignore").read_text().splitlines()
    assert context[0] == "**"
    for directory in ("customer", "tools", "site", "site/assets", "site/server"):
        assert context[context.index(f"!{directory}/") + 1] == f"{directory}/**"
    for relative in _exporter().SOURCE_FILES:
        assert "!" + relative in context
    assert not any(line in context for line in ("!site/server/tests/", "!.git/", "!runs/", "!site/server/data/"))
    dockerfile = (cloned / "Dockerfile").read_text()
    assert "COPY --from=source /export/site/" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "server/container_start.py"]' in dockerfile
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
    assert set(reads) == set(exporter.SOURCE_FILES) | {"customer/README.md"}
    assert "tools/build_customer_distribution.py" not in files


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
def customer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "customer")
    for name in (
        "GALLERY_ENTRA_TENANT_ID", "GALLERY_ENTRA_CLIENT_ID",
        "GALLERY_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "GALLERY_ADMIN_OIDS",
    ):
        monkeypatch.setenv(name, "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("GALLERY_EASY_AUTH", "true")
    monkeypatch.setenv("GALLERY_HTTPS", "true")
    for name in ("ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID", "GALLERY_INVITE_GUEST", "GALLERY_TERRAFORM_AUTO_INSTALL"):
        monkeypatch.delenv(name, raising=False)


def test_customer_configuration_accepts_explicit_settings(customer: None) -> None:
    distribution.validate_customer_configuration()


def test_installer_can_start_without_entra_only_with_bounded_host_configuration(
    customer: None, monkeypatch: pytest.MonkeyPatch,
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
    environment["GALLERY_DISTRIBUTION"] = "customer"
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
    customer: None, tmp_path: Path,
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
    customer: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
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
    customer: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", tmp_path / "missing.json")
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "internal")
    assert distribution.customer_configuration_errors() == [
        "Customer mode is not enabled; use a customer export or set GALLERY_DISTRIBUTION=customer"
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
    customer: None, monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        distribution.validate_customer_configuration()


def test_packaged_customer_mode_cannot_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "distribution.json"
    marker.write_text('{"distribution":"customer"}', encoding="utf-8")
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", marker)
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "internal")
    assert distribution.is_customer_distribution()


def test_first_visitor_is_not_customer_admin(
    customer: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "customer.db"))
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


def test_customer_blocks_legacy_auth_routes(customer: None) -> None:
    from app import app

    client = TestClient(app)
    for route in ("external/start", "external/poll", "device/start", "device/poll", "start", "photo"):
        response = client.post(f"/api/auth/entra/{route}")
        assert response.status_code == 404
    assert client.get("/api/me").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_customer_rejects_external_deploy_before_catalog_lookup(customer: None) -> None:
    import api
    from identity import CurrentUser, require_user

    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser(
        "user", "user@example.com", "User", "tenant"
    )
    response = TestClient(application).post("/api/deployments", json={
        "useCase": "customer-code", "externalSubscription": True,
    })
    assert response.status_code == 403


def test_customer_executor_uses_only_explicit_managed_identity(
    customer: None, monkeypatch: pytest.MonkeyPatch,
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
    customer: None, monkeypatch: pytest.MonkeyPatch,
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
    customer: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import azure_deploy

    monkeypatch.delenv("GALLERY_GRANT_VIEW_ACCESS", raising=False)
    assert not azure_deploy._view_grant_enabled()
    assert not azure_deploy._invite_enabled()
    monkeypatch.setenv("GALLERY_INVITE_GUEST", "true")
    assert not azure_deploy._invite_enabled()