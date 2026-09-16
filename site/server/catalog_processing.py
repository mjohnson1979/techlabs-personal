"""Portal-owned catalog processing and conservative runner availability checks."""

from __future__ import annotations

import time
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

import db
import pricing
import setup_cloud
import setup_config


class ProcessingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["container", "github-hosted", "self-hosted"] = "container"
    runnerLabels: list[str] = Field(default_factory=list, max_length=20)
    capacityRegions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("runnerLabels", "capacityRegions")
    @classmethod
    def distinct_values(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value) for value in values):
            raise ValueError("Use literal runner labels or canonical region names")
        return list(dict.fromkeys(values))

    @field_validator("capacityRegions")
    @classmethod
    def canonical_regions(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-z][a-z0-9]{2,30}", value) for value in values):
            raise ValueError("Use canonical lowercase Azure region names")
        return values


def settings() -> dict:
    return ProcessingSettings.model_validate(db.get_setup_setting("catalog_processing", {})).model_dump()


async def probe_runners(repo: str, request: Callable[..., Awaitable]) -> dict:
    hosted = {"available": False, "state": "unverified", "detail": "No recent successful GitHub-hosted job was verified for this repository."}
    hosted_labels = []
    self_hosted = {"available": False, "state": "unverified", "detail": "Self-hosted runner access could not be verified.", "candidates": []}
    result = {"repository": repo, "checkedAt": time.time(), "github-hosted": hosted, "self-hosted": self_hosted}
    try:
        policy = await request(f"/repos/{repo}/actions/permissions")
        if policy.get("enabled") is not True:
            for option in (hosted, self_hosted):
                option.update(state="unavailable", detail="GitHub Actions is disabled for this repository.")
            return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        hosted["detail"] = "Actions policy could not be verified with this connection. Container processing remains available."
        return result
    try:
        runners = await request(f"/repos/{repo}/actions/runners", params={"per_page": 100})
        candidates = []
        for runner in runners["runners"]:
            labels = [label["name"] for label in runner.get("labels", [])]
            if runner.get("os") == "linux" and runner.get("status") == "online" and {"self-hosted", "linux", "x64"} <= {label.lower() for label in labels}:
                candidates.append({"labels": labels, "busy": bool(runner.get("busy"))})
        self_hosted.update(available=bool(candidates), state="available" if candidates else "unavailable", candidates=candidates,
                           detail="Compatible online Linux x64 runners found; tools and isolation must meet the prerequisites." if candidates else "No compatible online self-hosted runner is visible for this repository.")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    try:
        runs = await request(f"/repos/{repo}/actions/runs", params={"per_page": 5})
        for run in runs["workflow_runs"]:
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - created).total_seconds() > 7 * 86400:
                continue
            jobs = await request(f"/repos/{repo}/actions/runs/{int(run['id'])}/jobs", params={"per_page": 30})
            for job in jobs["jobs"]:
                if job.get("conclusion") == "success" and job.get("runner_id") and job.get("runner_group_name") == "GitHub Actions" and job.get("labels") == ["ubuntu-latest"]:
                    hosted_labels = ["ubuntu-latest"]
                if job.get("conclusion") == "failure" and not job.get("runner_id") and "ubuntu-latest" in job.get("labels", []):
                    hosted.update(state="unavailable", detail="A recent ubuntu-latest job failed before receiving a runner. Use container processing until access is resolved.")
                    return result
        if hosted_labels:
            hosted.update(available=True, state="available", detail="A GitHub-hosted ubuntu-latest job succeeded in this repository within seven days. This is recent evidence, not a reservation.")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    return result


def runner_available(configuration: dict, probe: dict) -> bool:
    mode = configuration["mode"]
    if mode == "container":
        return True
    if not probe.get(mode, {}).get("available"):
        return False
    if mode == "self-hosted":
        labels = set(configuration["runnerLabels"])
        return bool(labels) and any(labels == set(candidate["labels"]) for candidate in probe[mode]["candidates"])
    return True


