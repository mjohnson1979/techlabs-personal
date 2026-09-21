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

REGIONAL_INTERVAL = 86400
REGIONAL_CONCURRENCY = asyncio.Semaphore(6)


def regional_input_key(item: dict, subscription: dict) -> str:
    cost = item.get("cost") or {}
    authored_cost = {key: value for key, value in cost.items() if key not in (
        "components", "fixedMonthly", "usageMonthly", "totalMonthly", "complete", "pricedAt", "pricingSource", "fallbackReason")}
    authored_cost["components"] = [{key: value for key, value in component.items() if key not in ("unitPrice", "monthly")}
                                   for component in cost.get("components", [])]
    payload = {"path": item.get("path", item.get("name")), "cost": authored_cost,
               "quota": item.get("quota"), "parameters": item.get("parameters"),
               "subscriptionId": subscription["subscriptionId"], "pricingMode": subscription.get("pricingMode", "retail")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def default_parameters(item: dict) -> dict[str, str]:
    return {entry["name"]: str(entry["default"]).lower() if isinstance(entry["default"], bool) else str(entry["default"])
            for entry in item.get("parameters", []) if isinstance(entry, dict) and entry.get("name")
            and entry.get("default") is not None and entry.get("type", "").lower() not in ("securestring", "secureobject", "object", "array")}


def regional_results(items: list[dict], subscriptions: list[dict]) -> dict:
    stored = db.get_setup_setting("regional_snapshot", {})
    keys = {(subscription["subscriptionId"], item.get("path", item.get("name"))): regional_input_key(item, subscription)
            for subscription in subscriptions for item in items}
    rows = []
    retail_keys = {(subscription["subscriptionId"], item.get("path", item.get("name"))): regional_input_key(item, {**subscription, "pricingMode": "retail"})
                   for subscription in subscriptions for item in items}
    for row in stored.get("rows", []):
        identity = (row.get("subscriptionId"), row.get("useCase"))
        if identity not in keys:
            continue
        runner_retail = stored.get("source") == "runner" and retail_keys.get(identity) == row.get("inputKey")
        if keys.get(identity) != row.get("inputKey") and not runner_retail:
            continue
        if runner_retail and keys.get(identity) != row.get("inputKey") and row.get("price"):
            reason = "Runner snapshots contain public retail prices only. Live deployment checks use the saved negotiated pricing preference."
            row = {**row, "priceDetail": reason, "price": {**row["price"], "fallbackReason": reason, "requestedPricing": "negotiated"}}
        rows.append({**row, "stale": time.time() - row.get("checkedAt", 0) >= REGIONAL_INTERVAL})
    allowed = {subscription["subscriptionId"] for subscription in subscriptions}
    return {"rows": rows, "generatedAt": stored.get("generatedAt"), "intervalSeconds": REGIONAL_INTERVAL,
            "source": stored.get("source", "container"), "job": db.get_setup_setting("regional_job", {"status": "pending"}),
            "errors": {key: value for key, value in stored.get("errors", {}).items() if key in allowed}}


def validate_runner_snapshot(document: dict) -> dict:
    import math

    if not isinstance(document, dict) or document.get("schemaVersion") != 1 or document.get("source") != "runner":
        raise ValueError("Invalid runner regional snapshot")
    rows = document.get("rows")
    now = time.time()
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError("Invalid regional snapshot rows")
    clean = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or setup_config.canonical_guid(row.get("subscriptionId", "")) != row.get("subscriptionId"):
            raise ValueError("Invalid regional subscription")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", row.get("useCase", "")) or not re.fullmatch(r"[a-z][a-z0-9]{2,30}", row.get("location", "")):
            raise ValueError("Invalid regional target")
        if not re.fullmatch(r"[a-f0-9]{64}", row.get("inputKey", "")):
            raise ValueError("Missing workload binding")
        if type(row.get("checkedAt")) not in (float, int) or not math.isfinite(row["checkedAt"]) or not 0 < row["checkedAt"] <= now + 300:
            raise ValueError("Invalid regional timestamp")
        identity = (row["subscriptionId"], row["useCase"], row["location"])
        if identity in seen:
            raise ValueError("Duplicate regional result")
        seen.add(identity)
        price = row.get("price")
        if price:
            if not isinstance(price, dict):
                raise ValueError("Invalid regional price")
            if price.get("pricingSource") != "retail" or price.get("requestedPricing") not in (None, "retail"):
                raise ValueError("Only public retail prices may be imported from runners")
            if type(price.get("totalMonthly")) not in (float, int) or not math.isfinite(price["totalMonthly"]) or price["totalMonthly"] < 0:
                raise ValueError("Invalid regional price")
        capacity = row.get("capacity")
        if not isinstance(capacity, dict) or capacity.get("physicalCapacity") != "unknown" or capacity.get("status") not in ("headroom", "offered", "unavailable", "unknown"):
            raise ValueError("Invalid regional capacity")
        if not isinstance(capacity.get("checks"), list) or not isinstance(capacity.get("quotas"), list):
            raise ValueError("Invalid regional capacity details")
        clean.append({key: row.get(key) for key in ("subscriptionId", "useCase", "location", "regionName", "inputKey", "checkedAt", "price", "priceDetail", "capacity")})
    return {"schemaVersion": 1, "source": "runner", "rows": clean, "generatedAt": max((row["checkedAt"] for row in clean), default=now), "errors": {}}


async def collect_regional_snapshot(items: list[dict], subscriptions: list[dict], previous: dict | None = None,
                                    on_batch: Callable[[dict], None] | None = None, source: str = "container") -> dict:
    from azure.core.exceptions import AzureError

    old_rows = {(row["subscriptionId"], row["useCase"], row["location"]): row for row in (previous or {}).get("rows", [])}
    snapshot = {"schemaVersion": 1, "source": source, "generatedAt": time.time(), "rows": [], "errors": {}}
    jobs = []
    for subscription in subscriptions:
        identifier = subscription["subscriptionId"]
        try:
            await setup_cloud.verify_subscription(subscription)
            locations = await setup_cloud.subscription_locations(identifier)
            if not locations:
                raise ValueError("No physical regions returned")
        except (ValueError, httpx.HTTPError, AzureError, TimeoutError):
            snapshot["errors"][identifier] = "Subscription or region discovery unavailable; prior results may be stale."
            for item in items:
                key = regional_input_key(item, subscription)
                snapshot["rows"].extend(row for row in old_rows.values() if row["subscriptionId"] == identifier
                                        and row["useCase"] == item.get("path", item.get("name")) and row.get("inputKey") == key)
            continue
        for item in items:
            key = regional_input_key(item, subscription)
            for region in locations:
                use_case = item.get("path", item.get("name"))
                row = old_rows.get((identifier, use_case, region["name"]))
                if row and row.get("inputKey") == key and time.time() - row.get("checkedAt", 0) < REGIONAL_INTERVAL:
                    snapshot["rows"].append(row)
                else:
                    position = len(snapshot["rows"])
                    snapshot["rows"].append(row if row and row.get("inputKey") == key else None)
                    jobs.append((position, item, subscription, region, key))

    def publish() -> None:
        snapshot["generatedAt"] = time.time()
        if on_batch:
            on_batch({**snapshot, "rows": [row for row in snapshot["rows"] if row is not None]})

    async def collect(job: tuple) -> None:
        position, item, subscription, region, key = job
        async with REGIONAL_CONCURRENCY:
            result = await check_region(item, subscription, region["name"], default_parameters(item))
        price = result.get("price")
        if price:
            result["price"] = {name: price.get(name) for name in ("complete", "currency", "totalMonthly", "pricingSource", "fallbackReason", "requestedPricing")}
        row = {**result, "useCase": item.get("path", item.get("name")), "regionName": region.get("displayName", region["name"]), "inputKey": key}
        old = snapshot["rows"][position]
        if old and ((not price or not price.get("complete")) or result["capacity"].get("status") == "unknown"):
            if (old.get("price") or {}).get("complete") and (old.get("capacity") or {}).get("status") != "unknown":
                row["lastSuccessful"] = {name: old.get(name) for name in ("price", "capacity", "checkedAt")}
            elif old.get("lastSuccessful"):
                row["lastSuccessful"] = old["lastSuccessful"]
        snapshot["rows"][position] = row

    publish()
    for offset in range(0, len(jobs), 3):
        await asyncio.gather(*(collect(job) for job in jobs[offset:offset + 3]))
        publish()
    snapshot["rows"] = [row for row in snapshot["rows"] if row is not None]
    return snapshot


async def refresh_regional_schedule() -> None:
    import setup_github
    from api import _catalog_items

    items = list(_catalog_items().values())
    subscriptions = setup_config.subscriptions()
    if not items or not subscriptions:
        db.set_setup_setting("regional_job", {"status": "waiting", "detail": "Waiting for published workloads and saved subscriptions."})
        return
    configuration = settings()
    if configuration["mode"] != "container" and runner_available(configuration, await setup_github.processing_probe()):
        db.set_setup_setting("regional_job", {"status": "runner", "detail": "Scheduled runner results are imported from the reviewed branch; deployment selections are checked live."})
        return
    previous = db.get_setup_setting("regional_snapshot", {})
    scope_key = hashlib.sha256(json.dumps([regional_input_key(item, subscription) for item in items for subscription in subscriptions]).encode()).hexdigest()
    job = db.get_setup_setting("regional_job", {})
    if job.get("scopeKey") == scope_key and job.get("nextRunAt", 0) > time.time():
        return
    started = time.time()
    db.set_setup_setting("regional_job", {"status": "running", "startedAt": started, "scopeKey": scope_key,
                         "detail": "Automatically refreshing regional prices and capacity for saved subscriptions."})

    def save(snapshot: dict) -> None:
        db.set_setup_setting("regional_snapshot", snapshot)
        db.set_setup_setting("regional_job", {"status": "running", "startedAt": started, "scopeKey": scope_key,
                             "completed": len(snapshot["rows"]), "detail": "Regional refresh in progress; completed results are available."})

    snapshot = await collect_regional_snapshot(items, subscriptions, previous, save)
    now = time.time()
    next_run = min((row["checkedAt"] + REGIONAL_INTERVAL for row in snapshot["rows"]), default=now + REGIONAL_INTERVAL)
    db.set_setup_setting("regional_job", {"status": "partial" if snapshot["errors"] else "succeeded", "completedAt": now,
                         "nextRunAt": now + 300 if snapshot["errors"] else max(now + 300, next_run), "scopeKey": scope_key,
                         "completed": len(snapshot["rows"]), "detail": "Scheduled refresh finished. Unknown or failed checks are shown explicitly."})


async def run_regional_schedule(stop: asyncio.Event) -> None:
    from azure.core.exceptions import AzureError

    while not stop.is_set():
        try:
            await asyncio.wait_for(refresh_regional_schedule(), timeout=1200)
        except asyncio.CancelledError:
            db.set_setup_setting("regional_job", {"status": "interrupted", "detail": "Refresh interrupted; completed rows will be reused after restart."})
            raise
        except (ValueError, KeyError, TypeError, httpx.HTTPError, AzureError, OSError, TimeoutError):
            db.set_setup_setting("regional_job", {"status": "failed", "detail": "Scheduled refresh incomplete; saved results retained. Check access and workload metadata. Retrying automatically."})
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except TimeoutError:
            pass


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
    configuration = ProcessingSettings.model_validate(db.get_setup_setting("catalog_processing", {})).model_dump()
    configuration["capacityRegions"] = []
    return configuration


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


class CatalogValidationError(ValueError):
    def __init__(self, tool: str, source: str, codes: list[str]) -> None:
        tool = tool if tool in ("bicep", "terraform") else "source"
        source = source if re.fullmatch(r"(?:catalog|modules)/(?:[A-Za-z0-9_-]+/){0,8}[A-Za-z0-9_.-]+", source) else "workload input"
        codes = sorted({code for code in codes if isinstance(code, str) and re.fullmatch(r"BCP\d{3}", code)})[:10]
        detail = f"{tool} validation failed for {source}"
        if codes:
            detail += " (" + ", ".join(codes) + ")"
        detail += ". "
        if "BCP091" in codes:
            detail += "A referenced file could not be read. Include required local modules in the configured repository branch at the paths referenced by the template."
        else:
            detail += "Check source syntax, local dependencies and container sandbox prerequisites."
        self.safe_detail = detail
        super().__init__(detail)


def _bicep_error_codes(content: bytes) -> list[str]:
    if len(content) > 1024 * 1024:
        return []
    try:
        document = json.loads(content)
    except (ValueError, UnicodeError):
        return []
    if not isinstance(document, dict) or not isinstance(document.get("runs"), list):
        return []
    codes = []
    for run in document["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("results"), list):
            continue
        codes.extend(result["ruleId"] for result in run["results"] if isinstance(result, dict)
                     and result.get("level") == "error" and isinstance(result.get("ruleId"), str))
    return codes


async def compile_source(work: Path, tool: str, arguments: list[str]) -> str:
    if tool not in ("bicep", "terraform"):
        raise ValueError("Unsupported compiler")
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work),
                   "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(work / ".dotnet"), "DOTNET_EnableDiagnostics": "0",
                   "DOTNET_GCHeapHardLimit": "0x10000000", "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1",
                   "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1", "GOMEMLIMIT": "256MiB"}
    diagnostics_arguments = ["--diagnostics-format", "sarif"] if tool == "bicep" and arguments[0] == "build" else []
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), "--sandbox", str(work), tool, *arguments, *diagnostics_arguments,
            cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=diagnostics,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=60)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            diagnostics.seek(0)
            codes = _bicep_error_codes(diagnostics.read(1024 * 1024 + 1)) if diagnostics_arguments else []
            source = arguments[1] if tool == "bicep" else arguments[-1]
            raise CatalogValidationError(tool, source, codes)
        output.seek(0)
        content = output.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("Compiler output exceeds the validation limit")
        return content.decode("utf-8")


