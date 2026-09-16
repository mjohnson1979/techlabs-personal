"""Build a history-free, allowlisted organization source archive. No cloud calls."""

from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


_SPEC = importlib.util.spec_from_file_location("portal_source", Path(__file__).with_name("portal_source.py"))
assert _SPEC is not None and _SPEC.loader is not None
_SOURCE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SOURCE)
SOURCE_FILES = _SOURCE.SOURCE_FILES
FORBIDDEN_CONTENT = re.compile(
    r"mcaps-microsoft|SME(?:&|&amp;)C|BusinessAgent|"
    r"8ad2703f-069c-4c1a-8f4f-850c2a4c2676|"
    r"0159c318-e7f6-4443-87c2-27b47c22a61c|"
    r"2929f0bd-f87b-47ae-bb73-ec22b65de557|"
    r"72f988bf-86f1-41af-91ab-2d7cd011db47|"
    r"[\w.+-]+@microsoft\.com|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})|"
    r"https?://[^\s\"']*/schemas/[^\s\"']*deploymentTemplate\.json",
    re.IGNORECASE,
)


def _read_source(root: Path, relative: str) -> bytes:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root):
        raise ValueError(f"Symbolic links are not allowed: {relative}")
    path.resolve().relative_to(root.resolve())
    content = path.read_bytes()
    if FORBIDDEN_CONTENT.search(content.decode("utf-8-sig")):
        raise ValueError(f"Release content check failed: {relative}")
    return content


def collect_files(root: Path) -> dict[str, bytes]:
    return _SOURCE.collect_files(root, reader=_read_source)


def build_archive(root: Path, output: Path) -> None:
    files = collect_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for relative, content in sorted(files.items()):
            archive.writestr(relative, content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build_archive(Path(__file__).resolve().parent.parent, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Organization export failed: {exc}\n")
    print(f"Organization source archive: {args.output}")


if __name__ == "__main__":
    main()