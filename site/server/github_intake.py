"""Create a review branch for a staged use-case submission via the GitHub API.

The intake flow the gallery implements is:

    upload -> validate -> stage -> **create branch + commit the bundle** ->
    inform admins -> admin opens the PR and merges.

This module owns the "create branch + commit" step. It uses the GitHub Git Data
API (blobs -> tree -> commit -> ref) over HTTPS, which needs only ``contents:
write`` on the repository. The token is read from the environment and is NEVER
logged. When no token is configured the feature degrades gracefully: the caller
keeps the submission staged for manual download (see api.py / submissions.py).

Opening the pull request itself is intentionally left to a human: org policy
blocks GitHub Actions/automation from creating PRs, so admins are handed a
pre-filled compare link instead (a one-click "Create pull request").
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

API_ROOT = "https://api.github.com"
_VALIDATION_LOCK = asyncio.Lock()


def _token() -> str:
    from github_device import access_token
    return access_token()


def _repo() -> str:
    # owner/repo the branches are created in.
    from distribution import is_customer_distribution
    if is_customer_distribution():
        import db
        settings = db.get_setup_setting("github")
        if settings is not None:
            return f"{settings['owner']}/{settings['repository']}" if settings.get("enabled") else ""
    return os.environ.get("GALLERY_GITHUB_REPO", "").strip()


def _base() -> str:
    from distribution import is_customer_distribution
    if is_customer_distribution():
        import db
        settings = db.get_setup_setting("github", {})
        if settings.get("enabled"):
            return settings["branch"]
    return os.environ.get("GALLERY_GITHUB_BASE", "main").strip() or "main"


def configured() -> bool:
    """True when repository authorization is available or can be renewed."""
    from github_device import renewal_available
    return bool(_repo()) and (bool(_token()) or renewal_available())


def compare_url(branch: str) -> str:
    return f"https://github.com/{_repo()}/compare/{_base()}...{branch}?expand=1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "techlabs-gallery-intake",
    }


class SubmissionValidationError(ValueError):
    """A proposed review branch failed source validation before any writes."""


async def validate_submission_sources(files: dict[str, bytes], repo: str, base_tree: str, token: str) -> None:
    import catalog_processing
    import setup_github
    from submissions import MAX_SINGLE_FILE, MAX_TOTAL_UNCOMPRESSED, REVIEW_ONLY_EXT

    prefixes = {name.rsplit("/", 1)[0] + "/" for name in files if re.fullmatch(r"catalog/[a-z0-9-]+/metadata\.json", name)}
    if not prefixes or any(not name.startswith(tuple(prefixes)) for name in files):
        raise SubmissionValidationError("The submission must contain workload files and metadata under its catalog folder.")
    if len(files) > 500 or sum(len(content) for content in files.values()) > MAX_TOTAL_UNCOMPRESSED or any(len(content) > MAX_SINGLE_FILE for content in files.values()):
        raise SubmissionValidationError("The submitted files exceed the upload limits (5 MiB per file, 25 MiB total).")
    sources = {name: content for name, content in files.items() if PurePosixPath(name).suffix.lower() not in REVIEW_ONLY_EXT}
    if sum(len(content) for content in sources.values()) > 16 * 1024 * 1024 or any(len(content) > 2 * 1024 * 1024 for content in sources.values()):
        raise SubmissionValidationError("The submitted files exceed the catalog validation limits (2 MiB per file, 16 MiB total).")
    async with _VALIDATION_LOCK:
        try:
            tree = await setup_github.github_request(f"/repos/{repo}/git/trees/{base_tree}", token=token, params={"recursive": "1"})
            if not isinstance(tree, dict) or tree.get("truncated") or not isinstance(tree.get("tree"), list):
                raise SubmissionValidationError("The repository tree could not be fully verified. No review branch was created.")
            total = sum(len(content) for content in sources.values())
            for entry in tree["tree"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise SubmissionValidationError("The repository tree could not be fully verified. No review branch was created.")
                name = entry.get("path", "")
                if entry.get("type") == "tree" or not name.startswith(("modules/", *prefixes)):
                    continue
                if entry.get("type") != "blob" or entry.get("mode") != "100644":
                    raise SubmissionValidationError("Source validation does not accept symlinks, executable files or submodules.")
                if name in sources or not re.fullmatch(r"(?:catalog|modules)/(?:[A-Za-z0-9_-]+/){1,8}[A-Za-z0-9_.-]+\.(?:bicep|bicepparam|tf|tfvars|json|txt|yaml|yml)", name):
                    continue
                if entry.get("size", 0) > 2 * 1024 * 1024 or not re.fullmatch(r"[a-f0-9]{40}", entry.get("sha", "")):
                    raise SubmissionValidationError("A repository dependency exceeds the validation limits.")
                blob = await setup_github.github_request(f"/repos/{repo}/git/blobs/{entry['sha']}", token=token, response_limit=3 * 1024 * 1024)
                content = base64.b64decode(blob["content"], validate=False)
                if len(content) > 2 * 1024 * 1024:
                    raise SubmissionValidationError("A repository dependency exceeds the validation limits.")
                sources[name] = content
                total += len(content)
                if total > 16 * 1024 * 1024 or len(sources) > 500:
                    raise SubmissionValidationError("The candidate workload and dependencies exceed the validation limits.")
            if total > 16 * 1024 * 1024 or len(sources) > 500:
                raise SubmissionValidationError("The candidate workload and dependencies exceed the validation limits.")
            await catalog_processing.process_blobs(sources, refresh_prices=False)
        except catalog_processing.CatalogValidationError as error:
            raise SubmissionValidationError(error.safe_detail) from None
        except SubmissionValidationError:
            raise
        except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError):
            raise SubmissionValidationError("Source validation could not finish. Check repository access, dependencies and compiler prerequisites; no review branch was created.") from None


class BranchDeletionError(ValueError):
    """A review branch could not be safely removed."""


async def delete_submission_branch(submission: dict[str, Any]) -> None:
    branch = submission.get("branch", "")
    if not branch:
        if submission.get("status") == "branched":
            raise BranchDeletionError("The review branch is missing from this record; verify it in GitHub before deleting.")
        return
    expected = f"usecase-intake/{submission['slug']}-{submission['id'][:6]}"
    if branch != expected or not re.fullmatch(r"usecase-intake/[a-z0-9-]+-[a-f0-9]{6}", branch):
        raise BranchDeletionError("The recorded branch is not this submission's review branch.")
    location = urlsplit(submission.get("compare_url", ""))
    match = re.fullmatch(r"/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/compare/(.+)", location.path)
    if location.scheme != "https" or location.netloc != "github.com" or location.fragment or not match:
        raise BranchDeletionError("The original repository could not be verified; the submission was kept.")
    owner, repository, comparison = match.groups()
    base, separator, head = unquote(comparison).partition("...")
    if repository in (".", "..") or not base or not separator or head != branch or base == branch:
        raise BranchDeletionError("The original review branch could not be verified; the submission was kept.")
    repo = f"{owner}/{repository}"
    if repo.lower() == _repo().lower() and branch == _base():
        raise BranchDeletionError("The publication branch cannot be deleted.")
    from github_device import ensure_access_token
    await ensure_access_token()
    if not _token():
        raise BranchDeletionError("Reconnect GitHub before deleting this review branch. The submission was kept.")
    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0, follow_redirects=False) as client:
            root = f"{API_ROOT}/repos/{repo}"
            response = await client.get(root)
            response.raise_for_status()
            remote = response.json()
            if str(remote.get("full_name", "")).lower() != repo.lower() or not remote.get("default_branch"):
                raise BranchDeletionError("GitHub repository verification failed; the submission was kept.")
            if remote.get("permissions", {}).get("push") is not True:
                raise BranchDeletionError("Repository write access is required to verify branch deletion; the submission was kept.")
            if branch == remote["default_branch"]:
                raise BranchDeletionError("The default branch cannot be deleted.")
            response = await client.get(f"{root}/branches/{quote(branch, safe='')}")
            if response.status_code == 404:
                return
            response.raise_for_status()
            if response.json().get("protected") is not False:
                raise BranchDeletionError("The review branch is protected; the submission was kept.")
            response = await client.delete(f"{root}/git/refs/heads/{quote(branch, safe='')}")
            response.raise_for_status()
            if response.status_code != 204:
                raise BranchDeletionError("GitHub did not confirm branch deletion; retry after checking the branch.")
    except httpx.HTTPStatusError as exc:
        raise BranchDeletionError(
            f"GitHub refused branch deletion (HTTP {exc.response.status_code}). Check repository access and branch rules; the submission was kept."
        ) from None
    except httpx.RequestError:
        raise BranchDeletionError("GitHub could not confirm branch deletion. The submission was kept; retry when GitHub is reachable.") from None


async def push_submission(
    *,
    branch: str,
    files: dict[str, bytes],
    message: str,
) -> dict[str, Any]:
    """Create ``branch`` off the base branch containing ``files``.

    ``files`` maps repository-relative paths (e.g. ``catalog/slug/main.bicep``)
    to their bytes. Returns ``{branch, commit, compareUrl}``. Raises on failure
    (the caller treats the push as best-effort and keeps the staged copy).
    """
    if not configured():
        raise RuntimeError("GitHub intake is not configured (no GALLERY_GITHUB_TOKEN).")
    if not files:
        raise RuntimeError("No files to commit.")

    from github_device import ensure_access_token
    await ensure_access_token()
    if not _token():
        raise ValueError("GitHub authorization could not be renewed. Reconnect GitHub in Setup > GitHub.")
    repo = _repo()
    base = _base()
    headers = _headers()
    async with httpx.AsyncClient(
        base_url=f"{API_ROOT}/repos/{repo}", headers=headers, timeout=30.0, follow_redirects=False
    ) as client:
        # 1. Resolve the base branch tip and its tree.
        r = await client.get(f"/git/ref/heads/{base}")
        r.raise_for_status()
        base_sha = r.json()["object"]["sha"]
        r = await client.get(f"/git/commits/{base_sha}")
        r.raise_for_status()
        base_tree = r.json()["tree"]["sha"]

        from distribution import is_customer_distribution
        if is_customer_distribution():
            try:
                await asyncio.wait_for(validate_submission_sources(files, repo, base_tree, headers["Authorization"].removeprefix("Bearer ")), timeout=300)
            except TimeoutError:
                raise SubmissionValidationError("Source validation timed out. The upload is staged; no review branch was created.") from None
            r = await client.get(f"/git/ref/heads/{base}")
            r.raise_for_status()
            if r.json()["object"]["sha"] != base_sha:
                raise SubmissionValidationError("The target branch changed during validation. Re-upload against the latest branch; no review branch was created.")

        # 2. Upload each file as a blob and assemble a tree on top of the base.
        tree_items: list[dict[str, str]] = []
        for path, content in files.items():
            r = await client.post(
                "/git/blobs",
                json={
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            )
            r.raise_for_status()
            tree_items.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": r.json()["sha"]}
            )
        r = await client.post(
            "/git/trees", json={"base_tree": base_tree, "tree": tree_items}
        )
        r.raise_for_status()
        tree_sha = r.json()["sha"]

        # 3. Commit and 4. create the branch ref.
        r = await client.post(
            "/git/commits",
            json={"message": message, "tree": tree_sha, "parents": [base_sha]},
        )
        r.raise_for_status()
        commit_sha = r.json()["sha"]

        r = await client.post(
            "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit_sha}
        )
        r.raise_for_status()

    return {"branch": branch, "commit": commit_sha, "compareUrl": compare_url(branch)}