def compiler_sandbox(work: Path) -> None:
    import ctypes
    import platform
    import resource

    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
        raise ValueError("Container validation requires the Linux x64 sandbox")
    libc = ctypes.CDLL(None, use_errno=True)

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("value", ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1 or libc.prctl(38, 1, 0, 0, 0) != 0:
        raise ValueError("Kernel filesystem sandbox is unavailable")
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= 1 << 13
    if abi >= 3:
        handled |= 1 << 14
    ruleset = Ruleset(handled)
    descriptor = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    if descriptor < 0:
        raise ValueError("Cannot create compiler filesystem sandbox")
    try:
        paths = [(work, handled)]
        paths.extend((Path(path), 1 | 4 | 8) for path in ("/usr/local/bin", "/usr/lib", "/lib", "/lib64", "/etc/ssl", "/sys/fs/cgroup", "/sys/devices/system/cpu"))
        paths.extend((Path(path), 4) for path in ("/etc/ld.so.cache", "/etc/localtime", "/dev/urandom", "/dev/random",
                 "/proc/meminfo", "/proc/cpuinfo", "/proc/stat", "/proc/self/status", "/proc/self/stat", "/proc/self/statm", "/proc/self/maps", "/proc/self/mountinfo"))
        paths.append((Path("/dev/null"), 2 | 4))
        for path, rights in paths:
            if not path.exists():
                continue
            opened = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(rights, opened)
                if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) != 0:
                    raise ValueError("Cannot restrict compiler filesystem access")
            finally:
                os.close(opened)
        if libc.syscall(446, descriptor, 0) != 0:
            raise ValueError("Cannot enforce compiler filesystem sandbox")
    finally:
        os.close(descriptor)
    filters = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, 0xC000003E), Filter(0x06, 0, 0, 0x80000000), Filter(0x20, 0, 0, 0)]
    for number in (41, 42, 43, 49, 50, 53, 101, 165, 166, 272, 288, 298, 308, 310, 311, 321, 425, 426, 427, 428):
        filters.extend((Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, 0x00050001)))
    filters.extend((Filter(0x35, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x80000000), Filter(0x06, 0, 0, 0x7FFF0000)))
    instructions = (Filter * len(filters))(*filters)
    program = Program(len(filters), instructions)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise ValueError("Cannot enforce compiler network sandbox")
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024 * 1024 * 1024, 8 * 1024 * 1024 * 1024))


async def compile_source(work: Path, tool: str, arguments: list[str]) -> str:
    if tool not in ("bicep", "terraform"):
        raise ValueError("Unsupported compiler")
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work),
                   "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(work / ".dotnet"), "DOTNET_EnableDiagnostics": "0",
                   "DOTNET_GCHeapHardLimit": "0x10000000", "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1",
                   "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1", "GOMEMLIMIT": "256MiB"}
    with tempfile.TemporaryFile() as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), "--sandbox", str(work), tool, *arguments,
            cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=60)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            raise ValueError(f"{tool} validation failed. Check source syntax, local module references and container sandbox prerequisites; compiler output is withheld to protect file contents.")
        output.seek(0)
        content = output.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("Compiler output exceeds the validation limit")
        return content.decode("utf-8")


