"""Tests for registry-backed validation of model parameter overrides.

Model name/version/SKU params carry no template @allowed set (the registry is
their source of truth), so the API validates them against site/registry.json.
These tests use the committed registry seed (OpenAI models).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import api  # noqa: E402

_ITEM = {
    "parameters": [
        {"name": "chatModelName", "type": "string", "default": "gpt-5-mini"},
        {"name": "chatModelVersion", "type": "string", "default": "2025-08-07"},
        {"name": "chatModelSku", "type": "string", "default": "GlobalStandard"},
    ]
}


def _has_seed_models() -> bool:
    reg = api._registry()
    return bool((reg.get("models") or {}).get("chat"))


def test_valid_model_triplet_passes() -> None:
    if not _has_seed_models():
        pytest.skip("registry.json has no models")
    out = api._validate_parameter_values(
        {"chatModelName": "gpt-4o", "chatModelVersion": "2024-11-20", "chatModelSku": "Standard"},
        _ITEM,
    )
    assert out["chatModelName"] == "gpt-4o"


def test_unknown_model_rejected() -> None:
    if not _has_seed_models():
        pytest.skip("registry.json has no models")
    with pytest.raises(HTTPException) as ei:
        api._validate_parameter_values({"chatModelName": "gpt-does-not-exist"}, _ITEM)
    assert ei.value.status_code == 400


def test_invalid_version_rejected() -> None:
    if not _has_seed_models():
        pytest.skip("registry.json has no models")
    with pytest.raises(HTTPException):
        api._validate_parameter_values(
            {"chatModelName": "gpt-4o", "chatModelVersion": "1999-01-01"}, _ITEM
        )


def test_invalid_sku_rejected() -> None:
    if not _has_seed_models():
        pytest.skip("registry.json has no models")
    with pytest.raises(HTTPException):
        api._validate_parameter_values(
            {"chatModelName": "gpt-4o", "chatModelVersion": "2024-11-20", "chatModelSku": "NoSuchSku"},
            _ITEM,
        )


def test_default_untouched_when_not_overridden() -> None:
    out = api._validate_parameter_values({}, _ITEM)
    assert out == {}


def test_non_scalar_param_rejected() -> None:
    item = {"parameters": [{"name": "tags", "type": "object", "default": {}}]}
    with pytest.raises(HTTPException) as ei:
        api._validate_parameter_values({"tags": "x=y"}, item)
    assert ei.value.status_code == 400



_AKS_ITEM = {
    "parameters": [
        {"name": "kubernetesVersion", "type": "string", "default": "1.30"},
        {"name": "systemNodeVmSize", "type": "string", "default": "Standard_D4s_v5"},
    ]
}


def _has_sku_family(name: str) -> bool:
    return bool(api._registry_sku_values(name))


def test_kubernetes_version_valid() -> None:
    if not _has_sku_family("kubernetesVersion"):
        pytest.skip("registry has no kubernetesVersion family")
    out = api._validate_parameter_values({"kubernetesVersion": "1.31"}, _AKS_ITEM)
    assert out["kubernetesVersion"] == "1.31"


def test_kubernetes_version_invalid() -> None:
    if not _has_sku_family("kubernetesVersion"):
        pytest.skip("registry has no kubernetesVersion family")
    with pytest.raises(HTTPException):
        api._validate_parameter_values({"kubernetesVersion": "9.99"}, _AKS_ITEM)


def test_vm_size_valid_via_shared_family() -> None:
    if not _has_sku_family("systemNodeVmSize"):
        pytest.skip("registry has no vmSize family")
    out = api._validate_parameter_values({"systemNodeVmSize": "Standard_D8s_v5"}, _AKS_ITEM)
    assert out["systemNodeVmSize"] == "Standard_D8s_v5"


def test_vm_size_invalid_rejected() -> None:
    if not _has_sku_family("systemNodeVmSize"):
        pytest.skip("registry has no vmSize family")
    with pytest.raises(HTTPException):
        api._validate_parameter_values({"systemNodeVmSize": "Standard_NoSuchSize"}, _AKS_ITEM)


_SQL_ITEM = {
    "parameters": [
        {"name": "sqlTier", "type": "string", "default": "GeneralPurpose"},
        {"name": "sqlSkuName", "type": "string", "default": "GP_S_Gen5_1"},
    ]
}


def test_sql_tier_and_sku_valid() -> None:
    if not _has_sku_family("sqlTier"):
        pytest.skip("registry has no sqlTier family")
    out = api._validate_parameter_values(
        {"sqlTier": "BusinessCritical", "sqlSkuName": "BC_Gen5_2"}, _SQL_ITEM
    )
    assert out["sqlTier"] == "BusinessCritical"
    assert out["sqlSkuName"] == "BC_Gen5_2"


def test_sql_tier_invalid_rejected() -> None:
    if not _has_sku_family("sqlTier"):
        pytest.skip("registry has no sqlTier family")
    with pytest.raises(HTTPException):
        api._validate_parameter_values({"sqlTier": "PlatinumDeluxe"}, _SQL_ITEM)


