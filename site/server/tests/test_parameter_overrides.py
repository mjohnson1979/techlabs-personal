"""Tests for merging advanced deployment options into ARM parameters.

The API validates the raw name->string map from the client; the executor then
casts each value to the type declared in the compiled template so ARM accepts
it. These tests cover that cast/merge step (a pure function, no Azure SDK).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import azure_deploy  # noqa: E402


_TEMPLATE = {
    "parameters": {
        "prefix": {"type": "string"},
        "appCapacity": {"type": "int"},
        "appZoneRedundant": {"type": "bool"},
        "appServiceSku": {"type": "string"},
    }
}


def test_load_template_accepts_utf8_bom(tmp_path: Path, monkeypatch) -> None:
    template_dir = tmp_path / "demo"
    template_dir.mkdir()
    artifact = {"template": _TEMPLATE, "parameters": {"prefix": {"value": "demo"}}}
    (template_dir / "dev.json").write_text(
        json.dumps(artifact), encoding="utf-8-sig"
    )
    monkeypatch.setattr(azure_deploy, "TEMPLATES_DIR", tmp_path)

    template, parameters = azure_deploy.load_template("demo", "dev")

    assert template == _TEMPLATE
    assert parameters == {"prefix": {"value": "demo"}}


def test_merge_casts_types() -> None:
    parameters: dict = {"prefix": {"value": "techlabs"}}
    azure_deploy._merge_parameter_overrides(
        _TEMPLATE,
        parameters,
        {"appCapacity": "3", "appZoneRedundant": "true", "appServiceSku": "P1v3"},
    )
    assert parameters["appCapacity"] == {"value": 3}
    assert parameters["appZoneRedundant"] == {"value": True}
    assert parameters["appServiceSku"] == {"value": "P1v3"}
    # untouched existing parameter stays
    assert parameters["prefix"] == {"value": "techlabs"}


def test_merge_bool_false() -> None:
    parameters: dict = {}
    azure_deploy._merge_parameter_overrides(
        _TEMPLATE, parameters, {"appZoneRedundant": "false"}
    )
    assert parameters["appZoneRedundant"] == {"value": False}


def test_merge_none_or_empty_is_noop() -> None:
    parameters: dict = {"prefix": {"value": "techlabs"}}
    azure_deploy._merge_parameter_overrides(_TEMPLATE, parameters, None)
    azure_deploy._merge_parameter_overrides(_TEMPLATE, parameters, {})
    assert parameters == {"prefix": {"value": "techlabs"}}


def test_merge_ignores_unknown_param() -> None:
    parameters: dict = {}
    azure_deploy._merge_parameter_overrides(
        _TEMPLATE, parameters, {"notInTemplate": "x"}
    )
    assert parameters == {}


def test_merge_bad_int_skipped() -> None:
    parameters: dict = {}
    azure_deploy._merge_parameter_overrides(
        _TEMPLATE, parameters, {"appCapacity": "not-a-number"}
    )
    assert parameters == {}
