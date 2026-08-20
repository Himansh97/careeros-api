"""Durable recruiter-message events and candidate-approved Gmail draft queue."""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
from contextlib import contextmanager
from email.utils import parseaddr
import re
from typing import Any

from . import store


RECRUITER_MESSAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS recruiter_messages (
    gmail_message_id TEXT PRIMARY KEY,
    application_id TEXT,
    sender_name TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    classification TEXT NOT NULL,
    synopsis TEXT NOT NULL,
    gmail_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recruiter_reply_drafts (
    id TEXT PRIMARY KEY,
    gmail_message_id TEXT NOT NULL UNIQUE,
    to_addresses TEXT NOT NULL,
    cc_addresses TEXT NOT NULL,
    bcc_addresses TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'awaiting_approval', 'approved', 'creating', 'created', 'dismissed', 'failed'
    )),
    approved_at TEXT,
    gmail_draft_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content_fingerprint TEXT,
    last_error_code TEXT,
    last_error_message TEXT
);
"""

_EDITABLE_STATUSES = {"awaiting_approval", "failed"}

# Deciding not to send is always available, including once a Gmail draft
# exists. Dismissal was gated on the same set as editing, which left `created`
# with no exit: the draft could not be dismissed, and the "approved but never
# sent" alert therefore repeated forever unless it was sent. Two of these were
# never sendable in the first place — one addressed to a `donotreply@` mailbox,
# one to a colleague covering a parental leave that had since ended — so the
# only honest resolution for them was the one the app did not offer.
#
# Editing stays narrower on purpose: changing the text after Gmail holds a copy
# would let the two diverge, which is a different problem from choosing to drop
# the reply altogether.
_DISMISSABLE_STATUSES = _EDITABLE_STATUSES | {"created"}


# Sending is recorded as its own fact rather than another `status` value.
#
# Two reasons. The status column carries a CHECK constraint that SQLite cannot
# alter in place, so extending the enum would mean rebuilding the table. More
# importantly, sending is not the next step after "created" — the candidate
# sent the GitLab reply straight from Gmail while the draft still sat at
# `approved`, with no Gmail draft ever created. A timestamp records what
# happened without claiming the draft moved through a state it never entered.
_SENT_COLUMNS = (
    ("sent_at", "TEXT"),
    ("gmail_sent_message_id", "TEXT"),
)

# Attachments were missing entirely, which is why "resume attached" went out
# three times with nothing attached. The draft had no field for one, so no
# review step could catch it and no reader could tell the difference between a
# reply that forgot the resume and one that never wanted it.
#
# Paths rather than bytes. The packet PDFs are regenerated whenever a resume is
# retailored, and a copy pasted into the database at draft time would quietly
# become the stale version. The path is resolved and read at send time, so what
# goes out is what is on disk now, and a missing file is an error rather than an
# old resume nobody noticed.
_ATTACHMENT_COLUMNS = (
    ("attachment_paths", "TEXT"),
)

# Phrases that promise an attachment. If the body makes the promise and nothing
# is attached, approval fails -- the same shape as the figure-binding check in
# Custody: the claim has to be backed by the thing it refers to.
_ATTACHMENT_CLAIMS = re.compile(
    r"\b(resume|cv|c\.v\.)\s+(is\s+)?(attached|enclosed)\b"
    r"|\battach(ed|ing)\s+(is\s+)?(my|the|a)?\s*(resume|cv)\b"
    r"|\bplease\s+(find|see)\s+(the\s+)?attach",
    re.IGNORECASE,
)


def _connect() -> sqlite3.Connection:
    conn = store.connect()
    conn.executescript(RECRUITER_MESSAGE_SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(recruiter_reply_drafts)")}
    for name, kind in _SENT_COLUMNS + _ATTACHMENT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE recruiter_reply_drafts ADD COLUMN {name} {kind}")
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return store.now()


def _normalize_addresses(addresses: list[str] | None) -> list[str]:
    if addresses is None:
        return []
    if not isinstance(addresses, (list, tuple)):
        raise ValueError("Recipients must be a list of email addresses")
    normalized: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        if not isinstance(address, str):
            raise ValueError("Recipient addresses must be text")
        value = address.strip().lower()
        _, parsed = parseaddr(value)
        if not value or "\n" in value or "\r" in value or "@" not in parsed:
            raise ValueError(f"Invalid recipient address: {address}")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _normalize_attachments(value: Any) -> list[str]:
    """Absolute, de-duplicated paths. Existence is checked at approval, not here.

    A draft is allowed to name a resume that has not been generated yet -- the
    packet may be rebuilt between drafting and sending. What is not allowed is
    approving one whose file is missing.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        resolved = str(pathlib.Path(text).expanduser())
        if resolved not in out:
            out.append(resolved)
    return out


