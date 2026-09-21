"""Exercise database durability across independent process lifetimes."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import db


def run_database_process(source: str) -> None:
    environment = dict(os.environ, PYTHONPATH=str(Path(db.__file__).parent))
    subprocess.run(
        [sys.executable, "-c", source], env=environment, check=True, timeout=20,
        capture_output=True, text=True,
    )


def test_database_uses_durable_rollback_journal():
    connection = db.init_db()
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_repository_setting_survives_process_exit_without_close():
    run_database_process("""
import db, os
db.set_setup_setting('github', {'enabled': True, 'owner': 'example',
                              'repository': 'portal', 'branch': 'main', 'seeded': True})
os._exit(0)
""")
    assert db.get_setup_setting("github") == {
        "enabled": True, "owner": "example", "repository": "portal",
        "branch": "main", "seeded": True,
    }
    assert db.init_db().execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_quiesced_wal_conversion_preserves_committed_settings_and_uploads():
    run_database_process("""
import db, json, os, sqlite3
connection = sqlite3.connect(os.environ['GALLERY_DB_PATH'])
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('PRAGMA wal_autocheckpoint=0')
connection.executescript(db.SCHEMA)
connection.execute('INSERT INTO setup_settings VALUES (?, ?)',
                   ('github', json.dumps({'enabled': True, 'repository': 'portal'})))
connection.execute('INSERT INTO submissions (id, slug, created_at) VALUES (?, ?, ?)',
                   ('staged-upload', 'example', 1.0))
connection.commit()
os._exit(0)
""")
    assert Path(os.environ["GALLERY_DB_PATH"] + "-wal").stat().st_size > 0
    connection = db.init_db()
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert db.get_setup_setting("github") == {"enabled": True, "repository": "portal"}
    assert connection.execute("SELECT status FROM submissions").fetchone()[0] == "pending"
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_overlapping_process_connections_preserve_each_others_settings():
    db.set_setup_setting("github", {"enabled": True, "repository": "portal"})
    run_database_process("""
import db, os
assert db.get_setup_setting('github')['repository'] == 'portal'
db.set_setup_setting('catalog_processing', {'mode': 'container'})
os._exit(0)
""")
    assert db.get_setup_setting("catalog_processing") == {"mode": "container"}
    db.set_setup_setting("github", {"enabled": True, "repository": "portal", "seeded": True})
    db.init_db().close()
    db._CONN = None
    assert db.get_setup_setting("github")["seeded"] is True
    assert db.get_setup_setting("catalog_processing") == {"mode": "container"}


def test_wal_conversion_fails_closed_with_active_reader():
    with sqlite3.connect(os.environ["GALLERY_DB_PATH"]) as reader:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.executescript(db.SCHEMA)
        reader.execute("INSERT INTO setup_settings VALUES (?, ?)", ("github", json.dumps({"enabled": True})))
        reader.commit()
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM setup_settings").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db.init_db()
        assert db._CONN is None
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        reader.rollback()
    reader.close()
    assert db.get_setup_setting("github") == {"enabled": True}