"""Unit tests for the opt-in Terraform binary bootstrap.

These run offline. They lock in the safety properties that matter for boot:

* a pre-set ``GALLERY_TERRAFORM_BIN`` short-circuits (no download, no env churn);
* with auto-install disabled (the default), nothing is provisioned and no network
  access is attempted;
* an already-present binary in the home dir is detected and exported.

The actual download path is intentionally not exercised (it requires network and a
writable persistent share); the logic is structured so those branches are only the
HTTPS fetch + checksum verify.

Run: ``python -m pytest site/server/tests/test_terraform_bootstrap.py``.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import terraform_bootstrap  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("GALLERY_TERRAFORM_BIN", "GALLERY_TERRAFORM_AUTO_INSTALL", "GALLERY_TERRAFORM_HOME"):
        monkeypatch.delenv(key, raising=False)
    yield


def _explode(*_args, **_kwargs):  # pragma: no cover - only hit on regression
    raise AssertionError("network access attempted")


def test_preset_binary_short_circuits(tmp_path, monkeypatch):
    binary = tmp_path / "terraform"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("GALLERY_TERRAFORM_BIN", str(binary))
    monkeypatch.setattr(terraform_bootstrap.urllib.request, "urlopen", _explode)

    assert terraform_bootstrap.ensure_terraform() == str(binary)


def test_disabled_by_default_no_network(monkeypatch):
    monkeypatch.setattr(terraform_bootstrap.urllib.request, "urlopen", _explode)

    assert terraform_bootstrap.ensure_terraform() is None


def test_existing_home_binary_is_detected(tmp_path, monkeypatch):
    home = tmp_path / "bin"
    home.mkdir()
    binary = home / "terraform"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("GALLERY_TERRAFORM_HOME", str(home))
    monkeypatch.setenv("GALLERY_TERRAFORM_AUTO_INSTALL", "true")
    monkeypatch.setattr(terraform_bootstrap.urllib.request, "urlopen", _explode)

    result = terraform_bootstrap.ensure_terraform()

    assert result == str(binary)
    assert os.environ["GALLERY_TERRAFORM_BIN"] == str(binary)


def test_failed_install_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_TERRAFORM_HOME", str(tmp_path / "bin"))
    monkeypatch.setenv("GALLERY_TERRAFORM_AUTO_INSTALL", "1")
    monkeypatch.setattr(terraform_bootstrap.urllib.request, "urlopen", _explode)

    assert terraform_bootstrap.ensure_terraform() is None