def _attachment_paths(row: sqlite3.Row) -> list[str]:
    raw = _optional(row, "attachment_paths")
    return json.loads(raw) if raw else []


def _draft_values(draft: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        json.dumps(_normalize_addresses(draft.get("to"))),
        json.dumps(_normalize_addresses(draft.get("cc"))),
        json.dumps(_normalize_addresses(draft.get("bcc"))),
        str(draft.get("subject") or "").strip(),
        str(draft.get("body") or ""),
    )


def _row_to_message(conn: sqlite3.Connection, event: sqlite3.Row) -> dict[str, Any]:
    draft = conn.execute(
        "SELECT * FROM recruiter_reply_drafts WHERE gmail_message_id=?",
        (event["gmail_message_id"],),
    ).fetchone()
    return {
        "gmailMessageId": event["gmail_message_id"],
        "applicationId": event["application_id"],
        "senderName": event["sender_name"],
        "senderEmail": event["sender_email"],
        "subject": event["subject"],
        "receivedAt": event["received_at"],
        "classification": event["classification"],
        "synopsis": event["synopsis"],
        "gmailUrl": event["gmail_url"],
        "createdAt": event["created_at"],
        "updatedAt": event["updated_at"],
        "draft": _row_to_draft(draft) if draft else None,
    }


