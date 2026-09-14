"""Validate and stage community use-case submissions (zip uploads).

A signed-in user uploads a ``.zip`` containing at least a Bicep template and its
parameter file(s). This module validates the archive (size, zip-slip / path
traversal, allowed file types, zip-bomb limits, required files), normalises it
into the ``catalog/<slug>/`` layout the gallery expects, synthesises a *draft*
``metadata.json`` / ``README.md`` when the submitter did not include them, and
stages the result on disk for a maintainer to review and commit.

The backend holds no GitHub credential, so it never pushes. An admin downloads
the normalised bundle from the admin panel (or, later, an Action wired to a
token opens the PR). See GitHub issue #6.
"""

from __future__ import annotations

import io
import json
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import db

# --------------------------------------------------------------------------- #
# Limits (defence against oversized uploads and zip bombs)
# --------------------------------------------------------------------------- #
MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # 5 MB compressed upload
MAX_TOTAL_UNCOMPRESSED = 25 * 1024 * 1024   # 25 MB once expanded
MAX_MEMBERS = 300                           # number of files in the archive
MAX_SINGLE_FILE = 5 * 1024 * 1024           # 5 MB per extracted file

# Only these extensions are accepted; anything else in the zip is rejected so we
# never stage scripts/binaries. Images are allowed for architecture diagrams.
ALLOWED_EXT = {".bicep", ".bicepparam", ".json", ".md", ".txt", ".png", ".jpg", ".jpeg"}

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "catalog"


class SubmissionError(ValueError):
    """Raised for any invalid submission; the message is safe to show the user."""


def _staging_root() -> Path:
    """Directory where staged submissions live (next to the SQLite DB, persistent
    on App Service ``/home`` and never web-served)."""
    root = db._default_db_path().parent / "submissions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:60]


def _safe_member_path(name: str) -> str | None:
    """Return a normalised relative POSIX path for a zip member, or None if the
    member is unsafe (absolute, drive-qualified, or escapes via ``..``)."""
    if not name or name.endswith("/"):
        return None  # directory entry — skip
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return None  # absolute or drive path
    parts = []
    for part in norm.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None  # path traversal
        parts.append(part)
    return "/".join(parts) if parts else None


def _strip_common_prefix(paths: list[str]) -> dict[str, str]:
    """Map original -> de-prefixed path, dropping a single shared wrapper folder
    (zips often expand into ``my-usecase/...``)."""
    tops = {p.split("/", 1)[0] for p in paths if "/" in p}
    roots = {p for p in paths if "/" not in p}
    if len(tops) == 1 and not roots:
        prefix = next(iter(tops)) + "/"
        return {p: p[len(prefix):] for p in paths}
    return {p: p for p in paths}


def _draft_metadata(slug: str, display_name: str, user_name: str, user_upn: str) -> dict[str, Any]:
    """Minimal draft metadata when the submitter did not include metadata.json."""
    return {
        "name": slug,
        "displayName": display_name or slug,
        "category": "Other",
        "description": "",
        "what": "",
        "why": "",
        "potentialUseCases": [],
        "industries": ["Cross-industry"],
        "compliance": ["Baseline"],
        "cost": {
            "currency": "USD",
            "region": "eastus",
            "profile": "Community submission - costs pending review",
            "components": [],
        },
        "submission": {
            "source": "gallery-zip-upload",
            "submittedBy": user_upn,
            "submittedByName": user_name,
        },
    }


def _draft_readme(slug: str, display_name: str, user_upn: str, has_metadata: bool) -> str:
    note = (
        "metadata.json was included by the submitter"
        if has_metadata
        else "metadata.json was generated as a draft (the submitter did not include one)"
    )
    return f"""# {display_name or slug}

> Community submission via zip upload (by {user_upn}). This is a **draft** pending
> maintainer review; {note}.

## Maintainer checklist

- [ ] Review `main.bicep` and the `parameters/*.bicepparam` files.
- [ ] Confirm / complete `metadata.json` (category, description, services, cost).
- [ ] Compile with your own toolchain and publish reviewed runtime inputs for catalog sync.
- [ ] Deploy once to confirm it provisions cleanly, then merge.
"""


