"""Build reviewed catalog inputs and collect read-only organization quota snapshots."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

OUTPUT_PATTERN = r"(?:site/capacity\.json|catalog/[a-z0-9][a-z0-9-]{0,79}/(?:pricing\.json|compiled/(?:dev|prod)\.json))"
MAX_FILE = 2 * 1024 * 1024


def command(arguments: list[str], root: Path) -> str:
    executable = shutil.which(arguments[0])
    if not executable:
        raise ValueError(f"Required tool is missing: {arguments[0]}")
    with tempfile.TemporaryFile() as output:
        result = subprocess.run([executable, *arguments[1:]], cwd=root, stdout=output,
                                stderr=subprocess.DEVNULL, timeout=180, check=False)
        if result.returncode:
            raise ValueError(f"{arguments[0]} {arguments[1]} failed; check tool configuration, access and branch protection")
        output.seek(0)
        data = output.read(16 * MAX_FILE + 1)
    if len(data) > 16 * MAX_FILE:
        raise ValueError("Command output exceeds the build limit")
    return data.decode("utf-8-sig").strip()


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    path.resolve().relative_to(root.resolve())
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Symbolic links are not allowed")
    return path


def read_json(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) > MAX_FILE:
        raise ValueError("Input exceeds 2 MiB")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_bundle(root: Path, files: dict[str, dict | None]) -> None:
    commit = command(["git", "rev-parse", "HEAD"], root)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source commit")
    payload = {}
    for name, value in files.items():
        if not re.fullmatch(OUTPUT_PATTERN, name):
            raise ValueError("Invalid generated path")
        data = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode() if value is not None else None
        if data is not None and len(data) > MAX_FILE:
            raise ValueError("Generated file exceeds 2 MiB")
        payload[name] = base64.b64encode(data).decode() if data is not None else None
    output = safe_path(root, "runs/portal-generated/manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"sourceCommit": commit, "files": payload}).encode()
    if len(content) > 24 * 1024 * 1024 or len(payload) > 500:
        raise ValueError("Generated catalog exceeds synchronization limits")
    output.write_bytes(content)


async def build(root: Path) -> None:
    sys.path.insert(0, str(root / "site/server"))
    import pricing

    files = {}
    for metadata_path in sorted((root / "catalog").glob("*/metadata.json")):
        slug = metadata_path.parent.name
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
            raise ValueError("Invalid workload directory")
        relative = f"catalog/{slug}"
        safe_path(root, f"{relative}/metadata.json")
        metadata = read_json(metadata_path)
        if not isinstance(metadata.get("displayName"), str):
            raise ValueError("Each workload needs a displayName")
        source = safe_path(root, f"{relative}/main.bicep")
        if source.is_file():
            template = json.loads(command(["az", "bicep", "build", "--file", str(source), "--stdout"], root))
            for environment in ("dev", "prod"):
                parameters = safe_path(root, f"{relative}/parameters/{environment}.bicepparam")
                destination = f"{relative}/compiled/{environment}.json"
                if parameters.is_file():
                    result = json.loads(command(["az", "bicep", "build-params", "--file", str(parameters), "--stdout"], root))
                    compiled = json.loads(result["templateJson"])
                    values = json.loads(result["parametersJson"])["parameters"]
                    if compiled != template:
                        raise ValueError("Parameter file must reference this workload's main.bicep")
                elif environment == "dev":
                    compiled, values = template, {}
                else:
                    files[destination] = None
                    continue
                for name, value in values.items():
                    declaration = compiled.get("parameters", {}).get(name, {})
                    if str(declaration.get("type", "")).lower() in ("securestring", "secureobject") and "value" in value:
                        raise ValueError("Do not publish literal secure parameter values; use protected references")
                files[destination] = {"template": compiled, "parameters": values}
        cost = metadata.get("cost")
        terraform = safe_path(root, f"{relative}/terraform")
        if (terraform / "main.tf").is_file():
            command(["terraform", "fmt", "-write=false", str(terraform)], root)
        price_path = f"{relative}/pricing.json"
        files[price_path] = None
        if cost and cost.get("components"):
            fresh = {**cost, "components": [{key: value for key, value in row.items() if key not in ("unitPrice", "monthly")} for row in cost["components"]]}
            priced = await asyncio.wait_for(pricing.estimate(fresh, cost.get("region", "eastus"), {}), timeout=120)
            if not priced.get("complete"):
                raise ValueError("Retail prices are incomplete; previous generated prices remain active")
            priced["components"] = [{**authored, **row} for authored, row in zip(cost["components"], priced["components"], strict=True)]
            files[price_path] = {"metadataSha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                                 "cost": {**cost, **priced, "pricedAt": stamp(), "pricingSource": "retail"}}
    write_bundle(root, files)


def capacity_inputs() -> tuple[str, list[str], list[str]]:
    tenant = str(UUID(os.environ.get("AZURE_TENANT_ID", "")))
    client = UUID(os.environ.get("AZURE_CLIENT_ID", ""))
    subscriptions = list(dict.fromkeys(str(UUID(value)) for value in os.environ.get("PORTAL_SUBSCRIPTION_IDS", "").split()))
    regions = list(dict.fromkeys(os.environ.get("PORTAL_CAPACITY_REGIONS", "").split()))
    if not client.int or not UUID(tenant).int or not subscriptions or any(not UUID(value).int for value in subscriptions):
        raise ValueError("Set nonzero organization tenant, client and subscription IDs")
    if not regions or len(subscriptions) > 20 or len(regions) > 20 or len(subscriptions) * len(regions) > 100:
        raise ValueError("Choose 1-20 subscriptions and regions, with at most 100 combinations")
    if any(not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region) for region in regions):
        raise ValueError("Use canonical Azure region names")
    return tenant, subscriptions, regions


def capacity(root: Path) -> None:
    tenant, subscriptions, regions = capacity_inputs()
    snapshots = {}
    for subscription in subscriptions:
        prefix = f"https://management.azure.com/subscriptions/{subscription}"
        details = json.loads(command(["az", "rest", "--method", "get", "--url", prefix + "?api-version=2022-12-01"], root))
        if str(details.get("tenantId", "")).lower() != tenant or details.get("state") != "Enabled" or str(details.get("subscriptionId", "")).lower() != subscription:
            raise ValueError("Capacity subscription is not enabled in the configured tenant")
        snapshots[subscription] = {}
        for region in regions:
            result = json.loads(command(["az", "rest", "--method", "get", "--url", prefix + f"/providers/Microsoft.Compute/locations/{region}/usages?api-version=2024-03-01"], root))
            if result.get("nextLink") or not isinstance(result.get("value"), list):
                raise ValueError("Incomplete quota response; retain the previous snapshot")
            rows = []
            for entry in result["value"]:
                limit, current = entry.get("limit"), entry.get("currentValue")
                if any(type(value) is not int or value < 0 for value in (limit, current)):
                    raise ValueError("Invalid quota counters")
                rows.append({"name": entry["name"].get("localizedValue") or entry["name"]["value"],
                             "limit": limit, "current": current, "available": max(0, limit - current)})
            snapshots[subscription][region] = rows
    write_bundle(root, {"site/capacity.json": {"schemaVersion": 1, "generated": stamp(), "tenantId": tenant,
                                             "subscriptions": snapshots, "physicalCapacity": "unknown"}})


def publish(root: Path) -> None:
    manifest_path = safe_path(root, "runs/portal-generated/manifest.json")
    if manifest_path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("Generated manifest exceeds the limit")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("sourceCommit") != command(["git", "rev-parse", "HEAD"], root):
        raise ValueError("Branch changed since build; rerun the workflow. No files were published.")
    if command(["git", "status", "--porcelain", "--untracked-files=no"], root):
        raise ValueError("Publish requires a clean checkout")
    files = manifest["files"]
    if not isinstance(files, dict) or len(files) > 500:
        raise ValueError("Invalid output manifest")
    decoded = {}
    total = 0
    for name, encoded in files.items():
        if not re.fullmatch(OUTPUT_PATTERN, name):
            raise ValueError("Output path is not allowlisted")
        path = safe_path(root, name)
        data = base64.b64decode(encoded, validate=True) if encoded is not None else None
        if data is not None:
            total += len(data)
            if len(data) > MAX_FILE or total > 16 * 1024 * 1024 or not isinstance(json.loads(data), dict):
                raise ValueError("Invalid generated output")
        decoded[path] = data
    changed = []
    for path, data in decoded.items():
        if data is None:
            if path.exists():
                path.unlink()
                changed.append(path.relative_to(root).as_posix())
        elif not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed.append(path.relative_to(root).as_posix())
    if changed:
        command(["git", "add", "--", *changed], root)
        command(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-m", "Refresh portal generated inputs"], root)
        command(["git", "push", "origin", "HEAD"], root)
    print("Generated inputs published." if changed else "Generated inputs unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "capacity", "capacity-check", "publish"))
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.action == "build":
            asyncio.run(build(root))
        elif args.action == "capacity":
            capacity(root)
        elif args.action == "capacity-check":
            capacity_inputs()
        else:
            publish(root)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, TimeoutError):
        parser.exit(1, "Portal automation failed; check reviewed inputs, configuration, tool availability and access. No cloud resources were changed.\n")