def _row_to_draft(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "gmailMessageId": row["gmail_message_id"],
        "to": json.loads(row["to_addresses"]),
        "cc": json.loads(row["cc_addresses"]),
        "bcc": json.loads(row["bcc_addresses"]),
        "subject": row["subject"],
        "body": row["body"],
        "status": row["status"],
        "approvedAt": row["approved_at"],
        "gmailDraftId": row["gmail_draft_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "contentFingerprint": row["content_fingerprint"],
        "lastErrorCode": row["last_error_code"],
        "lastErrorMessage": row["last_error_message"],
        "sentAt": _optional(row, "sent_at"),
        "gmailSentMessageId": _optional(row, "gmail_sent_message_id"),
        "attachments": _attachment_paths(row),
    }


def _optional(row: sqlite3.Row, key: str) -> Any:
    """Read a column that may predate this row's schema."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _message_or_raise(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM recruiter_messages WHERE gmail_message_id=?", (message_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown recruiter message: {message_id}")
    return row


def _draft_or_raise(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM recruiter_reply_drafts WHERE gmail_message_id=?", (message_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Missing reply draft for recruiter message: {message_id}")
    return row


def _fingerprint(draft: sqlite3.Row) -> str:
    content = {
        "to": json.loads(draft["to_addresses"]),
        "cc": json.loads(draft["cc_addresses"]),
        "bcc": json.loads(draft["bcc_addresses"]),
        "subject": draft["subject"],
        "body": draft["body"],
        # Swapping the resume changes what gets sent, so it has to change the
        # fingerprint -- otherwise an approved draft could be re-pointed at a
        # different file without the approval being invalidated.
        "attachments": _attachment_paths(draft),
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_complete(draft: sqlite3.Row) -> None:
    if not json.loads(draft["to_addresses"]):
        raise ValueError("Draft requires at least one recipient")
    if not draft["subject"].strip():
        raise ValueError("Draft requires a subject")
    body = draft["body"]
    if not body.strip():
        raise ValueError("Draft requires a body")

    attachments = _attachment_paths(draft)

    # The failure this exists to stop: a reply that says "resume attached" and
    # arrives with nothing attached. It happened three times before the draft
    # had anywhere to record an attachment at all.
    if _ATTACHMENT_CLAIMS.search(body) and not attachments:
        raise ValueError(
            "Draft says a resume is attached but has no attachment. Attach the "
            "file, or reword the body so it does not promise one."
        )

    missing = [p for p in attachments if not pathlib.Path(p).is_file()]
    if missing:
        raise ValueError(
            "Attachment file is missing: " + ", ".join(missing)
        )


def _timeline(conn: sqlite3.Connection, event: sqlite3.Row, label: str) -> None:
    if event["application_id"]:
        store.add_timeline(conn, event["application_id"], label)


def _sanitize_error(code: str, message: str) -> tuple[str, str]:
    safe_code = str(code).strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{1,64}", safe_code):
        safe_code = "gmail_draft_error"
    return safe_code, "Draft creation failed. Please retry after reviewing the draft."


def upsert_message(payload: dict) -> dict:
    """Insert/update event metadata without replacing an existing reply draft."""
    draft = payload["draft"]
    ts = _now()
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT application_id FROM recruiter_messages WHERE gmail_message_id=?",
            (payload["gmailMessageId"],),
        ).fetchone()
        conn.execute(
            """INSERT INTO recruiter_messages
               (gmail_message_id, application_id, sender_name, sender_email, subject,
                received_at, classification, synopsis, gmail_url, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(gmail_message_id) DO UPDATE SET
                 application_id=CASE
                     WHEN excluded.application_id IS NOT NULL THEN excluded.application_id
                     ELSE recruiter_messages.application_id
                 END,
                 synopsis=excluded.synopsis,
                 updated_at=excluded.updated_at""",
            (
                payload["gmailMessageId"], payload.get("applicationId"),
                payload["senderName"], payload["senderEmail"], payload["subject"],
                payload["receivedAt"], payload["classification"], payload["synopsis"],
                payload["gmailUrl"], ts, ts,
            ),
        )
        to, cc, bcc, subject, body = _draft_values(draft)
        conn.execute(
            """INSERT OR IGNORE INTO recruiter_reply_drafts
               (id, gmail_message_id, to_addresses, cc_addresses, bcc_addresses,
                subject, body, status, created_at, updated_at, attachment_paths)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (f"rmd_{payload['gmailMessageId']}", payload["gmailMessageId"], to, cc, bcc,
             subject, body, "awaiting_approval", ts, ts,
             json.dumps(_normalize_attachments(draft.get("attachments")))),
        )
        event = _message_or_raise(conn, payload["gmailMessageId"])
        association_changed = (
            payload.get("applicationId") is not None
            and (not existing or existing["application_id"] != payload["applicationId"])
        )
        if association_changed:
            _timeline(conn, event, "Recruiter reply detected")
        message = _row_to_message(conn, event)

    # Outside the transaction on purpose: `advance` opens its own connection,
    # and nesting them on one SQLite file deadlocks under BEGIN IMMEDIATE.
    #
    # This is the step that was missing. The classifier has produced
    # "application confirmation" since the beginning and nothing read it, so
    # Adobe and CVS both confirmed receipt while their applications sat waiting
    # for the candidate to press a button.
    from .pipeline_signals import apply_signal

    message["signal"] = apply_signal(message)
    return message


def get_message(message_id: str) -> dict | None:
    with _connection() as conn:
        event = conn.execute(
            "SELECT * FROM recruiter_messages WHERE gmail_message_id=?", (message_id,)
        ).fetchone()
        return _row_to_message(conn, event) if event else None


def list_messages(application_id: str | None = None) -> list[dict]:
    with _connection() as conn:
        if application_id is None:
            rows = conn.execute(
                "SELECT * FROM recruiter_messages ORDER BY received_at DESC, gmail_message_id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recruiter_messages WHERE application_id=? "
                "ORDER BY received_at DESC, gmail_message_id DESC", (application_id,)
            ).fetchall()
        return [_row_to_message(conn, row) for row in rows]


def update_draft(message_id: str, patch: dict) -> dict:
    allowed = {"to", "cc", "bcc", "subject", "body", "attachments"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"Unsupported draft fields: {', '.join(sorted(unknown))}")
    with _connection() as conn:
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] not in _EDITABLE_STATUSES:
            raise ValueError(f"Draft is not editable while {draft['status']}")
        fields: list[str] = []
        values: list[Any] = []
        for key, column in (("to", "to_addresses"), ("cc", "cc_addresses"), ("bcc", "bcc_addresses")):
            if key in patch:
                fields.append(f"{column}=?")
                values.append(json.dumps(_normalize_addresses(patch[key])))
        if "subject" in patch:
            fields.append("subject=?")
            values.append(str(patch["subject"] or "").strip())
        if "body" in patch:
            fields.append("body=?")
            values.append(str(patch["body"] or ""))
        if "attachments" in patch:
            fields.append("attachment_paths=?")
            values.append(json.dumps(_normalize_attachments(patch["attachments"])))
        if fields:
            if draft["status"] == "failed":
                fields.append("content_fingerprint=NULL")
            fields.append("updated_at=?")
            values.extend([_now(), message_id])
            updated = conn.execute(
                f"UPDATE recruiter_reply_drafts SET {', '.join(fields)} "
                "WHERE gmail_message_id=? AND status IN ('awaiting_approval', 'failed')",
                values,
            ).rowcount
            if not updated:
                raise ValueError("Draft changed state before the edit could be saved")
        return _row_to_message(conn, _message_or_raise(conn, message_id))


