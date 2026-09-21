"""Generate and install organization-owned repository automation without cloud writes."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def workflow_files(labels: list[str] | None = None) -> dict[str, bytes]:
    source = Path(__file__).resolve().parent.parent / "site/server/catalog_workflows.py"
    spec = importlib.util.spec_from_file_location("catalog_workflows", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.workflow_files(labels)


def install(target: Path, labels: list[str] | None = None) -> None:
    files = workflow_files(labels)
    for name in ("portal_workflows.py", "portal_catalog.py"):
        files[f"tools/{name}"] = Path(__file__).with_name(name).read_bytes()
    files["site/server/catalog_workflows.py"] = (Path(__file__).resolve().parent.parent / "site/server/catalog_workflows.py").read_bytes()
    files["site/server/lifecycle.py"] = (Path(__file__).resolve().parent.parent / "site/server/lifecycle.py").read_bytes()
    for relative, content in files.items():
        path = target / relative
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Symbolic links are not allowed: {relative}")
        if path.exists() and (not path.is_file() or path.read_bytes() != content):
            raise ValueError(f"Existing file differs; review and merge it manually: {relative}")
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--runner-label", action="append")
    args = parser.parse_args()
    install(args.target.resolve(), args.runner_label)
    print("Workflow files installed locally. Review, commit and push; no setup or cloud changes were made.")