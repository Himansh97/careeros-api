"""Outreach records, follow-up scheduling, and saved searches.

Follow-ups are derived, never guessed: one is due only when outreach was
actually marked sent and no reply has been recorded. Marking a reply cancels
the follow-up, because chasing someone who already answered is the fastest
way to burn a contact.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import connect, now

OUTREACH_SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    contact_id TEXT,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    email_subject TEXT,
    email_draft TEXT,
    linkedin_draft TEXT,
    sent_at TEXT,
    replied_at TEXT,
    followup_due_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_searches (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    filters TEXT NOT NULL,
    auto_rerun INTEGER DEFAULT 0,
    last_run_at TEXT,
    created_at TEXT NOT NULL
);
"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(OUTREACH_SCHEMA)


def _business_days_from(start: datetime, days: int) -> datetime:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:  # skip weekends
            added += 1
    return d


# ---------------------------------------------------------------- outreach
def upsert_outreach(payload: dict[str, Any]) -> dict[str, Any]:
    oid = f"o_{payload['jobId']}"
    with connect() as conn:
        _ensure(conn)
        existing = conn.execute("SELECT id FROM outreach WHERE id=?", (oid,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE outreach SET email_subject=?, email_draft=?, linkedin_draft=?,
                   contact_id=? WHERE id=?""",
                (
                    payload.get("emailSubject"),
                    payload.get("emailDraft"),
                    payload.get("linkedinDraft"),
                    payload.get("contactId"),
                    oid,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO outreach
                   (id, job_id, contact_id, company, job_title, channel, status,
                    email_subject, email_draft, linkedin_draft, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    oid,
                    payload["jobId"],
                    payload.get("contactId"),
                    payload["company"],
                    payload["jobTitle"],
                    payload.get("channel", "email"),
                    "drafted",
                    payload.get("emailSubject"),
                    payload.get("emailDraft"),
                    payload.get("linkedinDraft"),
                    now(),
                ),
            )
    return get_outreach(oid) or {}


def mark_sent(oid: str, followup_business_days: int = 6) -> dict[str, Any] | None:
    ts = datetime.now(timezone.utc)
    due = _business_days_from(ts, followup_business_days)
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET status='sent', sent_at=?, followup_due_at=? WHERE id=?",
            (ts.isoformat(), due.isoformat(), oid),
        )
    return get_outreach(oid)


def unmark_replied(oid: str, followup_business_days: int = 6) -> dict[str, Any] | None:
    """Undo "they replied" — the one status change that silently loses work.

    Marking a thread replied cancels its follow-up by clearing
    `followup_due_at`. A mis-click therefore does not just set a wrong label:
    it removes the reminder to chase an employer who never actually answered,
    and nothing surfaces that omission afterwards.

    The restored due date is computed from `sent_at`, not from now. The clock
    started when the email went out, and restarting it here would quietly grant
    the thread another six business days of silence.
    """
    record = get_outreach(oid)
    if not record:
        return None

    sent_at = record.get("sentAt")
    due = None
    if sent_at:
        try:
            start = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        except ValueError:
            start = datetime.now(timezone.utc)
        due = _business_days_from(start, followup_business_days).isoformat()

    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET status=?, replied_at=NULL, followup_due_at=? WHERE id=?",
            ("sent" if sent_at else "drafted", due, oid),
        )
    return get_outreach(oid)


def mark_replied(oid: str) -> dict[str, Any] | None:
    with connect() as conn:
        _ensure(conn)
        # Clearing followup_due_at is the cancellation — a replied thread must
        # never surface as a pending follow-up.
        conn.execute(
            "UPDATE outreach SET status='replied', replied_at=?, followup_due_at=NULL WHERE id=?",
            (now(), oid),
        )
    return get_outreach(oid)


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "jobId": r["job_id"],
        "contactId": r["contact_id"],
        "company": r["company"],
        "jobTitle": r["job_title"],
        "channel": r["channel"],
        "status": r["status"],
        "emailSubject": r["email_subject"],
        "emailDraft": r["email_draft"],
        "linkedinDraft": r["linkedin_draft"],
        "sentAt": r["sent_at"],
        "repliedAt": r["replied_at"],
        "followUpDueAt": r["followup_due_at"],
        "createdAt": r["created_at"],
    }


def get_outreach(oid: str) -> dict[str, Any] | None:
    with connect() as conn:
        _ensure(conn)
        r = conn.execute("SELECT * FROM outreach WHERE id=?", (oid,)).fetchone()
    return _row(r) if r else None


def list_outreach() -> list[dict[str, Any]]:
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT * FROM outreach ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def list_followups() -> list[dict[str, Any]]:
    """Only sent-and-unanswered outreach is ever a follow-up."""
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            """SELECT * FROM outreach
               WHERE status='sent' AND followup_due_at IS NOT NULL AND replied_at IS NULL
               ORDER BY followup_due_at"""
        ).fetchall()
    out = []
    today = datetime.now(timezone.utc)
    for r in rows:
        rec = _row(r)
        try:
            rec["overdue"] = datetime.fromisoformat(rec["followUpDueAt"]) < today
        except (TypeError, ValueError):
            rec["overdue"] = False
        out.append(rec)
    return out


# ---------------------------------------------------------- saved searches
def save_search(label: str, filters: dict[str, Any]) -> dict[str, Any]:
    sid = f"s_{abs(hash((label, json.dumps(filters, sort_keys=True)))) % 10**10}"
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            """INSERT OR REPLACE INTO saved_searches
               (id, label, filters, auto_rerun, created_at) VALUES (?,?,?,?,?)""",
            (sid, label, json.dumps(filters), 0, now()),
        )
    return {"id": sid, "label": label, "filters": filters, "autoRerun": False, "lastRunAt": None}


def list_searches() -> list[dict[str, Any]]:
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT * FROM saved_searches ORDER BY created_at DESC").fetchall()
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "filters": json.loads(r["filters"]),
            "autoRerun": bool(r["auto_rerun"]),
            "lastRunAt": r["last_run_at"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def delete_search(sid: str) -> None:
    with connect() as conn:
        _ensure(conn)
        conn.execute("DELETE FROM saved_searches WHERE id=?", (sid,))


def toggle_search(sid: str) -> None:
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE saved_searches SET auto_rerun = 1 - auto_rerun WHERE id=?", (sid,)
        )