def approve_draft(message_id: str) -> dict:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] == "approved":
            return _row_to_message(conn, event)
        if draft["status"] != "awaiting_approval":
            raise ValueError(f"Draft cannot be approved while {draft['status']}")
        _validate_complete(draft)
        conn.execute(
            """UPDATE recruiter_reply_drafts SET status='approved', approved_at=?,
               content_fingerprint=?, last_error_code=NULL, last_error_message=NULL,
               updated_at=? WHERE gmail_message_id=?""",
            (_now(), _fingerprint(draft), _now(), message_id),
        )
        _timeline(conn, event, "Recruiter reply draft approved")
        return _row_to_message(conn, event)


def dismiss_draft(message_id: str) -> dict:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] not in _DISMISSABLE_STATUSES and draft["status"] != "dismissed":
            raise ValueError(f"Draft cannot be dismissed while {draft['status']}")
        if draft["status"] != "dismissed":
            conn.execute(
                "UPDATE recruiter_reply_drafts SET status='dismissed', updated_at=? "
                "WHERE gmail_message_id=?", (_now(), message_id),
            )
            _timeline(conn, event, "Recruiter reply draft dismissed")
        return _row_to_message(conn, event)


def retry_draft(message_id: str) -> dict:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] != "failed":
            raise ValueError(f"Draft cannot be retried while {draft['status']}")
        _validate_complete(draft)
        conn.execute(
            """UPDATE recruiter_reply_drafts SET status='approved', approved_at=?,
               content_fingerprint=?, last_error_code=NULL, last_error_message=NULL, updated_at=?
               WHERE gmail_message_id=?""",
            (_now(), _fingerprint(draft), _now(), message_id),
        )
        _timeline(conn, event, "Recruiter reply draft retry approved")
        return _row_to_message(conn, event)


