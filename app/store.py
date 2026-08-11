"""SQLite persistence for applications, approvals, and activity."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    source TEXT,
    status TEXT NOT NULL,
    raw_fit_score INTEGER,
    resume_score INTEGER,
    apply_url TEXT,
    next_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    label TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    job_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_flags (
    job_id TEXT PRIMARY KEY,
    saved INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_timeline(conn: sqlite3.Connection, app_id: str, label: str) -> None:
    conn.execute(
        "INSERT INTO timeline (application_id, label, at) VALUES (?,?,?)",
        (app_id, label, now()),
    )


def upsert_application(job: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    app_id = f"app_{job['id']}"
    ts = now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM applications WHERE id=?", (app_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE applications SET raw_fit_score=?, updated_at=? WHERE id=?",
                (score["rawFitScore"], ts, app_id),
            )
        else:
            conn.execute(
                """INSERT INTO applications
                   (id, job_id, title, company, location, source, status,
                    raw_fit_score, resume_score, apply_url, next_action,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    app_id,
                    job["id"],
                    job["title"],
                    job["company"]["name"],
                    job.get("location"),
                    job.get("source"),
                    "qualified",
                    score["rawFitScore"],
                    None,
                    job.get("applyUrl"),
                    "Tailor resume",
                    ts,
                    ts,
                ),
            )
            add_timeline(conn, app_id, f"Discovered via {job.get('source')}")
            add_timeline(conn, app_id, f"Fit scored {score['rawFitScore']}")
    return get_application(app_id)


# Statuses that mean the candidate has already acted. Re-tailoring must not
# walk any of these backwards.
_COMMITTED_STATUSES = ("applied", "interviewing", "offer", "rejected", "withdrawn")


def set_resume_score(app_id: str, resume_score: int) -> None:
    """Record a resume score, without rewinding an application already sent.

    This unconditionally forced status back to "ready", so re-running /tailor
    on a job the candidate had already applied to silently erased that fact —
    the one piece of state in the pipeline they cannot reconstruct. A score
    refresh is not evidence that an application was un-sent.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?", (app_id,)
        ).fetchone()
        committed = row and (row["status"] or "") in _COMMITTED_STATUSES

        if committed:
            conn.execute(
                "UPDATE applications SET resume_score=?, updated_at=? WHERE id=?",
                (resume_score, now(), app_id),
            )
        else:
            conn.execute(
                "UPDATE applications SET resume_score=?, status=?, next_action=?, "
                "updated_at=? WHERE id=?",
                (resume_score, "ready", "Review and approve", now(), app_id),
            )
        add_timeline(conn, app_id, f"Resume tailored — score {resume_score}")


def advance(app_id: str, status: str, note: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE applications SET status=?, updated_at=? WHERE id=?",
            (status, now(), app_id),
        )
        add_timeline(conn, app_id, note)


def _row_to_app(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    events = conn.execute(
        "SELECT label, at FROM timeline WHERE application_id=? ORDER BY id", (row["id"],)
    ).fetchall()
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "title": row["title"],
        "company": {"id": row["company"].lower(), "name": row["company"]},
        "location": row["location"],
        "source": row["source"],
        "status": row["status"],
        "rawFitScore": row["raw_fit_score"],
        "resumeScore": row["resume_score"],
        "applyUrl": row["apply_url"],
        "nextAction": row["next_action"],
        "submittedAt": None,
        "timeline": [
            {"id": f"t{i}", "label": e["label"], "timestamp": e["at"]}
            for i, e in enumerate(events)
        ],
    }


def list_applications() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM applications ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_app(conn, r) for r in rows]


def get_application(app_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return _row_to_app(conn, row) if row else None


def add_approval(kind: str, job_id: str, payload: dict[str, Any]) -> str:
    approval_id = f"appr_{kind}_{job_id}"
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO approvals
               (id, kind, job_id, payload, status, created_at) VALUES (?,?,?,?,?,?)""",
            (approval_id, kind, job_id, json.dumps(payload), "pending", now()),
        )
    return approval_id


def list_approvals() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "jobId": r["job_id"],
            "status": r["status"],
            "createdAt": r["created_at"],
            **json.loads(r["payload"]),
        }
        for r in rows
    ]


def resolve_approval(approval_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE approvals SET status=? WHERE id=?", (status, approval_id)
        )


def job_flags() -> dict[str, dict[str, bool]]:
    """Every saved/dismissed flag, keyed by job id.

    One query for the whole search rather than a lookup per job, matching how
    `list_applications` is already used in the search handler.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT job_id, saved, dismissed FROM job_flags"
        ).fetchall()
    return {
        r["job_id"]: {"saved": bool(r["saved"]), "dismissed": bool(r["dismissed"])}
        for r in rows
    }


def set_job_flag(
    job_id: str, *, saved: bool | None = None, dismissed: bool | None = None
) -> dict[str, bool]:
    """Set one or both flags for a job and return the resulting state.

    Jobs are not stored locally — most come from a source API and exist only in
    the fetch cache — so this keys on the job id alone rather than a foreign
    key. A flag for a posting that has since vanished from its board is simply
    never read.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT saved, dismissed FROM job_flags WHERE job_id=?", (job_id,)
        ).fetchone()
        current = {
            "saved": bool(row["saved"]) if row else False,
            "dismissed": bool(row["dismissed"]) if row else False,
        }
        if saved is not None:
            current["saved"] = saved
        if dismissed is not None:
            current["dismissed"] = dismissed
        conn.execute(
            "INSERT INTO job_flags (job_id, saved, dismissed, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "saved=excluded.saved, dismissed=excluded.dismissed, "
            "updated_at=excluded.updated_at",
            (job_id, int(current["saved"]), int(current["dismissed"]), now()),
        )
    return current
