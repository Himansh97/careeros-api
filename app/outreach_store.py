"""Outreach records, follow-up scheduling, and saved searches.

Follow-ups are derived, never guessed: one is due only when outreach was
actually marked sent and no reply has been recorded. Marking a reply cancels
the follow-up, because chasing someone who already answered is the fastest
way to burn a contact.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import mail_drafts
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


# Added after the table existed, so applied with the same additive migration the
# rest of this codebase uses rather than by rebuilding it.
#
# Outreach could compose an email and store it and stop. There was no Gmail
# draft id, no approval state, and nothing that turned a stored draft into a
# message you could look at and send -- so twenty-one outreach emails were text
# in a database table. Recruiter replies already had all of this; outreach was
# simply never built that far.
_DRAFT_COLUMNS = (
    ("approval_status", "TEXT"),
    ("approved_at", "TEXT"),
    ("gmail_draft_id", "TEXT"),
    ("gmail_sent_message_id", "TEXT"),
    ("attachment_paths", "TEXT"),
    ("last_error_message", "TEXT"),
)

# awaiting_approval -> approved -> creating -> created, and dismissed from any
# of them. `sent` stays on the existing `status` column: sending is a fact about
# the outreach, not a stage of preparing the draft.
DRAFT_STATES = ("awaiting_approval", "approved", "creating", "created", "failed",
                "dismissed")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(OUTREACH_SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(outreach)")}
    for name, kind in _DRAFT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE outreach ADD COLUMN {name} {kind}")


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
        # The Gmail side. `draftStatus` is None on every row written before this
        # existed, which reads correctly as "never prepared for sending".
        "draftStatus": _opt(r, "approval_status"),
        "approvedAt": _opt(r, "approved_at"),
        "gmailDraftId": _opt(r, "gmail_draft_id"),
        "gmailSentMessageId": _opt(r, "gmail_sent_message_id"),
        "attachments": mail_drafts.stored_attachments(r),
        "lastError": _opt(r, "last_error_message"),
    }


def _opt(r: sqlite3.Row, key: str) -> Any:
    """Read a column that may predate this row's schema."""
    try:
        return r[key]
    except (IndexError, KeyError):
        return None


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


# ------------------------------------------------- preparing a Gmail draft
#
# Mirrors `recruiter_messages`, which already solved this: a draft is approved
# by the candidate, claimed by an agent session holding the Gmail connector,
# turned into a real Gmail draft, and marked created. Nothing sends. The
# candidate presses send in Gmail, having read it.


# A mailbox snapshot older than this describes a mailbox that has moved on, so
# its silence stops meaning anything. Same threshold daily_fetch reconciles at.
SNAPSHOT_STALE_HOURS = 36


def _sent_snapshot() -> tuple[list[dict[str, Any]], float | None]:
    """Messages this account has already sent, and how old that record is.

    Read from the gitignored Gmail snapshot, because CareerOS holds no Gmail
    credentials and cannot look at the live mailbox itself. The age is returned
    with it: a stale snapshot that shows nothing is not evidence that nothing
    was sent, and a caller that cannot tell those apart will state the wrong
    thing confidently.
    """
    import datetime

    path = pathlib.Path.home() / "careeros" / "gmail-snapshot" / "sent.json"
    if not path.exists():
        return [], None
    age_hours = (
        datetime.datetime.now() - datetime.datetime.fromtimestamp(path.stat().st_mtime)
    ).total_seconds() / 3600
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return [], age_hours

    threads = payload if isinstance(payload, list) else payload.get("threads", [])
    out = []
    for thread in threads:
        for message in thread.get("messages", []):
            for address in message.get("toRecipients") or []:
                out.append({
                    "to": str(address).lower(),
                    "subject": message.get("subject") or "",
                    "date": message.get("date") or "",
                })
    return out, age_hours


def prior_sends(email: str, company: str = "") -> dict[str, Any]:
    """Whether this person has already been written to, and how sure we are.

    Two independent sources, because neither is complete on its own:

    * **The outreach table** — what this system knows it sent. It missed a batch
      once: rows sat at `drafted` while the emails had already gone out, and a
      second set of drafts was written for the same three people, who each
      received the same message twice in one day.
    * **The Gmail sent snapshot** — what actually left the mailbox, which is the
      only place a send made outside this system appears at all.

    The snapshot's age is reported rather than swallowed. Beyond
    SNAPSHOT_STALE_HOURS it cannot rule anything out, and saying "no prior
    contact" from a two-day-old file is exactly the false confidence that caused
    the duplicates.
    """
    address = (email or "").strip().lower()
    hits: list[dict[str, Any]] = []

    rows = []
    if address:
        with connect() as conn:
            _ensure(conn)
            try:
                rows = conn.execute(
                    "SELECT o.company, o.job_title, o.sent_at FROM outreach o"
                    " JOIN contacts c ON c.id = o.contact_id"
                    " WHERE lower(c.email)=? AND o.sent_at IS NOT NULL", (address,)
                ).fetchall()
            except sqlite3.OperationalError:
                # `contacts` is owned by app/contacts.py and is created lazily.
                # Its absence means no contact has been recorded, not an error.
                rows = []
        hits += [
            {"source": "outreach", "when": r["sent_at"],
             "what": f"{r['company']} — {r['job_title']}"}
            for r in rows
        ]

    snapshot, age_hours = _sent_snapshot()
    if address:
        hits += [
            {"source": "gmail", "when": m["date"], "what": m["subject"]}
            for m in snapshot if m["to"] == address
        ]

    stale = age_hours is None or age_hours > SNAPSHOT_STALE_HOURS
    return {
        "email": address,
        "sends": sorted(hits, key=lambda h: str(h["when"]), reverse=True),
        "snapshotAgeHours": round(age_hours, 1) if age_hours is not None else None,
        # True when the mailbox record is too old to rule anything out. A caller
        # showing "no prior contact" must show this next to it.
        "snapshotStale": stale,
    }