def stage_submission(
    *,
    data: bytes,
    display_name: str,
    user_oid: str,
    user_upn: str,
    user_name: str,
) -> dict[str, Any]:
    """Validate an uploaded zip and stage a normalised catalog entry.

    Returns a dict: ``{id, slug, displayName, path, files}``.
    Raises ``SubmissionError`` with a user-safe message on any invalid input.
    """
    if not data:
        raise SubmissionError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SubmissionError(
            f"The zip is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SubmissionError("That is not a valid .zip archive.") from exc

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise SubmissionError("The archive contains too many files.")
    total = sum(i.file_size for i in infos)
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise SubmissionError("The archive expands to too much data.")

    # Map safe original member paths -> their ZipInfo.
    safe: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        rel = _safe_member_path(info.filename)
        if rel is None:
            raise SubmissionError(f"Unsafe path in archive: {info.filename!r}")
        if info.file_size > MAX_SINGLE_FILE:
            raise SubmissionError(f"File too large in archive: {rel}")
        safe[rel] = info

    if not safe:
        raise SubmissionError("The archive has no files.")

    # Drop a single common wrapper folder, then keep only allowed file types.
    mapped = _strip_common_prefix(list(safe.keys()))
    files: dict[str, bytes] = {}
    for original, target in mapped.items():
        ext = Path(target).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise SubmissionError(
                f"File type not allowed: {target} (allowed: "
                + ", ".join(sorted(ALLOWED_EXT)) + ")"
            )
        files[target] = zf.read(safe[original])

    # Required: a main.bicep at the root, plus at least one parameters file.
    if "main.bicep" not in files:
        raise SubmissionError("The archive must contain a main.bicep at its root.")
    param_files = [
        p for p in files
        if p.startswith("parameters/") and p.lower().endswith(".bicepparam")
    ]
    if not param_files:
        raise SubmissionError(
            "Include at least one parameters/<env>.bicepparam file (e.g. parameters/dev.bicepparam)."
        )

    # Validate metadata.json if present.
    has_metadata = "metadata.json" in files
    if has_metadata:
        try:
            json.loads(files["metadata.json"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SubmissionError("metadata.json is not valid JSON.") from exc

    # Derive a slug. Prefer the display name; fall back to metadata.name.
    slug = _sanitize_slug(display_name)
    if not slug and has_metadata:
        try:
            slug = _sanitize_slug(json.loads(files["metadata.json"]).get("name", ""))
        except ValueError:
            slug = ""
    if not slug:
        slug = "community-use-case"
    slug = _unique_slug(slug)

    # Synthesise drafts the submitter omitted.
    if not has_metadata:
        files["metadata.json"] = (
            json.dumps(
                _draft_metadata(slug, display_name, user_name, user_upn), indent=2
            ) + "\n"
        ).encode("utf-8")
    if "README.md" not in files:
        files["README.md"] = _draft_readme(
            slug, display_name, user_upn, has_metadata
        ).encode("utf-8")

    # Write the normalised bundle into the staging area.
    submission_id = secrets.token_hex(8)
    dest_root = _staging_root() / submission_id
    catalog_dir = dest_root / "catalog" / slug
    written: list[str] = []
    for rel, content in sorted(files.items()):
        out = catalog_dir / rel
        # Final defence-in-depth: ensure the resolved path stays inside dest.
        if not str(out.resolve()).startswith(str(catalog_dir.resolve())):
            shutil.rmtree(dest_root, ignore_errors=True)
            raise SubmissionError(f"Unsafe path: {rel}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        written.append(f"catalog/{slug}/{rel}")

    return {
        "id": submission_id,
        "slug": slug,
        "displayName": display_name or slug,
        "path": str(dest_root),
        "files": written,
        "hasMetadata": has_metadata,
    }


def _unique_slug(slug: str) -> str:
    """Avoid clobbering an existing catalog entry or a pending submission."""
    def taken(s: str) -> bool:
        return (CATALOG_DIR / s).exists() or db.slug_exists(s)

    if not taken(slug):
        return slug
    i = 2
    while taken(f"{slug}-{i}"):
        i += 1
    return f"{slug}-{i}"


def staged_files(submission: dict[str, Any]) -> dict[str, bytes]:
    """Read the staged catalog/<slug>/ files as {repo_relative_path: bytes}.

    Paths are repository-relative (e.g. ``catalog/slug/main.bicep``) so they can
    be committed straight into a branch by github_intake.push_submission.
    """
    root = Path(submission["path"])
    catalog_dir = root / "catalog"
    if not catalog_dir.is_dir():
        raise SubmissionError("Staged files are no longer available.")
    out: dict[str, bytes] = {}
    for path in sorted(catalog_dir.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def bundle_zip(submission: dict[str, Any]) -> bytes:
    """Zip the staged catalog/<slug>/ folder for an admin to download."""
    root = Path(submission["path"])
    catalog_dir = root / "catalog"
    if not catalog_dir.is_dir():
        raise SubmissionError("Staged files are no longer available.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(catalog_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(root).as_posix()))
    return buf.getvalue()


def discard(submission: dict[str, Any]) -> None:
    """Remove a staged submission's files from disk (best effort)."""
    path = submission.get("path")
    if path:
        shutil.rmtree(path, ignore_errors=True)
