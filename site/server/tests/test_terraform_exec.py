"""Unit tests for the Terraform executor plumbing.

These run without the Azure SDK or a real ``terraform`` binary. They lock in the
parts that previously only surfaced at runtime in App Service:

* injected values reach the run as an ``*.auto.tfvars.json`` file (no shell ``-var``
  strings, so no quoting/injection surface);
* the backend block is chosen from environment (azurerm remote vs local);
* the per-environment ``*.tfvars`` file is selected and unknown environments fall
  back to ``dev``.

Run: ``python -m pytest site/server/tests/test_terraform_exec.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import terraform_exec  # noqa: E402


def _make_module(root: Path, use_case: str) -> Path:
    src = root / use_case
    src.mkdir(parents=True)
    (src / "main.tf").write_text("# module\n", encoding="utf-8")
    (src / "variables.tf").write_text("# vars\n", encoding="utf-8")
    (src / "dev.tfvars").write_text('environment = "dev"\n', encoding="utf-8")
    (src / "prod.tfvars").write_text('environment = "prod"\n', encoding="utf-8")
    return src


def _dep(**over):
    base = {
        "id": "dep-123",
        "use_case": "web-app",
        "resource_group": "rg-techlabs-web-app-dev",
        "environment": "dev",
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "user_upn": "user@example.com",
        "params": {"location": "westus2"},
    }
    base.update(over)
    return base


@pytest.fixture
def staged(tmp_path, monkeypatch):
    tf_dir = tmp_path / "_tf"
    work_dir = tmp_path / "work"
    _make_module(tf_dir, "web-app")
    monkeypatch.setattr(terraform_exec, "TF_DIR", tf_dir)
    monkeypatch.setenv("GALLERY_TF_WORK", str(work_dir))
    for var in ("GALLERY_TF_STATE_STORAGE", "GALLERY_TERRAFORM_BIN", "ARM_USE_MSI"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_parameter_overrides_snake_case_and_types(monkeypatch):
    monkeypatch.setattr(
        terraform_exec,
        "_param_types",
        lambda _uc: {
            "appServiceSku": "string",
            "appCapacity": "int",
            "appZoneRedundant": "bool",
            "chatModelName": "string",
        },
    )
    dep = _dep(
        use_case="ai-chat",
        params={
            "location": "westus2",
            "parameterValues": {
                "appServiceSku": "P1v3",
                "appCapacity": "3",
                "appZoneRedundant": "true",
                "chatModelName": "gpt-4o",
            },
        },
    )
    out = terraform_exec._parameter_overrides(dep)
    assert out == {
        "app_service_sku": "P1v3",
        "app_capacity": 3,
        "app_zone_redundant": True,
        "chat_model_name": "gpt-4o",
    }


def test_parameter_overrides_empty_when_none():
    assert terraform_exec._parameter_overrides(_dep()) == {}


def test_terraform_unavailable_without_binary(monkeypatch):
    monkeypatch.delenv("GALLERY_TERRAFORM_BIN", raising=False)
    monkeypatch.setattr(terraform_exec.shutil, "which", lambda _name: None)
    ok, reason = terraform_exec.terraform_available()
    assert ok is False
    assert "not found" in reason.lower()


def test_injected_tags_mark_managed_deployment():
    tags = terraform_exec._injected_tags(_dep())
    assert tags["techlabs-managed"] == "true"
    assert tags["techlabs-deployment"] == "dep-123"
    assert tags["techlabs-usecase"] == "web-app"
    assert tags["techlabs-owner"] == "user@example.com"


def test_backend_config_local_by_default(monkeypatch):
    monkeypatch.delenv("GALLERY_TF_STATE_STORAGE", raising=False)
    assert terraform_exec._backend_config(_dep()) is None


def test_backend_config_remote_when_storage_set(monkeypatch):
    monkeypatch.setenv("GALLERY_TF_STATE_STORAGE", "stterraformstate")
    monkeypatch.setenv("ARM_USE_MSI", "true")
    flags = terraform_exec._backend_config(_dep())
    assert flags is not None
    joined = " ".join(flags)
    assert "storage_account_name=stterraformstate" in joined
    assert "key=web-app/dep-123.tfstate" in joined
    assert "use_msi=true" in joined


def test_prepare_workdir_injects_vars_and_local_backend(staged):
    work, remote = terraform_exec._prepare_workdir(_dep())
    assert remote is False
    assert (work / "main.tf").exists()
    assert (work / "dev.tfvars").exists()

    backend = (work / "backend.tf").read_text(encoding="utf-8")
    assert 'backend "local"' in backend

    injected = json.loads((work / "injected.auto.tfvars.json").read_text(encoding="utf-8"))
    assert injected["resource_group_name"] == "rg-techlabs-web-app-dev"
    assert injected["location"] == "westus2"
    assert injected["environment"] == "dev"
    assert injected["tags"]["techlabs-managed"] == "true"


def test_var_file_args_fall_back_to_dev_for_unknown_env(staged):
    work, _ = terraform_exec._prepare_workdir(_dep(environment="staging"))
    args = terraform_exec._var_file_args(_dep(environment="staging"), work)
    assert args == ["-var-file=dev.tfvars"]


def test_var_file_args_select_prod(staged):
    work, _ = terraform_exec._prepare_workdir(_dep(environment="prod"))
    args = terraform_exec._var_file_args(_dep(environment="prod"), work)
    assert args == ["-var-file=prod.tfvars"]


def test_source_dir_preserves_nested_use_case(tmp_path, monkeypatch):
    tf_dir = tmp_path / "_tf"
    _make_module(tf_dir / "landing-zone", "core")
    monkeypatch.setattr(terraform_exec, "TF_DIR", tf_dir)

    src = terraform_exec._source_dir("landing-zone/core")

    assert src == tf_dir / "landing-zone" / "core"
    assert (src / "main.tf").exists()


def test_prepare_workdir_resolves_nested_landing_zone(tmp_path, monkeypatch):
    tf_dir = tmp_path / "_tf"
    work_dir = tmp_path / "work"
    _make_module(tf_dir / "landing-zone", "core")
    monkeypatch.setattr(terraform_exec, "TF_DIR", tf_dir)
    monkeypatch.setenv("GALLERY_TF_WORK", str(work_dir))
    monkeypatch.delenv("GALLERY_TF_STATE_STORAGE", raising=False)

    dep = _dep(use_case="landing-zone/core", resource_group="rg-techlabs-landing-zone-core-dev")
    work, remote = terraform_exec._prepare_workdir(dep)

    assert remote is False
    assert (work / "main.tf").exists()