def set_attachments(oid: str, paths: Any) -> dict[str, Any] | None:
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET attachment_paths=? WHERE id=?",
            (json.dumps(mail_drafts.normalize_attachments(paths)), oid),
        )
    return get_outreach(oid)


def _contact_email(conn: sqlite3.Connection, oid: str) -> str | None:
    """The recipient's address, or None when there is no contact on file."""
    try:
        row = conn.execute(
            "SELECT c.email FROM outreach o LEFT JOIN contacts c"
            " ON c.id = o.contact_id WHERE o.id=?", (oid,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return (row["email"] if row else None) or None


class AlreadyContacted(ValueError):
    """This person has been written to already."""


def approve_outreach(oid: str, *, force: bool = False) -> dict[str, Any] | None:
    """Mark a composed outreach email ready for a Gmail draft to be made.

    Two refusals, both from failures that happened:

    * A body that promises a resume it does not have — the one that sent three
      recruiter replies saying "resume attached" with nothing attached.
    * A recipient who has already been written to. Three people received the
      same outreach twice in one day because a second batch of drafts was
      written while the first had already gone out, and nothing looked.

    `force` is for the deliberate second approach — a genuine follow-up is not a
    duplicate — and it is recorded rather than silent.
    """
    if not force:
        with connect() as conn:
            _ensure(conn)
            row = _contact_email(conn, oid)
        email = row or ""
        if email:
            history = prior_sends(email)
            if history["sends"]:
                last = history["sends"][0]
                raise AlreadyContacted(
                    f"{email} was already written to on {str(last['when'])[:10]} "
                    f"({last['what'][:60]}). Approve with force=true if this is a "
                    "deliberate follow-up."
                )

    with connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT * FROM outreach WHERE id=?", (oid,)).fetchone()
        if not row:
            return None
        if not (row["email_draft"] or "").strip():
            raise ValueError("This outreach has no email body to draft.")
        if not (row["email_subject"] or "").strip():
            raise ValueError("This outreach has no subject.")
        mail_drafts.validate(row["email_draft"], mail_drafts.stored_attachments(row))
        conn.execute(
            "UPDATE outreach SET approval_status='approved', approved_at=?,"
            " last_error_message=NULL WHERE id=?",
            (now(), oid),
        )
    record = get_outreach(oid)
    if record:
        with connect() as conn:
            _ensure(conn)
            row = _contact_email(conn, oid)
        email = row or ""
        history = prior_sends(email) if email else {"snapshotStale": True,
                                                    "snapshotAgeHours": None}
        # "No prior contact" from a stale mailbox record is not a finding, and a
        # caller that cannot tell the difference will state it as one.
        record["duplicateCheck"] = {
            "checked": bool(email),
            "priorSends": len(history.get("sends", [])),
            "snapshotAgeHours": history.get("snapshotAgeHours"),
            "conclusive": bool(email) and not history.get("snapshotStale", True),
        }
    return record


def claim_approved_outreach() -> dict[str, Any] | None:
    """Take the next approved draft, with its attachments read as base64.

    Claimed by moving it to `creating` inside the transaction, so two agent
    sessions running at once cannot both create a Gmail draft for the same
    outreach and leave the candidate with duplicates.
    """
    with connect() as conn:
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM outreach WHERE approval_status='approved'"
            " ORDER BY approved_at, id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        claimed = conn.execute(
            "UPDATE outreach SET approval_status='creating' WHERE id=?"
            " AND approval_status='approved'", (row["id"],)
        ).rowcount
        if not claimed:
            return None
        oid, body, paths = row["id"], row["email_draft"], mail_drafts.stored_attachments(row)

    record = get_outreach(oid)
    try:
        record["attachmentPayloads"] = mail_drafts.read(paths)
    except ValueError as exc:
        mark_outreach_failed(oid, str(exc))
        return None
    return record


def mark_outreach_draft_created(oid: str, gmail_draft_id: str) -> dict[str, Any] | None:
    if not gmail_draft_id:
        raise ValueError("A Gmail draft ID is required.")
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET approval_status='created', gmail_draft_id=?,"
            " last_error_message=NULL WHERE id=?", (gmail_draft_id, oid),
        )
    return get_outreach(oid)


def mark_outreach_failed(oid: str, message: str) -> dict[str, Any] | None:
    """Recorded rather than retried silently. A draft that failed to reach Gmail
    and looks identical to one that never got there is how these go unnoticed."""
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET approval_status='failed', last_error_message=?"
            " WHERE id=?", (str(message)[:400], oid),
        )
    return get_outreach(oid)


def dismiss_outreach(oid: str) -> dict[str, Any] | None:
    """Decide not to send this one. Distinct from failing, and from sending."""
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE outreach SET approval_status='dismissed' WHERE id=?", (oid,)
        )
    return get_outreach(oid)
