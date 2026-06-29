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

import base64
import os
from typing import Any

import httpx

API_ROOT = "https://api.github.com"


def _token() -> str:
    return os.environ.get("GALLERY_GITHUB_TOKEN", "").strip()


def _repo() -> str:
    # owner/repo the branches are created in.
    return os.environ.get("GALLERY_GITHUB_REPO", "mcaps-microsoft/TechLabs").strip()


def _base() -> str:
    return os.environ.get("GALLERY_GITHUB_BASE", "main").strip() or "main"


def configured() -> bool:
    """True when a token is present so the backend can push a branch."""
    return bool(_token()) and bool(_repo())


def compare_url(branch: str) -> str:
    return f"https://github.com/{_repo()}/compare/{_base()}...{branch}?expand=1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "techlabs-gallery-intake",
    }


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

    repo = _repo()
    base = _base()
    async with httpx.AsyncClient(
        base_url=f"{API_ROOT}/repos/{repo}", headers=_headers(), timeout=30.0
    ) as client:
        # 1. Resolve the base branch tip and its tree.
        r = await client.get(f"/git/ref/heads/{base}")
        r.raise_for_status()
        base_sha = r.json()["object"]["sha"]
        r = await client.get(f"/git/commits/{base_sha}")
        r.raise_for_status()
        base_tree = r.json()["tree"]["sha"]

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