async def process_blobs(blobs: dict[str, bytes], *, refresh_prices: bool = True) -> dict[str, bytes]:
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
            if refresh_prices and cost and cost.get("components"):
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


async def check_region(item: dict, subscription: dict, region: str, parameters: dict[str, str], *, fresh: bool = False) -> dict:
    arm_token = setup_cloud.FRESH_READ.set(fresh)
    price_token = pricing.FRESH_READ.set(fresh)
    try:
        defaults = default_parameters(item)
        result = await _check_region(item, subscription, region, {**defaults, **parameters})
        if result.get("price") is not None:
            missing = pricing.unpriced_parameters(item.get("cost") or {}, parameters, defaults)
            result["price"]["unpricedParameters"] = missing
            if missing:
                warning = "Not adjusted for: " + ", ".join(missing) + ". The workload cost model has no mapping for these options."
                result["priceDetail"] = " ".join(part for part in (result.get("priceDetail"), warning) if part)
        return result
    finally:
        setup_cloud.FRESH_READ.reset(arm_token)
        pricing.FRESH_READ.reset(price_token)


async def _check_region(item: dict, subscription: dict, region: str, parameters: dict[str, str]) -> dict:
    from azure.core.exceptions import AzureError

    result = {"subscriptionId": subscription["subscriptionId"], "location": region,
              "price": None, "priceDetail": "No component cost model is defined for this workload."}
    cost = item.get("cost") or {}
    if cost.get("components"):
        try:
            fresh = {**cost, "components": [{key: value for key, value in component.items() if key not in ("unitPrice", "monthly")} for component in cost["components"]]}
            result["price"] = await asyncio.wait_for(pricing.estimate_for_subscription(fresh, region, parameters, subscription), timeout=60)
            result["priceDetail"] = result["price"].get("fallbackReason") or ("" if result["price"].get("complete") else "Some regional meters could not be priced.")
        except (ValueError, RuntimeError, httpx.HTTPError, AzureError, TimeoutError):
            result["priceDetail"] = "Regional pricing unavailable; retry or check the workload cost model."
    try:
        result["capacity"] = await asyncio.wait_for(setup_cloud.workload_capacity(subscription["subscriptionId"], region, item, parameters), timeout=60)
    except (ValueError, httpx.HTTPError, AzureError, TimeoutError):
        result["capacity"] = {"status": "unknown", "checks": [], "quotas": [], "physicalCapacity": "unknown", "detail": "Capacity checks unavailable; verify access and retry."}
    result["checkedAt"] = time.time()
    return result


