"""Customer repository provisioning and bounded, commit-pinned catalog sync."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, ConfigDict, field_validator

import db
import github_device

_SYNC_LOCK = asyncio.Lock()


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    branch: str = Field(default="main", pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,99}$")
    create: bool = False
    seed: bool = False
    confirm: bool = False

    @field_validator("branch")
    @classmethod
    def valid_branch(cls, value: str) -> str:
        if ".." in value or "//" in value or value.endswith(("/", ".", ".lock")):
            raise ValueError("Invalid repository branch")
        return value


async def github_request(path: str, method: str = "GET", body: dict | None = None, *, token: str | None = None, params: dict | None = None) -> dict | list:
    token = token if token is not None else github_device.access_token()
    if not token or token.startswith("@Microsoft.KeyVault("):
        raise ValueError("Connect GitHub before configuring the repository; the connection may have expired")
    if not path.startswith("/") or ".." in path or "?" in path:
        raise ValueError("Invalid GitHub API path")
    async with httpx.AsyncClient(base_url="https://api.github.com", timeout=30, follow_redirects=False) as client:
        async with client.stream(method, path, json=body, params=params, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("GitHub response exceeds the synchronization limit")
    result = json.loads(content) if content else {}
    if not isinstance(result, (dict, list)):
        raise ValueError("Invalid GitHub response")
    return result


async def github_account(token: str | None = None) -> dict:
    account = await github_request("/user", token=token)
    if not isinstance(account, dict) or not isinstance(account.get("id"), int) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", str(account.get("login", ""))):
        raise ValueError("Could not verify the GitHub account")
    return {"id": account["id"], "login": account["login"]}


async def discover_repositories() -> dict:
    account = await github_account()
    owners = {account["login"]: {"login": account["login"], "type": "User"}}
    repositories = []
    truncated = False
    for page in range(1, 6):
        rows = await github_request("/user/orgs", params={"per_page": 100, "page": page})
        if not isinstance(rows, list): raise ValueError("Invalid GitHub organizations response")
        for item in rows:
            login = item.get("login", "")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
                owners[login] = {"login": login, "type": "Organization"}
        if len(rows) < 100: break
        if page == 5: truncated = True
    for page in range(1, 6):
        rows = await github_request("/user/repos", params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
        if not isinstance(rows, list): raise ValueError("Invalid GitHub repositories response")
        for item in rows:
            if item.get("archived") or not item.get("permissions", {}).get("push"):
                continue
            repositories.append({"fullName": item["full_name"], "owner": item["owner"]["login"], "name": item["name"], "defaultBranch": item.get("default_branch") or "main", "private": bool(item.get("private"))})
        if len(rows) < 100: break
        if page == 5: truncated = True
    return {"account": account, "owners": list(owners.values()), "repositories": repositories, "truncated": truncated}


def source_seed() -> dict[str, str]:
    path = Path(__file__).resolve().parent / "platform-source.json"
    if not path.is_file():
        raise ValueError("Repository seeding requires the customer export, not the development checkout")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    files = bundle["files"]
    if len(files) > 100:
        raise ValueError("Invalid customer source bundle")
    for name, content in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or name.startswith(("catalog/", "modules/", ".git/")):
            raise ValueError("Invalid source bundle path")
        raw = base64.b64decode(content, validate=True)
        if hashlib.sha256(raw).hexdigest() != bundle["sha256"].get(name):
            raise ValueError("Customer source bundle integrity check failed")
    return files


async def configure(settings: GitHubSettings) -> dict:
    if not settings.confirm:
        raise ValueError("Explicit confirmation is required for repository configuration")
    async with _SYNC_LOCK:
        if not settings.enabled:
            db.set_setup_setting("github", {"enabled": False})
            return {"enabled": False}
        if settings.seed and not settings.create:
            raise ValueError("Source seeding is limited to newly created private repositories")
        files = source_seed() if settings.seed else {}
        repo = f"{settings.owner}/{settings.repository}"
        if settings.create:
            account = await github_request("/user")
            endpoint = "/user/repos" if str(account.get("login", "")).lower() == settings.owner.lower() else f"/orgs/{settings.owner}/repos"
            remote = await github_request(endpoint, "POST", {"name": settings.repository, "private": True, "auto_init": False})
        else:
            remote = await github_request(f"/repos/{repo}")
        if str(remote.get("full_name", "")).lower() != repo.lower():
            raise ValueError("Repository owner/name verification failed")
        saved = {"enabled": True, "owner": settings.owner, "repository": settings.repository, "branch": settings.branch, "seeded": False}
        db.set_setup_setting("github", saved)
        if files:
            try:
                initial = await github_request(f"/repos/{repo}/contents/LICENSE", "PUT", {
                    "message": "Initialize customer platform license",
                    "content": files["LICENSE"], "branch": settings.branch,
                })
                parent = initial["commit"]["sha"]
                tree_entries = []
                for name, content in files.items():
                    blob = await github_request(f"/repos/{repo}/git/blobs", "POST", {"encoding": "base64", "content": content})
                    tree_entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
                tree = await github_request(f"/repos/{repo}/git/trees", "POST", {"tree": tree_entries})
                commit = await github_request(f"/repos/{repo}/git/commits", "POST", {"message": "Initialize customer deployment platform", "tree": tree["sha"], "parents": [parent]})
                await github_request(f"/repos/{repo}/git/refs/heads/{quote(settings.branch, safe='/')}", "PATCH", {"sha": commit["sha"], "force": False})
                saved["seeded"] = True
                db.set_setup_setting("github", saved)
            except (httpx.HTTPError, ValueError, KeyError):
                raise ValueError("Repository was created, but seeding did not finish. Inspect the private repository before retrying; it was not deleted.") from None
        return saved


def catalog_snapshot() -> dict:
    return db.get_setup_setting("catalog_snapshot", {})


def snapshot_root(version: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", version):
        raise ValueError("Invalid catalog version")
    return db._default_db_path().parent / "catalog-versions" / version


async def sync_catalog() -> dict:
    async with _SYNC_LOCK:
        settings = db.get_setup_setting("github", {})
        if not settings.get("enabled"):
            raise ValueError("GitHub is disabled")
        repo = f"{settings['owner']}/{settings['repository']}"
        ref = await github_request(f"/repos/{repo}/git/ref/heads/{quote(settings['branch'], safe='/')}")
        version = str(ref["object"]["sha"])
        root = snapshot_root(version)
        current = catalog_snapshot()
        if current.get("version") == version and current.get("repository") == repo:
            return current
        commit = await github_request(f"/repos/{repo}/git/commits/{version}")
        tree = await github_request(f"/repos/{repo}/git/trees/{commit['tree']['sha']}")
        catalog_entry = next((entry for entry in tree.get("tree", []) if entry.get("path") == "catalog" and entry.get("type") == "tree"), None)
        candidates: list[dict] = []
        if catalog_entry:
            await _walk_tree(repo, catalog_entry["sha"], "catalog", candidates, budget=[0])
        blobs: dict[str, bytes] = {}
        total = 0
        for entry in candidates:
            name = entry["path"]
            if not re.fullmatch(r"catalog/[a-z0-9][a-z0-9-]{0,79}/(?:metadata\.json|compiled/(?:dev|prod)\.json|terraform/[A-Za-z0-9_.-]+\.(?:tf|tfvars|json))", name):
                continue
            if entry.get("mode") != "100644" or entry.get("size", 0) > 2 * 1024 * 1024:
                raise ValueError("Unsupported catalog file mode or size")
            blob = await github_request(f"/repos/{repo}/git/blobs/{entry['sha']}")
            content = base64.b64decode(blob["content"], validate=False)
            total += len(content)
            if total > 16 * 1024 * 1024:
                raise ValueError("Catalog exceeds 16 MiB")
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("Catalog file exceeds 2 MiB")
            blobs[name] = content
        items = []
        for name, content in blobs.items():
            if not name.endswith("/metadata.json"):
                continue
            slug = name.split("/")[1]
            item = json.loads(content)
            if not isinstance(item, dict) or not isinstance(item.get("displayName"), str):
                raise ValueError("Each use case requires metadata.json with a displayName")
            engines = []
            for environment in ("dev", "prod"):
                source = f"catalog/{slug}/compiled/{environment}.json"
                if source in blobs:
                    compiled = json.loads(blobs[source])
                    if not isinstance(compiled.get("template"), dict) or not isinstance(compiled.get("parameters", {}), dict):
                        raise ValueError("Invalid customer compiled ARM input")
                    target = root / "_templates" / slug / f"{environment}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blobs[source])
                    if environment == "dev":
                        engines.append("bicep")
            for source, data in blobs.items():
                prefix = f"catalog/{slug}/terraform/"
                if source.startswith(prefix):
                    target = root / "_tf" / slug / source.removeprefix(prefix)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    if target.name == "main.tf":
                        engines.append("terraform")
            item.update({"name": slug, "path": slug, "engines": engines, "deployReady": bool(engines), "hasProd": f"catalog/{slug}/compiled/prod.json" in blobs, "catalogVersion": version})
            items.append(item)
        result = {"version": version, "items": items, "syncedAt": time.time(), "repository": repo}
        db.set_setup_setting("catalog_snapshot", result)
        db.set_setup_setting("github_sync_error", None)
        return result


async def _walk_tree(repo: str, sha: str, prefix: str, entries: list[dict], depth: int = 0, budget: list[int] | None = None) -> None:
    if depth > 2:
        return
    budget = budget if budget is not None else [0]
    budget[0] += 1
    if budget[0] > 150:
        raise ValueError("Catalog directory count exceeds the synchronization limit")
    tree = await github_request(f"/repos/{repo}/git/trees/{sha}")
    if tree.get("truncated"):
        raise ValueError("Catalog tree is truncated")
    for entry in tree.get("tree", []):
        path = f"{prefix}/{entry['path']}"
        if entry.get("type") == "tree":
            if depth == 0 and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", entry["path"]):
                continue
            if depth == 1 and entry["path"] not in ("compiled", "terraform"):
                continue
            await _walk_tree(repo, entry["sha"], path, entries, depth + 1, budget)
        elif entry.get("type") == "blob":
            entries.append({**entry, "path": path})
        if len(entries) > 500:
            raise ValueError("Catalog exceeds 500 files")


async def run_catalog_sync(stop: asyncio.Event) -> None:
    while not stop.is_set():
        if db.get_setup_setting("github", {}).get("enabled"):
            try:
                await asyncio.wait_for(sync_catalog(), timeout=120)
            except (ValueError, KeyError, httpx.HTTPError, OSError, TimeoutError):
                db.set_setup_setting("github_sync_error", "Sync failed; the last good catalog is still active. Check repository access and runtime inputs.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except TimeoutError:
            pass