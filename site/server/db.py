"""SQLite persistence for the Techlabs gallery backend.

Stores telemetry events, deployment records, and user roles. A single SQLite
file is the system of record; the backend is both the control plane and the
executor, deploying Azure resources directly with its managed identity (see
azure_deploy.py).

The database lives on App Service's persistent ``/home`` share by default, so
it survives restarts even though the OAuth session store is in-memory. Override
the location with ``GALLERY_DB_PATH`` (useful for local dev / tests).

Access is synchronous sqlite3 guarded by a process-wide lock. The web app runs
gunicorn with a single worker (sessions are in-memory), so a single writer is
sufficient and avoids cross-process contention.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _default_db_path() -> Path:
    """Pick a sensible DB path: explicit env, App Service /home, else local."""
    env_path = os.environ.get("GALLERY_DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    home = Path("/home")
    if home.is_dir() and os.access(home, os.W_OK):
        return home / "data" / "techlabs.db"
    # Local dev / tests: keep it next to the server package.
    return Path(__file__).resolve().parent / "data" / "techlabs.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    oid         TEXT PRIMARY KEY,
    upn         TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'guest',  -- 'admin' | 'guest'
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    id              TEXT PRIMARY KEY,
    use_case        TEXT NOT NULL,
    environment     TEXT NOT NULL,
    user_oid        TEXT NOT NULL,
    user_upn        TEXT NOT NULL,
    user_name       TEXT NOT NULL DEFAULT '',
    subscription_id TEXT NOT NULL DEFAULT '',
    resource_group  TEXT NOT NULL,
    status          TEXT NOT NULL,  -- requested|deploying|active|expiring|destroying|destroyed|failed
    engine          TEXT NOT NULL DEFAULT 'bicep',  -- bicep | terraform
    created_at      REAL NOT NULL,
    end_at          REAL NOT NULL,
    destroyed_at    REAL,
    gh_run_url      TEXT NOT NULL DEFAULT '',
    detail          TEXT NOT NULL DEFAULT '',
    params_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_deploy_user ON deployments(user_oid);
CREATE INDEX IF NOT EXISTS idx_deploy_status ON deployments(status);
CREATE INDEX IF NOT EXISTS idx_deploy_end ON deployments(end_at);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    type           TEXT NOT NULL,  -- view|deploy_requested|deploy_succeeded|... |signin
    user_oid       TEXT NOT NULL DEFAULT '',
    user_upn       TEXT NOT NULL DEFAULT '',
    use_case       TEXT NOT NULL DEFAULT '',
    deployment_id  TEXT NOT NULL DEFAULT '',
    meta_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_oid);

CREATE TABLE IF NOT EXISTS submissions (
    id           TEXT PRIMARY KEY,
    slug         TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    user_oid     TEXT NOT NULL DEFAULT '',
    user_upn     TEXT NOT NULL DEFAULT '',
    user_name    TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|branched|discarded|merged
    created_at   REAL NOT NULL,
    path         TEXT NOT NULL DEFAULT '',
    files_json   TEXT NOT NULL DEFAULT '[]',
    branch       TEXT NOT NULL DEFAULT '',
    compare_url  TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_subs_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_subs_created ON submissions(created_at);
"""