async def refresh_capacity(regions: list[str]) -> dict | None:
    subscriptions = setup_config.subscriptions()
    if not subscriptions:
        return None
    snapshots = {}
    for subscription in subscriptions:
        identifier = subscription["subscriptionId"]
        await setup_cloud.verify_subscription(subscription)
        selected_regions = regions or [item["name"] for item in await setup_cloud.subscription_locations(identifier)]
        snapshots[identifier] = {}
        async def read_region(region: str) -> tuple[str, list]:
            if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region):
                raise ValueError("Use canonical Azure region names")
            try:
                result = await setup_cloud.arm_request(f"/subscriptions/{identifier}/providers/Microsoft.Compute/locations/{region}/usages?api-version=2024-03-01")
            except httpx.HTTPError:
                return region, []
            if result.get("nextLink") or not isinstance(result.get("value"), list):
                raise ValueError("Incomplete quota response; previous capacity remains active")
            rows = []
            for entry in result["value"]:
                limit, current = entry.get("limit"), entry.get("currentValue")
                if any(type(value) is not int or value < 0 for value in (limit, current)):
                    raise ValueError("Invalid quota counters")
                rows.append({"name": entry["name"].get("localizedValue") or entry["name"]["value"], "limit": limit,
                             "current": current, "available": max(0, limit - current)})
            return region, rows
        for offset in range(0, len(selected_regions), 4):
            snapshots[identifier].update(await asyncio.gather(*(read_region(region) for region in selected_regions[offset:offset + 4])))
    return {"schemaVersion": 1, "generated": datetime.now(timezone.utc).isoformat(),
            "tenantId": os.environ.get("GALLERY_ENTRA_TENANT_ID", ""), "physicalCapacity": "unknown", "subscriptions": snapshots}


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] != "--sandbox" or sys.argv[3] not in ("bicep", "terraform"):
        raise SystemExit(2)
    compiler_sandbox(Path(sys.argv[2]).resolve())
    os.execv(f"/usr/local/bin/{sys.argv[3]}", [sys.argv[3], *sys.argv[4:]])