"""Best-effort provisioning of the Terraform binary on App Service.

The runtime host has no Terraform CLI by default, and a binary shipped inside the
deployment package loses its executable bit during Oryx/zip deploy. This module
fetches a pinned Terraform release into the App Service persistent ``/home`` share
(which survives restarts and keeps the executable bit), so the in-process executor
(:mod:`terraform_exec`) can find it via ``GALLERY_TERRAFORM_BIN``.

It is **opt-in**: nothing happens unless ``GALLERY_TERRAFORM_AUTO_INSTALL`` is set
truthy. Local development, CI, and tests are therefore never affected, and an
operator who prefers to pre-stage the binary can simply set ``GALLERY_TERRAFORM_BIN``
and leave auto-install off.

Integrity: the release zip is downloaded over HTTPS from the official HashiCorp
host and verified against the SHA256 published in that release's ``SHA256SUMS``
(also fetched over HTTPS). The version is pinned below. This guards against
corrupt/partial downloads and accidental version drift; for a stronger supply-chain
posture, pre-stage a vetted binary and point ``GALLERY_TERRAFORM_BIN`` at it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("terraform_bootstrap")

# Pinned Terraform release. Keep in step with the version used for local
# validation so module syntax matches what the host runs.
_TF_VERSION = "1.9.8"
_TF_PLATFORM = "linux_amd64"
_RELEASES_BASE = "https://releases.hashicorp.com/terraform"

_DEFAULT_HOME = "/home/bin"
_DOWNLOAD_TIMEOUT = 60


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _target_path() -> Path:
    home = os.environ.get("GALLERY_TERRAFORM_HOME", "").strip() or _DEFAULT_HOME
    return Path(home) / "terraform"


def _zip_url() -> str:
    return f"{_RELEASES_BASE}/{_TF_VERSION}/terraform_{_TF_VERSION}_{_TF_PLATFORM}.zip"


def _sums_url() -> str:
    return f"{_RELEASES_BASE}/{_TF_VERSION}/terraform_{_TF_VERSION}_SHA256SUMS"


def _expected_sha256() -> str | None:
    """Fetch and parse the SHA256 for the pinned platform zip from SHA256SUMS."""
    zip_name = f"terraform_{_TF_VERSION}_{_TF_PLATFORM}.zip"
    with urllib.request.urlopen(_sums_url(), timeout=_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310 (pinned HTTPS host)
        text = resp.read().decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == zip_name:
            return parts[0].lower()
    return None


def _already_usable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def ensure_terraform() -> str | None:
    """Resolve a usable Terraform binary, downloading it once if permitted.

    Returns the binary path (and exports ``GALLERY_TERRAFORM_BIN``) when available,
    otherwise ``None``. Never raises: provisioning is best-effort and the UI hides
    in-app Terraform deploys whenever the binary is absent.
    """
    explicit = os.environ.get("GALLERY_TERRAFORM_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit

    if not _truthy(os.environ.get("GALLERY_TERRAFORM_AUTO_INSTALL")):
        return None

    target = _target_path()
    if _already_usable(target):
        os.environ["GALLERY_TERRAFORM_BIN"] = str(target)
        return str(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = _expected_sha256()
        if not expected:
            log.warning("terraform bootstrap: no SHA256 for %s; skipping install", _TF_PLATFORM)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "terraform.zip"
            with urllib.request.urlopen(_zip_url(), timeout=_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310 (pinned HTTPS host)
                zip_path.write_bytes(resp.read())

            digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            if digest != expected:
                log.error("terraform bootstrap: checksum mismatch (got %s, want %s)", digest, expected)
                return None

            with zipfile.ZipFile(zip_path) as zf:
                member = "terraform"
                if member not in zf.namelist():
                    log.error("terraform bootstrap: zip missing 'terraform' entry")
                    return None
                # Stage the extracted binary inside the *target* directory so the
                # final rename is same-filesystem. On App Service Linux, /tmp and
                # /home are separate mounts, so renaming across them raises
                # OSError (cross-device link) and the install would silently fail.
                staged = target.parent / f".terraform.tmp-{os.getpid()}"
                try:
                    with zf.open(member) as src, open(staged, "wb") as dst:
                        dst.write(src.read())
                    mode = staged.stat().st_mode
                    staged.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    os.replace(staged, target)
                finally:
                    if staged.exists():
                        staged.unlink()

        os.environ["GALLERY_TERRAFORM_BIN"] = str(target)
        log.info("terraform bootstrap: installed %s at %s", _TF_VERSION, target)
        return str(target)
    except Exception as exc:  # noqa: BLE001 - boot must never fail on this
        log.warning("terraform bootstrap: install skipped (%s)", exc)
        return None