async def process_blobs(blobs: dict[str, bytes]) -> dict[str, bytes]:
    generated = dict(blobs)
    with tempfile.TemporaryDirectory(prefix="portal-validate-") as directory:
        work = Path(directory)
        for name, content in blobs.items():
            destination = work / name
            if destination.resolve().is_relative_to(work) is False or "\\" in name:
                raise ValueError("Invalid catalog source path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        for name in list(blobs):
            if not re.fullmatch(r"catalog/[^/]+/metadata\.json", name):
                continue
            prefix = name.rsplit("/", 1)[0]
            metadata = json.loads(blobs[name])
            if not isinstance(metadata, dict) or not isinstance(metadata.get("displayName"), str):
                raise ValueError("Each workload needs metadata with a displayName")
            validation = []
            if f"{prefix}/main.bicep" in blobs:
                template = json.loads(await compile_source(work, "bicep", ["build", f"{prefix}/main.bicep", "--stdout", "--no-restore"]))
                for environment in ("dev", "prod"):
                    parameter_path = f"{prefix}/parameters/{environment}.bicepparam"
                    target = f"{prefix}/compiled/{environment}.json"
                    generated.pop(target, None)
                    if parameter_path in blobs:
                        result = json.loads(await compile_source(work, "bicep", ["build-params", parameter_path, "--stdout", "--no-restore"]))
                        compiled = json.loads(result["templateJson"])
                        parameters = json.loads(result["parametersJson"])["parameters"]
                        if compiled != template:
                            raise ValueError("Parameter file must reference this workload's main.bicep")
                    elif environment == "dev":
                        compiled, parameters = template, {}
                    else:
                        continue
                    for key, value in parameters.items():
                        if str(compiled.get("parameters", {}).get(key, {}).get("type", "")).lower() in ("securestring", "secureobject") and "value" in value:
                            raise ValueError("Do not publish literal secure parameter values")
                    generated[target] = json.dumps({"template": compiled, "parameters": parameters}).encode()
                validation.append("bicep-compiled")
            if f"{prefix}/terraform/main.tf" in blobs:
                await compile_source(work, "terraform", ["fmt", "-write=false", f"{prefix}/terraform"])
                validation.append("terraform-syntax")
            for target in (f"{prefix}/compiled/dev.json", f"{prefix}/compiled/prod.json"):
                if target in generated:
                    document = json.loads(generated[target])
                    if not isinstance(document.get("template"), dict) or not isinstance(document.get("parameters", {}), dict):
                        raise ValueError("Invalid compiled ARM input")
            if not validation and f"{prefix}/compiled/dev.json" in generated:
                validation.append("arm-json-structure")
            generated.pop(f"{prefix}/pricing.json", None)
            cost = metadata.get("cost")
            if cost and cost.get("components"):
                fresh = {**cost, "components": [{key: value for key, value in row.items() if key not in ("unitPrice", "monthly")} for row in cost["components"]]}
                priced = await asyncio.wait_for(pricing.estimate(fresh, cost.get("region", "eastus"), {}), timeout=120)
                if not priced.get("complete"):
                    raise ValueError("Retail prices are incomplete; the previous catalog remains active")
                priced["components"] = [{**authored, **row} for authored, row in zip(cost["components"], priced["components"], strict=True)]
                generated[f"{prefix}/pricing.json"] = json.dumps({"metadataSha256": hashlib.sha256(blobs[name]).hexdigest(),
                    "cost": {**cost, **priced, "pricedAt": datetime.now(timezone.utc).isoformat(), "pricingSource": "retail"}}).encode()
            metadata["validation"] = {"status": "passed" if validation else "no-deployment-input", "checks": validation,
                                      "detail": "Static validation only; Azure deployment feasibility and Terraform provider schemas are checked separately."}
            generated[name] = json.dumps(metadata).encode()
            if f"{prefix}/pricing.json" in generated:
                price = json.loads(generated[f"{prefix}/pricing.json"])
                price["metadataSha256"] = hashlib.sha256(generated[name]).hexdigest()
                generated[f"{prefix}/pricing.json"] = json.dumps(price).encode()
    return generated


async def refresh_capacity(regions: list[str]) -> dict | None:
    subscriptions = setup_config.subscriptions()
    if not regions or not subscriptions:
        return None
    if len(subscriptions) * len(regions) > 100:
        raise ValueError("Capacity refresh supports at most 100 subscription/region combinations")
    snapshots = {}
    for subscription in subscriptions:
        identifier = subscription["subscriptionId"]
        await setup_cloud.verify_subscription(subscription)
        snapshots[identifier] = {}
        for region in regions:
            if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region):
                raise ValueError("Use canonical Azure region names")
            result = await setup_cloud.arm_request(f"/subscriptions/{identifier}/providers/Microsoft.Compute/locations/{region}/usages?api-version=2024-03-01")
            if result.get("nextLink") or not isinstance(result.get("value"), list):
                raise ValueError("Incomplete quota response; previous capacity remains active")
            rows = []
            for entry in result["value"]:
                limit, current = entry.get("limit"), entry.get("currentValue")
                if any(type(value) is not int or value < 0 for value in (limit, current)):
                    raise ValueError("Invalid quota counters")
                rows.append({"name": entry["name"].get("localizedValue") or entry["name"]["value"], "limit": limit,
                             "current": current, "available": max(0, limit - current)})
            snapshots[identifier][region] = rows
    return {"schemaVersion": 1, "generated": datetime.now(timezone.utc).isoformat(),
            "tenantId": os.environ.get("GALLERY_ENTRA_TENANT_ID", ""), "physicalCapacity": "unknown", "subscriptions": snapshots}


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] != "--sandbox" or sys.argv[3] not in ("bicep", "terraform"):
        raise SystemExit(2)
    compiler_sandbox(Path(sys.argv[2]).resolve())
    os.execv(f"/usr/local/bin/{sys.argv[3]}", [sys.argv[3], *sys.argv[4:]])