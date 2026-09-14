"""Keep test state isolated from customer installations and workstation data."""

import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import db
import distribution


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "internal")
    monkeypatch.setenv("GALLERY_DB_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setattr(distribution, "CUSTOMER_MARKER", tmp_path / "not-packaged.json")
    monkeypatch.setattr(db, "_CONN", None)
    yield
    if db._CONN is not None:
        db._CONN.close()