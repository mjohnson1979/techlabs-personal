"""Assemble the organization runtime and source seed without local state or history."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE_FILES = (
    "LICENSE", "Dockerfile", ".dockerignore", "tools/portal_source.py",
    "tools/preview_portal.py", "tools/portal_workflows.py", "tools/portal_catalog.py",
    "tools/install_processing_tools.py",
    "tools/Deploy-Portal.ps1", "tools/Initialize-Portal.ps1", "tools/Reset-Portal.ps1",
    "tools/Portal-Foundation.ps1",
    "site/main.py", "site/requirements.txt",
    "site/index.html", "site/install.html", "site/assets/install.js",
    "site/assets/install.css", "site/assets/account.js", "site/assets/admin.js",
    "site/assets/api.js", "site/assets/app.js", "site/assets/panel.js",
    "site/assets/setup.js", "site/assets/styles.css", "site/assets/user-menu.js",
    "site/server/.env.example", "site/server/api.py", "site/server/app.py",
    "site/server/azure_deploy.py", "site/server/db.py", "site/server/distribution.py",
    "site/server/container_start.py", "site/server/setup_config.py",
    "site/server/setup_cloud.py", "site/server/setup_routes.py",
    "site/server/setup_github.py", "site/server/setup_auth.py",
    "site/server/catalog_processing.py",
    "site/server/catalog_workflows.py",
    "site/server/lifecycle.py",
    "site/server/entra_auth.py", "site/server/github_intake.py",
    "site/server/github_device.py", "site/server/identity.py", "site/server/installer.py",
    "site/server/pricing.py", "site/server/scheduler.py", "site/server/submissions.py",
    "site/server/terraform_bootstrap.py", "site/server/terraform_exec.py",
)


def read_source(root: Path, relative: str) -> bytes:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root):
        raise ValueError(f"Symbolic links are not allowed: {relative}")
    path.resolve().relative_to(root.resolve())
    return path.read_bytes()


def collect_files(root: Path, reader: Callable[[Path, str], bytes] = read_source) -> dict[str, bytes]:
    files = {relative: reader(root, relative) for relative in SOURCE_FILES}
    guide = "docs/OPERATIONS.md" if (root / "docs/OPERATIONS.md").is_file() else "README.md"
    files["README.md"] = reader(root, guide)
    snapshots = {
        "site/catalog.json": {"items": []},
        "site/capacity.json": {"regions": [], "services": {}},
        "site/updates.json": {"items": []},
        "site/registry.json": {"regions": [], "models": {}, "skus": {}},
        "site/distribution.json": {"distribution": "organization"},
    }
    for relative, data in snapshots.items():
        files[relative] = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    files[".gitignore"] = (
        ".portal/\n.env\n.env.*\n!.env.example\n.venv/\n__pycache__/\n*.pyc\n"
        "*.db\n*.db-*\n*.log\nsite/server/data/\nsite/server/_tfwork/\n"
        "*.tfstate*\n.terraform/\n*.tfplan\nruns/\n"
    ).encode("utf-8")
    seed = {
        "files": {name: base64.b64encode(content).decode("ascii") for name, content in files.items()},
        "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files["site/server/platform-source.json"] = json.dumps(seed).encode("utf-8")
    manifest = {
        "distribution": "organization",
        "releaseStatus": "engineering-preview-requires-release-approval",
        "files": {relative: hashlib.sha256(content).hexdigest() for relative, content in sorted(files.items())},
    }
    files["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = collect_files(Path(__file__).resolve().parent.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for relative, content in sorted(files.items()):
            archive.writestr(relative, content)


if __name__ == "__main__":
    main()