def init_db() -> sqlite3.Connection:
    """Open (once) and initialise the database."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        path = _default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _CONN = conn
    return _CONN


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight additive migrations for columns added after first release."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)").fetchall()}
    for col in ("branch", "compare_url"):
        if col not in cols:
            conn.execute(
                f"ALTER TABLE submissions ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
    dep_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deployments)").fetchall()}
    if "engine" not in dep_cols:
        conn.execute(
            "ALTER TABLE deployments ADD COLUMN engine TEXT NOT NULL DEFAULT 'bicep'"
        )


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        return init_db()
    return _CONN


def has_admin() -> bool:
    """True if at least one admin exists (real or placeholder)."""
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
    return row is not None


# --------------------------------------------------------------------------- #
# Users / roles
# --------------------------------------------------------------------------- #
def upsert_user(oid: str, upn: str, name: str) -> str:
    """Record a sign-in and return the user's role.

    Reconciles any admin placeholder row (keyed by ``upn:<upn>``, created when an
    admin pre-assigns a role to someone who hasn't signed in yet) into a real row
    keyed by the user's oid. Bootstrap: the very first person to ever sign in,
    when no admin exists yet, becomes the admin.
    """
    now = time.time()
    upn_l = (upn or "").strip().lower()
    with _LOCK:
        conn = _conn()
        placeholder = conn.execute(
            "SELECT role FROM users WHERE oid = ?", (f"upn:{upn_l}",)
        ).fetchone()
        existing = conn.execute(
            "SELECT role FROM users WHERE oid = ?", (oid,)
        ).fetchone()
        role = "guest"
        if existing is not None:
            role = existing["role"]
        if placeholder is not None:
            role = placeholder["role"] or role
            conn.execute("DELETE FROM users WHERE oid = ?", (f"upn:{upn_l}",))
        if role != "admin":
            admins = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            if admins is None:
                role = "admin"
        if existing is None:
            conn.execute(
                "INSERT INTO users(oid, upn, name, role, created_at, last_seen)"
                " VALUES(?,?,?,?,?,?)",
                (oid, upn, name, role, now, now),
            )
        else:
            conn.execute(
                "UPDATE users SET upn=?, name=?, role=?, last_seen=? WHERE oid=?",
                (upn, name, role, now, oid),
            )
        conn.commit()
        return role


def get_role(oid: str, upn: str = "") -> str:
    with _LOCK:
        conn = _conn()
        row = conn.execute("SELECT role FROM users WHERE oid = ?", (oid,)).fetchone()
        if row is not None:
            return row["role"]
        if upn:
            ph = conn.execute(
                "SELECT role FROM users WHERE oid = ?", (f"upn:{upn.strip().lower()}",)
            ).fetchone()
            if ph is not None:
                return ph["role"]
    return "guest"


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT oid, upn, name, role, created_at, last_seen FROM users"
            " ORDER BY role DESC, lower(upn)"
        ).fetchall()
    return [dict(r) for r in rows]


def set_user_role(upn: str, role: str) -> None:
    """Set a user's role by UPN, creating a placeholder if they've never signed in."""
    if role not in ("admin", "guest"):
        raise ValueError("role must be 'admin' or 'guest'")
    now = time.time()
    upn_l = upn.strip().lower()
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT oid FROM users WHERE lower(upn) = ?", (upn_l,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users(oid, upn, name, role, created_at, last_seen)"
                " VALUES(?,?,?,?,?,?)",
                (f"upn:{upn_l}", upn, "", role, now, now),
            )
        else:
            conn.execute(
                "UPDATE users SET role=? WHERE lower(upn) = ?", (role, upn_l)
            )
        conn.commit()


# --------------------------------------------------------------------------- #
# Deployments
# --------------------------------------------------------------------------- #
@dataclass
class DeploymentRow:
    id: str
    use_case: str
    environment: str
    user_oid: str
    user_upn: str
    user_name: str
    subscription_id: str
    resource_group: str
    status: str
    created_at: float
    end_at: float
    destroyed_at: float | None
    gh_run_url: str
    detail: str
    params: dict[str, Any]


def _row_to_deployment(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    try:
        d["params"] = json.loads(d.pop("params_json", "{}") or "{}")
    except (ValueError, TypeError):
        d["params"] = {}
    return d


def create_deployment(
    *,
    deployment_id: str,
    use_case: str,
    environment: str,
    user_oid: str,
    user_upn: str,
    user_name: str,
    subscription_id: str,
    resource_group: str,
    end_at: float,
    engine: str = "bicep",
    params: dict[str, Any] | None = None,
) -> None:
    now = time.time()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO deployments(id, use_case, environment, user_oid, user_upn,"
            " user_name, subscription_id, resource_group, status, engine, created_at, end_at,"
            " destroyed_at, gh_run_url, detail, params_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                deployment_id,
                use_case,
                environment,
                user_oid,
                user_upn,
                user_name,
                subscription_id,
                resource_group,
                "requested",
                engine,
                now,
                end_at,
                None,
                "",
                "",
                json.dumps(params or {}),
            ),
        )
        conn.commit()


def update_deployment(
    deployment_id: str,
    *,
    status: str | None = None,
    gh_run_url: str | None = None,
    detail: str | None = None,
    destroyed_at: float | None = None,
    end_at: float | None = None,
) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if status is not None:
        sets.append("status=?")
        args.append(status)
    if gh_run_url is not None:
        sets.append("gh_run_url=?")
        args.append(gh_run_url)
    if detail is not None:
        sets.append("detail=?")
        args.append(detail)
    if destroyed_at is not None:
        sets.append("destroyed_at=?")
        args.append(destroyed_at)
    if end_at is not None:
        sets.append("end_at=?")
        args.append(end_at)
    if not sets:
        return
    args.append(deployment_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            f"UPDATE deployments SET {', '.join(sets)} WHERE id = ?", tuple(args)
        )
        conn.commit()


def get_deployment(deployment_id: str) -> dict[str, Any] | None:
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM deployments WHERE id = ?", (deployment_id,)
        ).fetchone()
    return _row_to_deployment(row) if row is not None else None


def list_deployments(
    *, user_oid: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _conn()
        if user_oid is not None:
            rows = conn.execute(
                "SELECT * FROM deployments WHERE user_oid = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (user_oid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deployments ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_deployment(r) for r in rows]


def due_deployments(now: float | None = None) -> list[dict[str, Any]]:
    """Active deployments whose end_at has passed — ready for teardown."""
    now = time.time() if now is None else now
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM deployments WHERE status IN ('requested','deploying','active')"
            " AND end_at <= ? ORDER BY end_at ASC",
            (now,),
        ).fetchall()
    return [_row_to_deployment(r) for r in rows]


def deployments_by_status(status: str, limit: int = 200) -> list[dict[str, Any]]:
    """Return recent deployments with the requested lifecycle status."""
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM deployments WHERE status = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [_row_to_deployment(r) for r in rows]


# --------------------------------------------------------------------------- #
# Telemetry events
# --------------------------------------------------------------------------- #
def record_event(
    *,
    type: str,
    user_oid: str = "",
    user_upn: str = "",
    use_case: str = "",
    deployment_id: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO events(ts, type, user_oid, user_upn, use_case, deployment_id,"
            " meta_json) VALUES(?,?,?,?,?,?,?)",
            (
                time.time(),
                type,
                user_oid,
                user_upn,
                use_case,
                deployment_id,
                json.dumps(meta or {}),
            ),
        )
        conn.commit()


def telemetry_summary(days: int = 30) -> dict[str, Any]:
    """Aggregate counts for the admin dashboard."""
    since = time.time() - days * 86400
    with _LOCK:
        conn = _conn()
        by_type = {
            r["type"]: r["n"]
            for r in conn.execute(
                "SELECT type, COUNT(*) n FROM events WHERE ts >= ? GROUP BY type",
                (since,),
            ).fetchall()
        }
        top_views = [
            dict(r)
            for r in conn.execute(
                "SELECT use_case, COUNT(*) n FROM events"
                " WHERE type='view' AND ts >= ? AND use_case <> ''"
                " GROUP BY use_case ORDER BY n DESC LIMIT 10",
                (since,),
            ).fetchall()
        ]
        top_deploys = [
            dict(r)
            for r in conn.execute(
                "SELECT use_case, COUNT(*) n FROM deployments"
                " WHERE created_at >= ? GROUP BY use_case ORDER BY n DESC LIMIT 10",
                (since,),
            ).fetchall()
        ]
        top_users = [
            dict(r)
            for r in conn.execute(
                "SELECT user_upn, COUNT(*) n FROM deployments"
                " WHERE created_at >= ? GROUP BY user_upn ORDER BY n DESC LIMIT 10",
                (since,),
            ).fetchall()
        ]
        status_counts = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) n FROM deployments GROUP BY status"
            ).fetchall()
        }
        # Deployments per day (last `days`).
        per_day = [
            dict(r)
            for r in conn.execute(
                "SELECT date(created_at,'unixepoch') d, COUNT(*) n FROM deployments"
                " WHERE created_at >= ? GROUP BY d ORDER BY d",
                (since,),
            ).fetchall()
        ]
        active = conn.execute(
            "SELECT COUNT(*) n FROM deployments WHERE status IN"
            " ('requested','deploying','active')"
        ).fetchone()["n"]
        unique_users = conn.execute(
            "SELECT COUNT(DISTINCT user_oid) n FROM events WHERE ts >= ?", (since,)
        ).fetchone()["n"]
    return {
        "days": days,
        "byType": by_type,
        "topViews": top_views,
        "topDeploys": top_deploys,
        "topUsers": top_users,
        "statusCounts": status_counts,
        "perDay": per_day,
        "activeDeployments": active,
        "uniqueUsers": unique_users,
    }


# --------------------------------------------------------------------------- #
# Use-case submissions (zip uploads staged for maintainer review)
# --------------------------------------------------------------------------- #
def _row_to_submission(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    try:
        d["files"] = json.loads(d.pop("files_json", "[]") or "[]")
    except (ValueError, TypeError):
        d["files"] = []
    return d


def create_submission(
    *,
    submission_id: str,
    slug: str,
    display_name: str,
    user_oid: str,
    user_upn: str,
    user_name: str,
    path: str,
    files: list[str],
    detail: str = "",
) -> None:
    now = time.time()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO submissions(id, slug, display_name, user_oid, user_upn,"
            " user_name, status, created_at, path, files_json, detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                submission_id,
                slug,
                display_name,
                user_oid,
                user_upn,
                user_name,
                "pending",
                now,
                path,
                json.dumps(files or []),
                detail,
            ),
        )
        conn.commit()


def list_submissions(
    *, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _conn()
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM submissions WHERE status = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM submissions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_submission(r) for r in rows]


def get_submission(submission_id: str) -> dict[str, Any] | None:
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
    return _row_to_submission(row) if row is not None else None


def update_submission(
    submission_id: str,
    *,
    status: str | None = None,
    detail: str | None = None,
    branch: str | None = None,
    compare_url: str | None = None,
) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if status is not None:
        sets.append("status=?")
        args.append(status)
    if detail is not None:
        sets.append("detail=?")
        args.append(detail)
    if branch is not None:
        sets.append("branch=?")
        args.append(branch)
    if compare_url is not None:
        sets.append("compare_url=?")
        args.append(compare_url)
    if not sets:
        return
    args.append(submission_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            f"UPDATE submissions SET {', '.join(sets)} WHERE id = ?", tuple(args)
        )
        conn.commit()


def slug_exists(slug: str) -> bool:
    """True if an active (non-discarded) submission already claims this slug."""
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE slug = ? AND status <> 'discarded' LIMIT 1",
            (slug,),
        ).fetchone()
    return row is not None