_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def _read_attachments(paths: list[str]) -> list[dict[str, Any]]:
    """Base64 payloads shaped for the Gmail draft call, so the sender never
    has to touch the filesystem or encode anything by hand."""
    import base64

    payloads = []
    for path in paths:
        file = pathlib.Path(path)
        if not file.is_file():
            raise ValueError(f"Attachment file is missing: {path}")
        payloads.append({
            "filename": file.name,
            "mimeType": _MIME_BY_SUFFIX.get(file.suffix.lower(), "application/octet-stream"),
            "content": base64.b64encode(file.read_bytes()).decode("ascii"),
        })
    return payloads


def claim_approved_draft() -> dict | None:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = conn.execute(
            "SELECT * FROM recruiter_reply_drafts WHERE status='approved' "
            "ORDER BY approved_at, id LIMIT 1"
        ).fetchone()
        if not draft:
            return None
        claimed = conn.execute(
            "UPDATE recruiter_reply_drafts SET status='creating', updated_at=? "
            "WHERE gmail_message_id=? AND status='approved'", (_now(), draft["gmail_message_id"]),
        ).rowcount
        if not claimed:
            return None
        event = _message_or_raise(conn, draft["gmail_message_id"])
        _timeline(conn, event, "Draft creation started")
        message = _row_to_message(conn, event)
        # Read the files here, at send time, rather than storing bytes at draft
        # time -- a resume retailored in between should go out in its current
        # form, and a file that has disappeared should stop the send instead of
        # silently dropping the attachment.
        message["draft"]["attachmentPayloads"] = _read_attachments(
            _attachment_paths(draft)
        )
        return message


def mark_draft_created(message_id: str, gmail_draft_id: str) -> dict:
    if not gmail_draft_id:
        raise ValueError("Gmail draft ID is required")
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] != "creating":
            raise ValueError(f"Draft cannot be completed while {draft['status']}")
        conn.execute(
            """UPDATE recruiter_reply_drafts SET status='created', gmail_draft_id=?,
               updated_at=? WHERE gmail_message_id=?""",
            (gmail_draft_id, _now(), message_id),
        )
        _timeline(conn, event, "Gmail draft created")
        return _row_to_message(conn, event)


def mark_draft_sent(
    message_id: str, gmail_sent_message_id: str, sent_at: str | None = None
) -> dict:
    """Record that the reply actually went out.

    Deliberately accepted from any state. CareerOS never sends — the candidate
    does, in Gmail — so this is always news arriving from outside, and it must
    not be refused because the draft sat at `approved` rather than `created`.
    The GitLab reply to Izzy Chu was sent that way: straight from Gmail, with
    no CareerOS-created draft in between.

    Idempotent, because reconciliation re-reads the same Sent folder on every
    run and must not append a timeline entry each time.
    """
    if not gmail_sent_message_id:
        raise ValueError("Gmail message ID of the sent mail is required")
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if _optional(draft, "sent_at"):
            return _row_to_message(conn, event)
        conn.execute(
            """UPDATE recruiter_reply_drafts SET sent_at=?, gmail_sent_message_id=?,
               updated_at=? WHERE gmail_message_id=?""",
            (sent_at or _now(), gmail_sent_message_id, _now(), message_id),
        )
        _timeline(conn, event, "Reply sent from Gmail")
        return _row_to_message(conn, event)


def mark_draft_failed(message_id: str, code: str, message: str) -> dict:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = _message_or_raise(conn, message_id)
        draft = _draft_or_raise(conn, message_id)
        if draft["status"] != "creating":
            raise ValueError(f"Draft cannot fail while {draft['status']}")
        safe_code, safe_message = _sanitize_error(code, message)
        conn.execute(
            """UPDATE recruiter_reply_drafts SET status='failed', last_error_code=?,
               last_error_message=?, updated_at=? WHERE gmail_message_id=?""",
            (safe_code, safe_message, _now(), message_id),
        )
        _timeline(conn, event, "Gmail draft creation failed")
        return _row_to_message(conn, event)
