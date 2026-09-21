"""Organization repository provisioning and bounded, commit-pinned catalog sync."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx
from azure.core.exceptions import AzureError
from pydantic import BaseModel, Field, ConfigDict, field_validator

import db
import lifecycle
import github_device
import setup_config
import catalog_processing
import catalog_workflows

_SYNC_LOCK = asyncio.Lock()
_SYNC_WAKE = asyncio.Event()
_ACCOUNT_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}"


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    owner: str = Field(pattern=rf"^{_ACCOUNT_PATTERN}$")
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


class GitHubConnectionError(ValueError):
    def __init__(self) -> None:
        super().__init__("GitHub authorization is unavailable or expired. Reconnect GitHub in Setup > GitHub, keep the existing repository, then refresh the portal.")


async def github_request(path: str, method: str = "GET", body: dict | None = None, *, token: str | None = None, params: dict | None = None, require_workflow: bool = False, response_limit: int = 8 * 1024 * 1024) -> dict | list:
    token = token if token is not None else await github_device.ensure_access_token()
    if not token or token.startswith("@Microsoft.KeyVault("):
        raise GitHubConnectionError()
    if not path.startswith("/") or ".." in path or "?" in path:
        raise ValueError("Invalid GitHub API path")
    async with httpx.AsyncClient(base_url="https://api.github.com", timeout=30, follow_redirects=False) as client:
        async with client.stream(method, path, json=body, params=params, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }) as response:
            if response.status_code == 401:
                raise GitHubConnectionError()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > response_limit:
                    raise ValueError("GitHub response exceeds the synchronization limit")
            if response.is_error:
                httpx.Response(response.status_code, request=response.request, headers=response.headers, content=bytes(content)).raise_for_status()
            response.raise_for_status()
            if require_workflow:
                scopes = set(response.headers.get("x-oauth-scopes", "").replace(",", " ").split())
                if "workflow" not in scopes:
                    raise ValueError("GitHub workflow permission is missing or could not be verified. Disable the current repository connection and save, reconnect GitHub with workflow permission, then retry. No repository or workflow files were changed by this check.")
    result = json.loads(content) if content else {}
    if not isinstance(result, (dict, list)):
        raise ValueError("Invalid GitHub response")
    return result


async def github_account(token: str | None = None) -> dict:
    account = await github_request("/user", token=token)
    if not isinstance(account, dict) or not isinstance(account.get("id"), int) or not re.fullmatch(_ACCOUNT_PATTERN, str(account.get("login", ""))):
        raise ValueError("Could not verify the GitHub account")
    return {"id": account["id"], "login": account["login"]}


async def verify_reconnection(account: dict, token: str) -> bool:
    configuration = db.get_setup_setting("github", {})
    if not configuration.get("enabled"):
        return False
    previous = db.get_setup_setting("github_connection", {})
    if type(previous.get("id")) is not int or previous["id"] != account.get("id"):
        raise ValueError("Sign in with the previously connected GitHub account to keep this repository enabled. To change accounts, disable the repository connection first. No credentials were replaced.")
    selected = GitHubSettings(owner=configuration["owner"], repository=configuration["repository"], branch=configuration["branch"])
    repo = f"{selected.owner}/{selected.repository}"
    try:
        remote = await github_request(f"/repos/{repo}", token=token)
        permissions = remote.get("permissions") if isinstance(remote, dict) else None
        if (not isinstance(remote, dict) or str(remote.get("full_name", "")).lower() != repo.lower()
            or remote.get("archived") or remote.get("disabled") or not isinstance(permissions, dict) or permissions.get("push") is not True):
            raise ValueError("The new GitHub sign-in must retain write access to the saved repository. Check organization access and SSO. No credentials were replaced.")
        ref = await github_request(f"/repos/{repo}/git/ref/heads/{quote(selected.branch, safe='/')}", token=token)
        target = ref.get("object") if isinstance(ref, dict) else None
        if (not isinstance(ref, dict) or ref.get("ref") != f"refs/heads/{selected.branch}"
            or not isinstance(target, dict) or not re.fullmatch(r"[a-f0-9]{40}", str(target.get("sha", "")))):
            raise ValueError("The saved repository branch could not be verified. No credentials were replaced.")
    except httpx.HTTPStatusError as error:
        raise ValueError(f"The new GitHub sign-in could not access the saved repository or branch (HTTP {error.response.status_code}). Check organization access and SSO. No credentials were replaced.") from None
    return True


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
            if re.fullmatch(_ACCOUNT_PATTERN, login):
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
        raise ValueError("Repository seeding requires the organization export, not the development checkout")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    files = bundle["files"]
    if len(files) > 100:
        raise ValueError("Invalid organization source bundle")
    for name, content in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or name.startswith(("catalog/", "modules/", ".git/")):
            raise ValueError("Invalid source bundle path")
        raw = base64.b64decode(content, validate=True)
        if hashlib.sha256(raw).hexdigest() != bundle["sha256"].get(name):
            raise ValueError("Organization source bundle integrity check failed")
    return files


def repository_failure(error: httpx.HTTPError, creating: bool) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        status = response.status_code
        document = {}
        try:
            if len(response.content) <= 16384:
                document = response.json()
        except (ValueError, httpx.ResponseNotRead):
            pass
        errors = document.get("errors", []) if isinstance(document, dict) else []
        duplicate = isinstance(errors, list) and any(
            isinstance(item, dict) and item.get("resource") == "Repository" and
            item.get("field") == "name" and (item.get("code") == "already_exists" or
            "name already exists" in str(item.get("message", "")).lower())
            for item in errors
        )
        if creating and status == 422 and duplicate:
            return "That repository name already exists in the selected GitHub account. Choose Connect an existing repository to connect it, or Copy portal source to an empty or LICENSE-only repository to finish an incomplete copy. Other existing content requires a reviewed source import. No existing repository was overwritten or deleted."
        if status == 401:
            reason = "GitHub authorization expired or was rejected. Reconnect GitHub."
        elif status == 429 or response.headers.get("x-ratelimit-remaining") == "0":
            reason = "GitHub rate-limited the request. Wait for the rate limit to reset."
        elif status == 403:
            reason = "GitHub denied access. Check OAuth approval/SSO, repository permissions and organization policy."
        elif status == 404:
            reason = "The repository or account was not found or is not accessible to this GitHub connection."
        elif status == 422:
            reason = "GitHub rejected the repository settings. Check the name, ownership, existing repositories and organization rules."
        else:
            reason = "GitHub could not complete the request. Check service availability and repository access."
        return f"GitHub repository configuration failed (HTTP {status}). {reason} Inspect the target repository before retrying; do not create another repository to recover."
    return "The GitHub connection timed out or was interrupted. Repository creation may have succeeded remotely. Reload Setup and inspect the selected account before retrying; do not create another repository to recover."


def seed_failure(error: Exception, step: str) -> str:
    prefix = f"Repository source copy did not finish while {step}."
    guidance = "Inspect the private repository before retrying; it was not deleted."
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        status = response.status_code
        message = ""
        try:
            response_content = response.content
        except httpx.ResponseNotRead:
            response_content = b""
        if response_content and len(response_content) <= 16384:
            try:
                document = response.json()
                if isinstance(document, dict) and isinstance(document.get("message"), str):
                    message = document["message"].lower()
            except ValueError:
                pass
        if "workflow" in message and ("scope" in message or "permission" in message):
            reason = "GitHub requires workflow permission to install the included Actions files. Reauthorize the organization OAuth connection with workflow scope."
        elif status == 429 or response.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in message:
            reason = "GitHub rate-limited seeding. Wait for the rate limit to reset before retrying."
        elif "protected branch" in message or "repository rule" in message or "rule violations" in message:
            reason = "GitHub branch protection or repository rules blocked the seed commit. Use your organization's reviewed import process; do not disable protection."
        elif status == 401:
            reason = "GitHub authorization expired or was rejected. Reconnect the organization GitHub account."
        elif status == 403:
            reason = "GitHub denied the write. Check OAuth approval/SSO, repository write/workflow access and organization rules."
        elif status in (409, 422):
            reason = "GitHub rejected the repository state or commit update. Check whether initialization partially completed or the branch changed."
        else:
            reason = "GitHub could not complete the request. Check service availability and repository access."
        return f"{prefix} HTTP {status}. {reason} {guidance}"
    if isinstance(error, httpx.TimeoutException):
        return f"{prefix} The GitHub request timed out; it may have succeeded remotely. {guidance}"
    if isinstance(error, httpx.RequestError):
        return f"{prefix} GitHub could not be reached. {guidance}"
    return f"{prefix} The source bundle or GitHub response could not be validated. {guidance}"


async def source_copy_base(repo: str, branch: str, entries: list[dict]) -> tuple[str | None, bool]:
    branches = await github_request(f"/repos/{repo}/branches", params={"per_page": 1})
    if not isinstance(branches, list):
        raise ValueError("Could not verify repository branches; no source was copied")
    if not branches:
        return None, False
    ref = await github_request(f"/repos/{repo}/git/ref/heads/{quote(branch, safe='/')}")
    parent = ref["object"]["sha"]
    commit = await github_request(f"/repos/{repo}/git/commits/{parent}")
    tree = await github_request(f"/repos/{repo}/git/trees/{commit['tree']['sha']}", params={"recursive": "1"})
    if tree.get("truncated") or not isinstance(tree.get("tree"), list):
        raise ValueError("Repository tree could not be fully verified; no source was copied")
    expected = {}
    for entry in entries:
        content = entry["content"].encode("utf-8")
        expected[entry["path"]] = hashlib.sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest()
    actual = {}
    for entry in tree["tree"]:
        if entry.get("type") == "tree" and entry.get("mode") == "040000":
            continue
        if entry.get("type") != "blob" or entry.get("mode") != "100644" or entry.get("path") in actual:
            raise ValueError("Repository contains unsupported files; use a reviewed source import. No files were changed")
        actual[entry["path"]] = entry["sha"]
    if actual == expected:
        return parent, True
    if actual and actual != {"LICENSE": expected["LICENSE"]}:
        raise ValueError("Source copy requires an empty repository or only the matching portal LICENSE. Existing code was not overwritten; use a reviewed source import")
    return parent, False


async def configure(settings: GitHubSettings) -> dict:
    if not settings.confirm:
        raise ValueError("Explicit confirmation is required for repository configuration")
    async with _SYNC_LOCK:
        if not settings.enabled:
            db.set_setup_setting("github", {"enabled": False})
            return {"enabled": False}
        files = source_seed() if settings.seed else {}
        files = {name: content for name, content in files.items() if not name.startswith(".github/workflows/")}
        if settings.seed and (not files or "LICENSE" not in files):
            raise ValueError("A valid portal source bundle including LICENSE is required")
        tree_entries = [
            {"path": name, "mode": "100644", "type": "blob", "content": base64.b64decode(content, validate=True).decode("utf-8")}
            for name, content in files.items()
        ]
        repo = f"{settings.owner}/{settings.repository}"
        account = None
        if any(name.startswith(".github/workflows/") for name in files):
            account = await github_request("/user", require_workflow=True)
        if settings.create:
            if account is None:
                account = await github_request("/user")
            endpoint = "/user/repos" if str(account.get("login", "")).lower() == settings.owner.lower() else f"/orgs/{settings.owner}/repos"
            remote = await github_request(endpoint, "POST", {"name": settings.repository, "private": True, "auto_init": False})
        else:
            remote = await github_request(f"/repos/{repo}")
        if str(remote.get("full_name", "")).lower() != repo.lower():
            raise ValueError("Repository owner/name verification failed")
        parent, already_copied = None, False
        if files:
            if remote.get("private") is not True or remote.get("archived") or remote.get("disabled"):
                raise ValueError("Source copy requires an active private repository")
            if not settings.create:
                if remote.get("permissions", {}).get("push") is not True:
                    raise ValueError("Source copy requires repository write access")
                parent, already_copied = await source_copy_base(repo, settings.branch, tree_entries)
        previous = db.get_setup_setting("github", {})
        same_repository = all(previous.get(key) == getattr(settings, key) for key in ("owner", "repository", "branch"))
        saved = {"enabled": True, "owner": settings.owner, "repository": settings.repository, "branch": settings.branch,
                 "seeded": already_copied if files else bool(same_repository and previous.get("seeded"))}
        db.set_setup_setting("github", saved)
        if files and not already_copied:
            step = "initializing the license commit"
            try:
                if parent is None:
                    initial = await github_request(f"/repos/{repo}/contents/LICENSE", "PUT", {
                        "message": "Initialize organization platform license",
                        "content": files["LICENSE"], "branch": settings.branch,
                    })
                    parent = initial["commit"]["sha"]
                    step = "verifying the initial repository contents"
                    checked_parent, _ = await source_copy_base(repo, settings.branch, tree_entries)
                    if checked_parent != parent:
                        raise ValueError("Repository branch changed during initialization")
                step = "creating the source tree"
                tree = await github_request(f"/repos/{repo}/git/trees", "POST", {"tree": tree_entries})
                step = "creating the source commit"
                commit = await github_request(f"/repos/{repo}/git/commits", "POST", {"message": "Initialize organization deployment platform", "tree": tree["sha"], "parents": [parent]})
                step = "updating the repository branch"
                current = await github_request(f"/repos/{repo}/git/ref/heads/{quote(settings.branch, safe='/')}")
                if current["object"]["sha"] != parent:
                    raise ValueError("Repository branch changed during source copy")
                await github_request(f"/repos/{repo}/git/refs/heads/{quote(settings.branch, safe='/')}", "PATCH", {"sha": commit["sha"], "force": False})
                step = "verifying the copied commit"
                observed = await github_request(f"/repos/{repo}/git/ref/heads/{quote(settings.branch, safe='/')}")
                if observed["object"]["sha"] != commit["sha"]:
                    raise ValueError("Repository branch readback did not match the copied commit")
                saved["seeded"] = True
                db.set_setup_setting("github", saved)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise ValueError(seed_failure(exc, step)) from None
        return saved


def catalog_snapshot() -> dict:
    return db.get_setup_setting("catalog_snapshot", {})


def visible_catalog_snapshot() -> dict:
    snapshot = catalog_snapshot()
    if not snapshot:
        return snapshot
    repository = str(snapshot.get("repository", "")).lower()
    removed = db.get_setup_setting("catalog_removed", {}).get(repository, {})
    return {**snapshot, "items": [item for item in snapshot.get("items", []) if item.get("path") not in removed]}


def catalog_management() -> dict:
    snapshot = catalog_snapshot()
    repository = str(snapshot.get("repository", "")).lower()
    removed = db.get_setup_setting("catalog_removed", {}).get(repository, {})
    items = {item["path"]: item for item in snapshot.get("items", [])}
    rows = []
    for slug in sorted(items.keys() | removed.keys()):
        item = items.get(slug, {})
        removal = removed.get(slug, {})
        rows.append({"path": slug, "displayName": item.get("displayName", removal.get("displayName", slug)),
                     "category": item.get("category", ""), "removed": slug in removed,
                     "inSnapshot": slug in items, "removedAt": removal.get("removedAt")})
    configuration = db.get_setup_setting("github", {})
    configured_repo = f"{configuration.get('owner', '')}/{configuration.get('repository', '')}".lower()
    can_request = configuration.get("enabled") is True and configured_repo == repository
    return {"repository": repository, "version": snapshot.get("version", ""), "items": rows,
            "branch": configuration.get("branch", "") if can_request else "",
            "sourceCommit": snapshot.get("sourceCommit", ""), "repositoryRemovalAvailable": can_request}


class CatalogRepositoryConflict(ValueError):
    """The confirmed repository source changed or could not be verified."""


def _removal_tree_entries(document: dict) -> list[dict]:
    if not isinstance(document, dict) or document.get("truncated") or not isinstance(document.get("tree"), list):
        raise CatalogRepositoryConflict("The repository tree could not be fully verified. Inspect the configured branch before retrying.")
    result = []
    names = set()
    for entry in document["tree"]:
        if not isinstance(entry, dict):
            raise CatalogRepositoryConflict("Invalid repository tree.")
        name = entry.get("path")
        if (not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name
                or name in names or any(ord(character) < 32 for character in name)
                or (entry.get("mode"), entry.get("type")) not in {
                    ("040000", "tree"), ("100644", "blob"), ("100755", "blob"), ("120000", "blob"), ("160000", "commit")}
                or not re.fullmatch(r"[a-f0-9]{40}", str(entry.get("sha", "")))):
            raise CatalogRepositoryConflict("Unsupported repository tree entry. Inspect the configured branch before retrying.")
        names.add(name)
        result.append({key: entry[key] for key in ("path", "mode", "type", "sha")})
    return sorted(result, key=lambda entry: entry["path"])


class CatalogRepositoryFailure(ValueError):
    def __init__(self, stage: str, *, status: int | None = None, rejected: bool = False) -> None:
        operation = {"access": "checking repository access", "source": "reading catalog source",
                     "delete": "committing the folder deletion", "verify": "verifying the deletion"}.get(stage, "processing the deletion")
        if rejected:
            detail = "GitHub rejected the deletion. Check repository write permission and branch protection/rules; required pull requests will not be bypassed. Sync and confirm the latest source before retrying."
        else:
            detail = f"Repository deletion could not be confirmed while {operation}"
            detail += f" (HTTP {status}). " if status is not None else ". "
            detail += "Check GitHub access and connectivity. "
        if stage in ("delete", "verify"):
            detail += " The deletion may already have committed; inspect the configured branch and sync before retrying."
        self.stage = stage
        self.http_status = status
        super().__init__(detail)


async def delete_catalog_repository_folder(use_case: str, repository: str, version: str, branch: str, source_commit: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", use_case) or not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("Invalid use case or source commit")
    async with _SYNC_LOCK:
        configuration = db.get_setup_setting("github", {})
        if not configuration.get("enabled"):
            raise CatalogRepositoryConflict("Connect the repository before deleting source.")
        selected = GitHubSettings(owner=configuration["owner"], repository=configuration["repository"], branch=configuration["branch"])
        repo = f"{selected.owner}/{selected.repository}"
        snapshot = catalog_snapshot()
        removed = db.get_setup_setting("catalog_removed", {}).get(repo.lower(), {})
        if (repository.lower() != repo.lower() or snapshot.get("repository", "").lower() != repo.lower()
                or version != snapshot.get("version") or source_commit != snapshot.get("sourceCommit") or branch != selected.branch
                or (use_case not in {item.get("path") for item in snapshot.get("items", [])} and use_case not in removed)):
            raise CatalogRepositoryConflict("The catalog or repository selection changed. Refresh and confirm again.")
        token = await github_device.ensure_access_token()
        if not token:
            raise GitHubConnectionError()
        stage = "access"
        async def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
            try:
                result = await github_request(path, method, body, token=token)
            except httpx.HTTPStatusError as error:
                raise CatalogRepositoryFailure(stage, status=error.response.status_code) from None
            except httpx.HTTPError:
                raise CatalogRepositoryFailure(stage) from None
            if not isinstance(result, dict):
                raise CatalogRepositoryFailure(stage)
            return result

        root_path = f"/repos/{repo}"
        remote = await request(root_path)
        permissions = remote.get("permissions")
        if (str(remote.get("full_name", "")).lower() != repo.lower() or remote.get("archived") or remote.get("disabled")
                or not isinstance(permissions, dict) or permissions.get("push") is not True):
            raise CatalogRepositoryConflict("Repository write access could not be verified. No files were deleted.")
        stage = "source"
        base_path = f"{root_path}/git/ref/heads/{quote(branch, safe='/')}"
        async def verify_head(expected: str) -> None:
            ref = await request(base_path)
            if ref.get("ref") != f"refs/heads/{branch}" or (ref.get("object") or {}).get("sha") != expected:
                if stage == "verify":
                    raise CatalogRepositoryFailure(stage)
                raise CatalogRepositoryConflict("The repository branch changed. Sync and confirm the latest source before retrying.")
        async def tree_for(commit_sha: str) -> tuple[dict, list[dict], list[dict]]:
            commit = await request(f"{root_path}/git/commits/{commit_sha}")
            tree_sha = (commit.get("tree") or {}).get("sha", "")
            if not re.fullmatch(r"[a-f0-9]{40}", str(tree_sha)):
                raise CatalogRepositoryFailure(stage)
            root_entries = _removal_tree_entries(await request(f"{root_path}/git/trees/{tree_sha}"))
            catalog = next((entry for entry in root_entries if entry["path"] == "catalog"), None)
            if catalog and catalog["type"] != "tree":
                raise CatalogRepositoryConflict("The catalog path is not a directory.")
            catalog_entries = _removal_tree_entries(await request(f"{root_path}/git/trees/{catalog['sha']}")) if catalog else []
            return commit, root_entries, catalog_entries

        await verify_head(source_commit)
        _, root_entries, catalog_entries = await tree_for(source_commit)
        target = next((entry for entry in catalog_entries if entry["path"] == use_case), None)
        if target and target["type"] != "tree":
            raise CatalogRepositoryConflict("The use-case path is not a directory. No files were deleted.")
        folder = f"catalog/{use_case}/"
        deletions = []
        directories = 0
        async def collect(tree_sha: str, prefix: str, depth: int = 0) -> None:
            nonlocal directories
            directories += 1
            if depth > 8 or directories > 150:
                raise CatalogRepositoryConflict("The use-case folder exceeds the deletion limits. No files were deleted.")
            for entry in _removal_tree_entries(await request(f"{root_path}/git/trees/{tree_sha}")):
                path = prefix + entry["path"]
                if entry["type"] == "tree":
                    await collect(entry["sha"], path + "/", depth + 1)
                elif entry["type"] == "blob":
                    if len(path) > 1024 or len(deletions) >= 500:
                        raise CatalogRepositoryConflict("The use-case folder exceeds the deletion limits. No files were deleted.")
                    deletions.append({"path": path})
                else:
                    raise CatalogRepositoryConflict("Use-case folders containing submodules require manual repository review. No files were deleted.")
        candidate = source_commit
        if target:
            await collect(target["sha"], folder)
            if not deletions:
                raise CatalogRepositoryConflict("No files could be verified in the use-case folder. No deletion was sent.")
            await verify_head(source_commit)
            stage = "delete"
            result = await request("/graphql", "POST", {
                "query": "mutation DeleteCatalogFolder($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
                "variables": {"input": {"branch": {"repositoryNameWithOwner": repo, "branchName": branch},
                    "expectedHeadOid": source_commit, "message": {"headline": f"Remove {folder} by portal administrator"},
                    "fileChanges": {"deletions": deletions}}},
            })
            if result.get("errors"):
                raise CatalogRepositoryFailure(stage, rejected=True)
            payload = (result.get("data") or {}).get("createCommitOnBranch") or {}
            candidate = payload.get("commit") or {}
            candidate = candidate.get("oid", "")
            if not re.fullmatch(r"[a-f0-9]{40}", str(candidate)) or candidate == source_commit:
                raise CatalogRepositoryFailure(stage)
            stage = "verify"
            try:
                proposed, observed_root, observed_catalog = await tree_for(candidate)
            except (CatalogRepositoryConflict, KeyError, TypeError, AttributeError):
                raise CatalogRepositoryFailure(stage) from None
            if ([parent.get("sha") for parent in proposed.get("parents", [])] != [source_commit]
                    or [entry for entry in observed_root if entry["path"] != "catalog"] != [entry for entry in root_entries if entry["path"] != "catalog"]
                    or observed_catalog != [entry for entry in catalog_entries if entry["path"] != use_case]):
                raise CatalogRepositoryFailure(stage)
        stage = "verify"
        await verify_head(candidate)
        db.set_setup_setting("catalog_snapshot", {**snapshot, "items": [item for item in snapshot.get("items", []) if item.get("path") != use_case]})
        all_removed = db.get_setup_setting("catalog_removed", {})
        removals = all_removed.get(repo.lower(), {})
        removals.pop(use_case, None)
        if removals:
            all_removed[repo.lower()] = removals
        else:
            all_removed.pop(repo.lower(), None)
        db.set_setup_setting("catalog_removed", all_removed)
        queue_catalog_refresh()
        return {"status": "deleted", "changed": target is not None, "path": use_case, "folder": folder,
                "branch": branch, "commit": candidate, "deletedFiles": len(deletions),
                "url": f"https://github.com/{repo.lower()}/commit/{candidate}"}


async def create_workflow_pr(confirm: bool) -> dict:
    if not confirm:
        raise ValueError("Explicit confirmation is required for a workflow pull request")
    async with _SYNC_LOCK:
        settings = db.get_setup_setting("github", {})
        if not settings.get("enabled"):
            raise ValueError("Connect a repository first")
        processing = catalog_processing.settings()
        if processing["mode"] == "container":
            raise ValueError("Container processing needs no workflows. Select and verify runners before installing workflows.")
        if not catalog_processing.runner_available(processing, await processing_probe(force=True)):
            raise ValueError("Runner access is unavailable or unverified. Use container processing.")
        seed = source_seed()
        names = ("tools/portal_workflows.py", "tools/portal_catalog.py", "site/server/pricing.py", "site/server/catalog_workflows.py",
             "site/server/catalog_processing.py", "site/server/setup_cloud.py", "site/server/lifecycle.py", "site/requirements.txt")
        files = {name: seed[name] for name in names}
        files.update({name: base64.b64encode(content).decode() for name, content in catalog_workflows.workflow_files(
            processing["runnerLabels"] if processing["mode"] == "self-hosted" else None).items()})
        repo = f"{settings['owner']}/{settings['repository']}"
        digest = hashlib.sha256(json.dumps([settings["branch"], files], sort_keys=True).encode()).hexdigest()[:16]
        branch = f"portal/workflows-{digest}"
        existing = await github_request(f"/repos/{repo}/pulls", params={"state": "open", "head": f"{settings['owner']}:{branch}", "base": settings["branch"]})
        if existing:
            return {"status": "review", "url": f"https://github.com/{repo}/pull/{int(existing[0]['number'])}"}
        await github_request("/user", require_workflow=True)
        ref = await github_request(f"/repos/{repo}/git/ref/heads/{quote(settings['branch'], safe='/')}")
        parent = ref["object"]["sha"]
        commit = await github_request(f"/repos/{repo}/git/commits/{parent}")
        changes = {}
        for name, content in files.items():
            try:
                current = await github_request(f"/repos/{repo}/contents/{name}", params={"ref": parent})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                current = {}
            if current.get("type") == "symlink" or current.get("type") == "submodule":
                raise ValueError("Workflow destination must not be a symlink or submodule")
            if current.get("encoding") != "base64" or base64.b64decode(current.get("content", "")) != base64.b64decode(content):
                changes[name] = content
        if not changes:
            return {"status": "current"}
        entries = []
        for name, content in changes.items():
            blob = await github_request(f"/repos/{repo}/git/blobs", "POST", {"encoding": "base64", "content": content})
            entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = await github_request(f"/repos/{repo}/git/trees", "POST", {"base_tree": commit["tree"]["sha"], "tree": entries})
        updated = await github_request(f"/repos/{repo}/git/commits", "POST", {"message": "Add organization portal build and refresh automation", "tree": tree["sha"], "parents": [parent]})
        await github_request(f"/repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": updated["sha"]})
        pull = await github_request(f"/repos/{repo}/pulls", "POST", {
            "title": "Add organization portal build and refresh automation", "head": branch, "base": settings["branch"],
            "body": "Review these organization-owned workflows and helper changes before merging. Catalog compilation and public retail refresh need no Azure identity. Capacity remains disabled until PORTAL_CAPACITY_ENABLED and organization OIDC configuration are supplied. No portal deployment, Azure role grants, or resource changes are included. See the organization workflow documentation before enabling capacity.",
        })
        return {"status": "review", "url": f"https://github.com/{repo}/pull/{int(pull['number'])}"}


def capacity_snapshot() -> dict:
    snapshot = catalog_snapshot().get("capacity")
    if not snapshot:
        return {"regions": [], "services": {}}
    allowed = {item["subscriptionId"] for item in setup_config.subscriptions()}
    return {**{key: value for key, value in snapshot.items() if key != "regional"},
            "subscriptions": {identifier: regions for identifier, regions in snapshot["subscriptions"].items() if identifier in allowed}}


def validate_capacity(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1 or snapshot.get("physicalCapacity") != "unknown":
        raise ValueError("Invalid capacity snapshot")
    if snapshot.get("tenantId") != os.environ.get("GALLERY_ENTRA_TENANT_ID", "").lower():
        raise ValueError("Capacity snapshot belongs to another tenant")
    if not isinstance(snapshot.get("generated"), str):
        raise ValueError("Invalid capacity timestamp")
    generated = datetime.fromisoformat(snapshot["generated"])
    if generated.tzinfo is None or generated.timestamp() > time.time() + 300:
        raise ValueError("Invalid capacity timestamp")
    subscriptions = snapshot.get("subscriptions")
    if not isinstance(subscriptions, dict) or len(subscriptions) > 200:
        raise ValueError("Invalid capacity subscriptions")
    pairs = 0
    clean = {}
    for identifier, regions in subscriptions.items():
        if setup_config.canonical_guid(identifier) != identifier or not isinstance(regions, dict) or len(regions) > 200:
            raise ValueError("Invalid capacity scope")
        pairs += len(regions)
        clean[identifier] = {}
        for region, rows in regions.items():
            if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", region) or not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError("Invalid capacity rows")
            clean[identifier][region] = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not 1 <= len(row["name"]) <= 200:
                    raise ValueError("Invalid quota name")
                if any(type(row.get(key)) is not int or row[key] < 0 for key in ("limit", "current", "available")):
                    raise ValueError("Invalid quota counters")
                if row["available"] != max(0, row["limit"] - row["current"]):
                    raise ValueError("Invalid quota headroom")
                clean[identifier][region].append({key: row[key] for key in ("name", "limit", "current", "available")})
    if pairs > 40000:
        raise ValueError("Capacity scope exceeds the limit")
    result = {"schemaVersion": 1, "tenantId": snapshot["tenantId"], "generated": generated.astimezone(timezone.utc).isoformat(),
              "physicalCapacity": "unknown", "subscriptions": clean, "regions": [], "services": {}}
    if "regional" in snapshot:
        result["regional"] = catalog_processing.validate_runner_snapshot(snapshot["regional"])
    return result


def generated_cost(document: dict, metadata: bytes, authored: dict) -> dict | None:
    if not isinstance(document, dict):
        raise ValueError("Invalid generated prices")
    if document.get("metadataSha256") != hashlib.sha256(metadata).hexdigest():
        return None
    priced = document.get("cost")
    if not isinstance(priced, dict) or priced.get("pricingSource") != "retail" or priced.get("complete") is not True:
        raise ValueError("Invalid retail snapshot")
    if priced.get("currency") != authored.get("currency", "USD") or priced.get("region") != authored.get("region", "eastus"):
        raise ValueError("Price scope does not match metadata")
    rows = priced.get("components")
    components = authored.get("components", [])
    if not isinstance(rows, list) or not rows or len(rows) != len(components) or len(rows) > 200:
        raise ValueError("Price components do not match metadata")
    result = []
    for original, row in zip(components, rows, strict=True):
        unit = row.get("unitPrice") if isinstance(row, dict) else None
        quantity = original.get("quantityPerMonth", 0)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in (unit, quantity)):
            raise ValueError("Invalid retail price")
        monthly = unit * quantity
        if not math.isfinite(monthly):
            raise ValueError("Invalid retail total")
        result.append({**original, "unitPrice": unit, "monthly": round(monthly, 2)})
    if not isinstance(priced.get("pricedAt"), str):
        raise ValueError("Invalid pricing timestamp")
    generated = datetime.fromisoformat(priced["pricedAt"])
    if generated.tzinfo is None or generated.timestamp() > time.time() + 300:
        raise ValueError("Invalid pricing timestamp")
    fixed = round(sum(row["monthly"] for row in result if row.get("billing") == "fixed"), 2)
    usage = round(sum(row["monthly"] for row in result if row.get("billing") != "fixed"), 2)
    return {**authored, "components": result, "fixedMonthly": fixed, "usageMonthly": usage, "totalMonthly": round(fixed + usage, 2),
            "complete": True, "pricedAt": generated.isoformat(), "pricingSource": "retail"}


def snapshot_root(version: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", version):
        raise ValueError("Invalid catalog version")
    return db._default_db_path().parent / "catalog-versions" / version


async def processing_probe(force: bool = False) -> dict:
    configuration = db.get_setup_setting("github", {})
    if not configuration.get("enabled"):
        return {"repository": "", "checkedAt": time.time(), "github-hosted": {"available": False, "state": "unverified", "detail": "Connect a repository first."},
                "self-hosted": {"available": False, "state": "unverified", "detail": "Connect a repository first.", "candidates": []}}
    repo = f"{configuration['owner']}/{configuration['repository']}"
    cached = db.get_setup_setting("runner_probe", {})
    if not force and cached.get("repository") == repo and time.time() - cached.get("checkedAt", 0) < 600:
        return cached
    try:
        result = await asyncio.wait_for(catalog_processing.probe_runners(repo, github_request), timeout=45)
    except TimeoutError:
        result = {"repository": repo, "checkedAt": time.time(), "github-hosted": {"available": False, "state": "unverified", "detail": "Runner check timed out. Container processing is the fallback."},
                  "self-hosted": {"available": False, "state": "unverified", "detail": "Runner check timed out.", "candidates": []}}
    db.set_setup_setting("runner_probe", result)
    return result


def catalog_refresh_status() -> dict:
    if not db.get_setup_setting("github", {}).get("enabled"):
        return {"status": "not_configured"}
    job = db.get_setup_setting("catalog_job", {})
    status = {"status": job.get("status", "idle"), "completedAt": job.get("completedAt")}
    if status["status"] == "failed" and job.get("code") == "github_reconnect_required":
        status["code"] = "github_reconnect_required"
    return status


def queue_catalog_refresh(force: bool = True) -> dict:
    if not db.get_setup_setting("github", {}).get("enabled"):
        raise ValueError("Connect a repository first")
    job = db.get_setup_setting("catalog_job", {})
    if job.get("status") in ("queued", "running"):
        if not force and job["status"] == "running":
            _SYNC_WAKE.set()
        if force and job["status"] == "queued" and not job.get("force", True):
            job = {**job, "force": True}
            db.set_setup_setting("catalog_job", job)
        return job
    remaining = 10 - (time.time() - job.get("completedAt", 0))
    if not force and remaining > 0:
        return {"status": "throttled", "retryAfter": math.ceil(remaining)}
    job = {"status": "queued", "requestedAt": time.time(), "force": force,
           "detail": "Catalog validation, pricing and quota refresh queued."}
    db.set_setup_setting("catalog_job", job)
    _SYNC_WAKE.set()
    return job


def _catalog_stage(stage: str) -> None:
    job = db.get_setup_setting("catalog_job", {})
    if job.get("status") == "running":
        db.set_setup_setting("catalog_job", {**job, "stage": stage})


def _catalog_failure(error: Exception) -> dict:
    job = db.get_setup_setting("catalog_job", {})
    stages = {
        "repository": "reading the configured repository",
        "validation_pricing": "validating source and refreshing public prices",
        "capacity": "reading subscription regions and quota data",
        "capacity_validation": "validating the capacity snapshot",
        "artifacts": "preparing catalog artifacts and prices",
        "publication": "saving the catalog snapshot",
    }
    stage = job.get("stage", "unknown")
    if stage not in stages:
        stage = "unknown"
    category = "invalid_response"
    hint = "Check the input format and response completeness."
    status = None
    if isinstance(error, GitHubConnectionError):
        category = "github_reconnect_required"
        hint = str(GitHubConnectionError())
    elif isinstance(error, httpx.HTTPStatusError):
        category = "http_error"
        status = error.response.status_code
        hint = f"The upstream service returned HTTP {status}. Check access, rate limits and service availability."
    elif isinstance(error, (TimeoutError, httpx.TimeoutException)):
        category = "timeout"
        hint = "The operation exceeded its time limit."
    elif isinstance(error, httpx.RequestError):
        category = "connection_error"
        hint = "The upstream service could not be reached."
    elif isinstance(error, AzureError):
        category = "azure_access_error"
        hint = "Verify managed-identity authentication and access for this operation."
    elif isinstance(error, OSError):
        category = "storage_error"
        hint = "Verify writable storage, available space and file access."
    elif isinstance(error, ValueError) and str(error) in {
        "Retail prices are incomplete; the previous catalog remains active",
        "Subscription tenant could not be verified", "Subscription is not enabled",
        "Incomplete quota response; previous capacity remains active", "Invalid quota counters",
        "Price scope does not match metadata", "Price components do not match metadata",
        "Do not publish literal secure parameter values",
        "Parameter file must reference this workload's main.bicep",
    }:
        hint = str(error).rstrip(".") + "."
    detail = f"Processing failed while {stages.get(stage, 'processing the catalog')}. {hint} The last good catalog is still active."
    return {"status": "failed", "startedAt": job.get("startedAt"), "completedAt": time.time(),
            "stage": stage, "code": category, "httpStatus": status, "detail": detail}


def template_parameters(template: dict, values: dict) -> list[dict]:
    declarations = template.get("parameters", {})
    if not isinstance(declarations, dict):
        raise ValueError("Invalid template parameter declarations")
    managed = {"location", "environment", "tags", "resourceGroupName", "resource_group_name"}
    parameters = []
    for name, declaration in declarations.items():
        if name in managed or not isinstance(declaration, dict):
            continue
        kind = str(declaration.get("type", "")).lower()
        if kind not in ("string", "int", "bool") or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            continue
        expected = {"string": str, "int": int, "bool": bool}[kind]
        parameter = {"name": name, "type": kind}
        metadata = declaration.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("description"), str):
            parameter["description"] = metadata["description"][:2000]
        supplied = values.get(name, {})
        default = supplied.get("value") if isinstance(supplied, dict) and "value" in supplied else declaration.get("defaultValue")
        if type(default) is expected and not (isinstance(default, str) and (default.startswith("[") or len(default) > 256)):
            parameter["default"] = default
        allowed = declaration.get("allowedValues")
        if isinstance(allowed, list) and allowed and all(type(value) is expected for value in allowed):
            parameter["allowed"] = allowed
        if kind == "int":
            for source, target in (("minValue", "min"), ("maxValue", "max")):
                if type(declaration.get(source)) is int:
                    parameter[target] = declaration[source]
        parameters.append(parameter)
    return parameters


async def sync_catalog(force: bool = False) -> dict:
    async with _SYNC_LOCK:
        _catalog_stage("repository")
        settings = db.get_setup_setting("github", {})
        if not settings.get("enabled"):
            raise ValueError("GitHub is disabled")
        repo = f"{settings['owner']}/{settings['repository']}"
        processing = catalog_processing.settings()
        mode = processing["mode"]
        fallback = False
        if mode != "container" and not catalog_processing.runner_available(processing, await processing_probe()):
            mode, fallback = "container", True
        db.set_setup_setting("processing_effective", {"mode": mode, "fallback": fallback,
            "detail": "Runner access is unavailable or unverified; processing in the portal container." if fallback else ""})
        ref = await github_request(f"/repos/{repo}/git/ref/heads/{quote(settings['branch'], safe='/')}")
        version = str(ref["object"]["sha"])
        source_version = version
        current = catalog_snapshot()
        processing_key = json.dumps([mode, processing["capacityRegions"], setup_config.subscriptions(), "template-parameters-v1"], sort_keys=True)
        if not force and current.get("sourceCommit", current.get("version")) == version and current.get("repository") == repo and current.get("processingKey") == processing_key and time.time() - current.get("syncedAt", 0) < 86400:
            return current
        commit = await github_request(f"/repos/{repo}/git/commits/{version}")
        tree = await github_request(f"/repos/{repo}/git/trees/{commit['tree']['sha']}")
        if tree.get("truncated"):
            raise ValueError("Repository tree is truncated")
        catalog_entry = next((entry for entry in tree.get("tree", []) if entry.get("path") == "catalog" and entry.get("type") == "tree"), None)
        candidates: list[dict] = []
        if catalog_entry:
            await _walk_tree(repo, catalog_entry["sha"], "catalog", candidates, budget=[0], sources=mode == "container")
        modules_entry = next((entry for entry in tree.get("tree", []) if entry.get("path") == "modules" and entry.get("type") == "tree"), None)
        if mode == "container" and modules_entry:
            await _walk_tree(repo, modules_entry["sha"], "modules", candidates, budget=[0], sources=True)
        site_entry = next((entry for entry in tree.get("tree", []) if entry.get("path") == "site" and entry.get("type") == "tree"), None)
        if site_entry:
            site_tree = await github_request(f"/repos/{repo}/git/trees/{site_entry['sha']}")
            if site_tree.get("truncated"):
                raise ValueError("Site tree is truncated")
            candidates.extend({**entry, "path": "site/" + entry["path"]} for entry in site_tree.get("tree", [])
                              if entry.get("path") in (("capacity.json", "updates.json") if mode != "container" else ("capacity.json",)) and entry.get("type") == "blob")
        blobs: dict[str, bytes] = {}
        total = 0
        capacity_size = 0
        for entry in candidates:
            name = entry["path"]
            generated_input = re.fullmatch(r"catalog/[a-z0-9][a-z0-9-]{0,79}/(?:metadata\.json|pricing\.json|compiled/(?:dev|prod)\.json|terraform/[A-Za-z0-9_.-]+\.(?:tf|tfvars|json))", name)
            local_source = mode == "container" and re.fullmatch(r"(?:catalog|modules)/(?:[A-Za-z0-9_-]+/){1,8}[A-Za-z0-9_.-]+\.(?:bicep|bicepparam|json|txt|yaml|yml)", name)
            if name not in ("site/capacity.json", "site/updates.json") and not generated_input and not local_source:
                continue
            file_limit = (16 if name == "site/capacity.json" else 2) * 1024 * 1024
            if entry.get("mode") != "100644" or entry.get("size", 0) > file_limit:
                raise ValueError("Unsupported catalog file mode or size")
            if name == "site/capacity.json" and entry.get("size", 0) > 4 * 1024 * 1024:
                blob = await github_request(f"/repos/{repo}/git/blobs/{entry['sha']}", response_limit=24 * 1024 * 1024)
            else:
                blob = await github_request(f"/repos/{repo}/git/blobs/{entry['sha']}")
            content = base64.b64decode(blob["content"], validate=False)
            total += len(content)
            if name == "site/capacity.json":
                capacity_size = len(content)
            if total - capacity_size > 16 * 1024 * 1024:
                raise ValueError("Catalog exceeds 16 MiB")
            if len(content) > file_limit:
                raise ValueError("Catalog file exceeds 2 MiB")
            blobs[name] = content
        if mode == "container":
            _catalog_stage("validation_pricing")
            blobs = await catalog_processing.process_blobs(blobs)
            _catalog_stage("capacity")
            snapshot = await catalog_processing.refresh_capacity(processing["capacityRegions"])
            blobs.pop("site/capacity.json", None)
            if snapshot is not None:
                blobs["site/capacity.json"] = json.dumps(snapshot).encode()
        elif "site/updates.json" in blobs:
            lifecycle.import_runner(blobs.pop("site/updates.json"))
        artifact_hash = hashlib.sha256()
        artifact_hash.update(f"{repo}:{version}:{mode}".encode())
        for name, content in sorted(blobs.items()):
            if "/compiled/" in name or "/terraform/" in name:
                artifact_hash.update(name.encode())
                artifact_hash.update(hashlib.sha256(content).digest())
        artifact_version = artifact_hash.hexdigest()[:40]
        root = snapshot_root(artifact_version)
        capacity = None
        if "site/capacity.json" in blobs:
            _catalog_stage("capacity_validation")
            document = json.loads(blobs["site/capacity.json"])
            if document != {"regions": [], "services": {}}:
                capacity = validate_capacity(document)
        _catalog_stage("artifacts")
        items = []
        for name, content in blobs.items():
            if not re.fullmatch(r"catalog/[^/]+/metadata\.json", name):
                continue
            slug = name.split("/")[1]
            item = json.loads(content)
            if not isinstance(item, dict) or not isinstance(item.get("displayName"), str):
                raise ValueError("Each use case requires metadata.json with a displayName")
            prices = blobs.get(f"catalog/{slug}/pricing.json")
            if prices:
                cost = generated_cost(json.loads(prices), content, item.get("cost") or {})
                if cost is not None:
                    item["cost"] = cost
            engines = []
            for environment in ("dev", "prod"):
                source = f"catalog/{slug}/compiled/{environment}.json"
                if source in blobs:
                    compiled = json.loads(blobs[source])
                    if not isinstance(compiled.get("template"), dict) or not isinstance(compiled.get("parameters", {}), dict):
                        raise ValueError("Invalid organization compiled ARM input")
                    target = root / "_templates" / slug / f"{environment}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blobs[source])
                    if environment == "dev":
                        engines.append("bicep")
                        item["parameters"] = template_parameters(compiled["template"], compiled.get("parameters", {}))
            for source, data in blobs.items():
                prefix = f"catalog/{slug}/terraform/"
                if source.startswith(prefix):
                    target = root / "_tf" / slug / source.removeprefix(prefix)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    if target.name == "main.tf":
                        engines.append("terraform")
            item.update({"name": slug, "path": slug, "engines": engines, "deployReady": bool(engines), "hasProd": f"catalog/{slug}/compiled/prod.json" in blobs, "catalogVersion": artifact_version})
            items.append(item)
        result = {"version": artifact_version, "sourceCommit": source_version, "items": items, "syncedAt": time.time(), "repository": repo, "capacity": capacity,
                  "processingKey": processing_key, "processingMode": mode,
                  "capacityDetail": "Compute quotas checked across subscription regions; empty results mean unavailable or not reported, not unlimited capacity." if capacity else "No quota snapshot: configure deployment subscriptions in Setup."}
        _catalog_stage("publication")
        db.set_setup_setting("catalog_snapshot", result)
        if mode != "container" and capacity and capacity.get("regional"):
            db.set_setup_setting("regional_snapshot", capacity["regional"])
        db.set_setup_setting("github_sync_error", None)
        return result


async def _walk_tree(repo: str, sha: str, prefix: str, entries: list[dict], depth: int = 0, budget: list[int] | None = None, sources: bool = False) -> None:
    if depth > (8 if sources else 2):
        if sources:
            raise ValueError("Catalog source nesting exceeds the validation limit")
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
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", entry["path"]):
                continue
            if not sources and depth == 1 and entry["path"] not in ("compiled", "terraform"):
                continue
            await _walk_tree(repo, entry["sha"], path, entries, depth + 1, budget, sources)
        elif entry.get("type") == "blob":
            if sources and entry.get("mode") != "100644":
                raise ValueError("Source validation does not accept symlinks or executable repository files")
            entries.append({**entry, "path": path})
        elif sources:
            raise ValueError("Source validation does not accept submodules")
        if len(entries) > 500:
            raise ValueError("Catalog exceeds 500 files")


async def run_catalog_sync(stop: asyncio.Event) -> None:
    if db.get_setup_setting("catalog_job", {}).get("status") == "running":
        db.set_setup_setting("catalog_job", {"status": "queued", "requestedAt": time.time(), "detail": "Resuming processing after a container restart."})
    while not stop.is_set():
        _SYNC_WAKE.clear()
        if db.get_setup_setting("github", {}).get("enabled"):
            job = db.get_setup_setting("catalog_job", {})
            manual = job.get("status") == "queued" and job.get("force", True)
            db.set_setup_setting("catalog_job", {"status": "running", "startedAt": time.time(), "detail": "Checking runners, validating catalog inputs and refreshing snapshots."})
            try:
                result = await asyncio.wait_for(sync_catalog(force=manual), timeout=1200)
                db.set_setup_setting("catalog_job", {"status": "succeeded", "completedAt": time.time(), "detail": "Catalog processing completed.",
                    "mode": result.get("processingMode"), "sourceCommit": result.get("sourceCommit", result["version"]), "count": len(result["items"]), "capacityDetail": result.get("capacityDetail", "")})
                if _SYNC_WAKE.is_set():
                    db.set_setup_setting("catalog_job", {"status": "queued", "force": False, "requestedAt": time.time(),
                                                        "detail": "Checking for changes merged during processing."})
            except catalog_processing.CatalogValidationError as error:
                message = error.safe_detail + " The last good catalog is still active."
                db.set_setup_setting("github_sync_error", message)
                db.set_setup_setting("catalog_job", {"status": "failed", "completedAt": time.time(),
                                                    "code": "catalog_validation_failed", "detail": message})
            except (ValueError, KeyError, TypeError, httpx.HTTPError, AzureError, OSError, TimeoutError) as error:
                failure = _catalog_failure(error)
                db.set_setup_setting("github_sync_error", failure["detail"])
                db.set_setup_setting("catalog_job", failure)
            except asyncio.CancelledError:
                db.set_setup_setting("catalog_job", {"status": "queued", "requestedAt": time.time(), "detail": "Processing interrupted; it will retry after restart."})
                raise
        waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(_SYNC_WAKE.wait())]
        try:
            await asyncio.wait(waiters, timeout=300, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)