"""Run Terraform deployments with the App Service managed identity.

Companion to ``azure_deploy.py``: when a deployment's ``engine`` is ``terraform``
the executor runs the use case's Terraform module instead of submitting ARM JSON.

Customer Terraform sources are supplied under ``site/server/_tf/<use_case>/``
or synchronized from a reviewed customer repository commit.

Execution model
---------------
* Binary: ``GALLERY_TERRAFORM_BIN`` or ``terraform`` on PATH.
* Auth: the ``azurerm`` provider reads ``ARM_SUBSCRIPTION_ID`` and, in App Service,
  ``ARM_USE_MSI=true`` (managed identity). Locally it falls back to ``az login``.
* State: when ``GALLERY_TF_STATE_STORAGE`` is set the run uses an ``azurerm`` remote
  backend (one state blob per deployment id); otherwise local state under a stable
  per-deployment working directory so teardown can find it.

No secrets are written to disk or logged. Injected values (resource group, region,
tags) go into an ``*.auto.tfvars.json`` file to avoid any shell quoting/injection.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import db

SERVER_DIR = Path(__file__).resolve().parent
TF_DIR = SERVER_DIR / "_tf"

# Per-deployment working directories persist across apply/destroy so local-state
# runs can be torn down. /home is the App Service persistent share.
_DEFAULT_WORK = "/home/data/tf-runs"

_ENV_NAMES = {"dev", "prod"}


def terraform_available() -> tuple[bool, str]:
    """Return (ok, reason) — whether the terraform binary is resolvable."""
    binary = _terraform_bin()
    if not binary:
        return False, "Terraform binary not found (set GALLERY_TERRAFORM_BIN or add terraform to PATH)"
    return True, "ok"


def _terraform_bin() -> str | None:
    explicit = os.environ.get("GALLERY_TERRAFORM_BIN", "").strip()
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which("terraform")


def _source_dir(use_case: str) -> Path:
    # use_case mirrors the catalog path (e.g. "web-app" or "landing-zone/core").
    # Sanitize each segment but keep the nested structure so the module staged by
    # Build-Templates at _tf/<path> is found. Empty/traversal segments are dropped;
    # use_case is already validated against the catalog by the API layer.
    segments = [re.sub(r"[^a-z0-9-]", "", seg.lower()) for seg in use_case.split("/")]
    segments = [seg for seg in segments if seg]
    if not segments:
        return TF_DIR / "__invalid__"
    return TF_DIR.joinpath(*segments)


def _work_root() -> Path:
    configured = os.environ.get("GALLERY_TF_WORK", "").strip()
    if configured:
        return Path(configured)
    home = Path("/home")
    if home.is_dir() and os.access(home, os.W_OK):
        return Path(_DEFAULT_WORK)
    return SERVER_DIR / "_tf_runs"


def _location(dep: dict[str, Any]) -> str:
    loc = (dep.get("params") or {}).get("location")
    if loc:
        return str(loc)
    return os.environ.get("GALLERY_DEPLOY_LOCATION", "centralus").strip() or "centralus"


_CATALOG_PATH = SERVER_DIR.parent / "catalog.json"


def _camel_to_snake(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def _param_types(use_case: str) -> dict[str, str]:
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    for it in data.get("items", []):
        if it.get("path") == use_case:
            return {
                p["name"]: str(p.get("type") or "string").lower()
                for p in (it.get("parameters") or [])
                if isinstance(p, dict) and p.get("name")
            }
    return {}


# Advanced deployment options -> Terraform variables. Bicep params are camelCase;
# the modules declare the snake_case equivalent, so convert names and cast each
# value to its catalog type (Terraform is strict about number/bool vs string).
def _parameter_overrides(dep: dict[str, Any]) -> dict[str, Any]:
    raw = (dep.get("params") or {}).get("parameterValues") or {}
    if not raw:
        return {}
    types = _param_types(dep["use_case"])
    if (dep.get("params") or {}).get("catalogVersion"):
        types = (dep.get("params") or {}).get("parameterTypes", {})
    out: dict[str, Any] = {}
    for name, value in raw.items():
        sval = str(value)
        ptype = types.get(name, "string")
        if ptype == "int":
            try:
                cast: Any = int(sval)
            except ValueError:
                continue
        elif ptype == "bool":
            cast = sval.strip().lower() == "true"
        else:
            cast = sval
        out[_camel_to_snake(name)] = cast
    return out


def _injected_tags(dep: dict[str, Any]) -> dict[str, str]:
    return {
        "techlabs-managed": "true",
        "techlabs-deployment": dep["id"],
        "techlabs-usecase": dep["use_case"],
        "techlabs-owner": dep.get("user_upn", ""),
    }


def _backend_config(dep: dict[str, Any]) -> list[str] | None:
    """Return ``-backend-config`` flags for the azurerm remote backend, or None
    to fall back to local state."""
    storage = os.environ.get("GALLERY_TF_STATE_STORAGE", "").strip()
    if not storage:
        return None
    rg = os.environ.get("GALLERY_TF_STATE_RG", "").strip()
    container = os.environ.get("GALLERY_TF_STATE_CONTAINER", "tfstate").strip() or "tfstate"
    key = f"{dep['use_case']}/{dep['id']}.tfstate"
    flags = [
        f"-backend-config=storage_account_name={storage}",
        f"-backend-config=container_name={container}",
        f"-backend-config=key={key}",
        "-backend-config=use_msi=true" if _use_msi() else "-backend-config=use_azuread_auth=true",
    ]
    if rg:
        flags.append(f"-backend-config=resource_group_name={rg}")
    return flags


def _use_msi() -> bool:
    from distribution import is_customer_distribution

    if is_customer_distribution():
        return True
    return os.environ.get("ARM_USE_MSI", "").strip().lower() in ("1", "true", "yes")


def _write_backend_file(work: Path, remote: bool) -> None:
    if remote:
        content = 'terraform {\n  backend "azurerm" {}\n}\n'
    else:
        content = 'terraform {\n  backend "local" {}\n}\n'
    (work / "backend.tf").write_text(content, encoding="utf-8")


def _prepare_workdir(dep: dict[str, Any]) -> tuple[Path, bool]:
    src = _source_dir(dep["use_case"])
    version = (dep.get("params") or {}).get("catalogVersion")
    if version:
        from setup_github import snapshot_root
        src = snapshot_root(version) / "_tf" / dep["use_case"]
    if not src.is_dir():
        raise RuntimeError(
            f"No Terraform module for {dep['use_case']}. "
            "Publish the customer module through the configured repository."
        )
    work = _work_root() / dep["id"]
    work.mkdir(parents=True, exist_ok=True)

    # Copy source .tf/.tfvars over any prior copy, but never clobber existing
    # local state or the provider cache so teardown can reuse them.
    for item in src.iterdir():
        if item.is_file() and item.suffix in (".tf", ".tfvars", ".json"):
            shutil.copy2(item, work / item.name)

    remote = _backend_config(dep) is not None
    _write_backend_file(work, remote)

    injected = {
        "resource_group_name": dep["resource_group"],
        "location": _location(dep),
        "environment": dep["environment"],
        "tags": _injected_tags(dep),
    }
    injected.update(_parameter_overrides(dep))
    (work / "injected.auto.tfvars.json").write_text(
        json.dumps(injected, indent=2), encoding="utf-8"
    )
    return work, remote


def _run_env(dep: dict[str, Any]) -> dict[str, str]:
    from distribution import is_customer_distribution

    env = dict(os.environ)
    env["ARM_SUBSCRIPTION_ID"] = dep.get("subscription_id", "") or env.get(
        "ARM_SUBSCRIPTION_ID", ""
    )
    env.setdefault("TF_IN_AUTOMATION", "1")
    if is_customer_distribution():
        from setup_config import subscription_for

        try:
            if not dep.get("subscription_id"):
                raise ValueError
            subscription_for(dep["subscription_id"])
        except ValueError:
            raise RuntimeError("subscription is outside the customer installation") from None
        env["ARM_USE_MSI"] = "true"
        env["ARM_USE_CLI"] = "false"
        env["ARM_USE_OIDC"] = "false"
        env["ARM_CLIENT_ID"] = env.get("AZURE_CLIENT_ID", "")
        env["ARM_TENANT_ID"] = env.get("GALLERY_ENTRA_TENANT_ID", "")
        for name in tuple(env):
            if name.startswith(("ARM_CLIENT_SECRET", "ARM_CLIENT_CERTIFICATE", "ARM_OIDC", "ARM_ACCESS_KEY", "ARM_SAS_TOKEN")):
                env.pop(name)
    env.setdefault("TF_INPUT", "0")
    env.setdefault("CHECKPOINT_DISABLE", "1")
    return env


def _var_file_args(dep: dict[str, Any], work: Path) -> list[str]:
    env_name = dep["environment"] if dep["environment"] in _ENV_NAMES else "dev"
    var_file = work / f"{env_name}.tfvars"
    return [f"-var-file={var_file.name}"] if var_file.exists() else []


def _terraform(args: list[str], work: Path, env: dict[str, str]) -> subprocess.Popen:
    binary = _terraform_bin()
    if not binary:
        raise RuntimeError("Terraform binary not found")
    return subprocess.Popen(
        [binary, *args],
        cwd=str(work),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _init(work: Path, env: dict[str, str], backend_config: list[str] | None) -> None:
    args = ["init", "-input=false", "-no-color"]
    if backend_config:
        args += backend_config
    proc = _terraform(args, work, env)
    out, _ = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(_clean_tf_output(out, "terraform init failed"))


def _stream_apply_progress(proc: subprocess.Popen, dep_id: str, action: str) -> str:
    """Read terraform -json output, push coarse progress to the deployment log,
    and return the captured diagnostics for error reporting."""
    completed: list[str] = []
    total: int | None = None
    last_diag = ""
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        etype = evt.get("type", "")
        if etype == "change_summary":
            changes = evt.get("changes", {})
            total = changes.get(action) or changes.get("add")
        elif etype in ("apply_complete", "apply_errored"):
            hook = evt.get("hook", {})
            addr = hook.get("resource", {}).get("addr", "")
            if addr:
                completed.append(addr)
        elif etype == "diagnostic":
            diag = evt.get("diagnostic", {})
            summary = diag.get("summary", "")
            detail = diag.get("detail", "")
            if diag.get("severity") == "error" and summary:
                last_diag = " ".join(f"{summary} {detail}".split())[:600]
        done = len(completed)
        head = f"{done}/{total} resources {action}d" if total else f"{done} resources {action}d"
        recent = completed[-1].split(".")[-1] if completed else ""
        detail_line = f"{head} - {recent}" if recent else head
        db.update_deployment(dep_id, detail=detail_line)
    proc.wait()
    if last_diag:
        return last_diag
    return "\n".join(tail[-6:])


def _clean_tf_output(out: str, fallback: str) -> str:
    text = " ".join((out or "").split()).strip()
    return text[:600] if text else fallback


# --------------------------------------------------------------------------- #
# Public blocking entry points (call via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def tf_apply(dep: dict[str, Any]) -> None:
    work, remote = _prepare_workdir(dep)
    env = _run_env(dep)
    _init(work, env, _backend_config(dep))
    args = ["apply", "-input=false", "-auto-approve", "-no-color", "-json"]
    args += _var_file_args(dep, work)
    proc = _terraform(args, work, env)
    diag = _stream_apply_progress(proc, dep["id"], "create")
    if proc.returncode != 0:
        raise RuntimeError(diag or "terraform apply failed")


def tf_destroy(dep: dict[str, Any]) -> None:
    work, remote = _prepare_workdir(dep)
    env = _run_env(dep)
    _init(work, env, _backend_config(dep))
    args = ["destroy", "-input=false", "-auto-approve", "-no-color", "-json"]
    args += _var_file_args(dep, work)
    proc = _terraform(args, work, env)
    diag = _stream_apply_progress(proc, dep["id"], "destroy")
    if proc.returncode != 0:
        raise RuntimeError(diag or "terraform destroy failed")
    # Best-effort cleanup of the local working directory once state is gone.
    if not remote:
        shutil.rmtree(work, ignore_errors=True)
