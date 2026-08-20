"""SQLite persistence for applications, approvals, and activity."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH, READY_SCORE
from .db import connect as database_connection


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Compatibility boundary over the shared, always-closing DB runtime."""
    with database_connection(path=DB_PATH) as connection:
        yield connection


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


# Statuses that mean the application has been sent, or the employer has already
# responded. Re-tailoring must not walk any of these backwards.
#
# This listed "applied" and "interviewing" — two spellings nothing in the system
# writes. The pipeline the UI actually drives is qualified -> tailoring -> ready
# -> applying -> submitted -> recruiter_contacted -> screening -> interview ->
# offer -> rejected, so the guard missed "submitted": the exact status the
# "Mark applied" button produces, and the only one `advance` stamps
# `submitted_at` for.
#
# The cost was the failure this guard exists to prevent. Six applications the
# candidate had sent were rewound to "ready" by the next autopilot re-tailor and
# reappeared in the apply queue, asking to be applied to a second time — and one
# was, taking a submitted application back to "applying". Sending is the one
# piece of state in this pipeline that cannot be reconstructed from the job
# boards.
#
# "applied", "interviewing" and "withdrawn" stay as legacy spellings that are
# already in the stored data. "applying" is deliberately absent: it means in
# progress, not sent, and must stay re-tailorable.
_COMMITTED_STATUSES = (
    "applied",
    "submitted",
    "recruiter_contacted",
    "screening",
    "interview",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)


def set_resume_score(app_id: str, resume_score: int) -> None:
    """Record a resume score, and let it decide whether the application is ready.

    Two guards, for two different ways this went wrong.

    It unconditionally forced status back to "ready", so re-running /tailor on a
    job the candidate had already applied to silently erased that fact — the one
    piece of state in the pipeline they cannot reconstruct. A score refresh is
    not evidence that an application was un-sent.

    It also wrote "ready" whatever the score was, while `tailor_resume` gated the
    same word on `READY_SCORE`. Nothing caught the disagreement because the audit
    was mostly constants and every score landed in the 80s and 90s. Once the
    scoring started discriminating, resumes scoring 50 sat in the list marked
    "Ready" with "Review and approve" beside them — the number saying the
    document is not worth sending and the label saying it is.
    """
    ready = resume_score >= READY_SCORE
    with connect() as conn:
        row = conn.execute(
            "SELECT status, resume_score FROM applications WHERE id=?", (app_id,)
        ).fetchone()
        committed = row and (row["status"] or "") in _COMMITTED_STATUSES
        previous = row["resume_score"] if row else None

        if committed:
            conn.execute(
                "UPDATE applications SET resume_score=?, updated_at=? WHERE id=?",
                (resume_score, now(), app_id),
            )
        else:
            conn.execute(
                "UPDATE applications SET resume_score=?, status=?, next_action=?, "
                "updated_at=? WHERE id=?",
                (
                    resume_score,
                    "ready" if ready else "draft",
                    "Review and approve"
                    if ready
                    else f"Strengthen the resume — {READY_SCORE - resume_score} short of {READY_SCORE}",
                    now(),
                    app_id,
                ),
            )
        # Only a change is history.
        #
        # Autopilot re-tailors on every pass and lands on the same score nearly
        # every time, so logging each call made 85% of the timeline read
        # "Resume tailored", with 1158 of those 1330 entries repeating the
        # number immediately above them. One application carried twenty
        # identical lines at score 83. A history that records non-events buries
        # the events it exists to show.
        if previous is None:
            add_timeline(conn, app_id, f"Resume tailored — score {resume_score}")
        elif previous != resume_score:
            direction = "up" if resume_score > previous else "down"
            add_timeline(
                conn,
                app_id,
                f"Resume re-tailored — score {direction} {previous} to {resume_score}",
            )


# Statuses that mean the employer has responded — the moment worth timing an
# application against. `rejected` counts: a rejection is a response, and
# excluding it would make the response rate flatter than reality.
_RESPONSE_STATUSES = ("recruiter_contacted", "screening", "interview", "offer", "rejected")

# Terminal outcomes. Recorded separately from status so a later status change
# cannot silently erase how an application actually ended.
_TERMINAL = {"offer": "offer", "rejected": "rejected", "withdrawn": "withdrawn"}

# The pipeline in order, so "forwards" is a fact rather than a judgement call.
#
# `advance` would happily walk a sent application back to `ready`, and the
# comment above _UNSENT records what that cost once already: six applications
# the candidate had actually sent were rewound and offered back to them to send
# again. Automatic signals make that far likelier -- a confirmation email
# arriving out of order must not undo a later stage -- so regression is refused
# unless a human explicitly asks for it.
_PIPELINE = (
    "qualified", "tailoring", "draft", "ready", "applying", "submitted",
    "applied", "recruiter_contacted", "screening", "interview", "offer",
)

# Ends the application; reachable from anywhere, and never walked back out of.
_ABSORBING = ("rejected", "withdrawn")


def _rank(status: str) -> int:
    """Position in the pipeline. Unknown statuses rank -1 and never block."""
    try:
        return _PIPELINE.index(status)
    except ValueError:
        return -1


class StatusRegression(Exception):
    """A transition that would move an application backwards."""


def record_outcome(
    app_id: str, outcome: str, reason: str = "", stage: str = ""
) -> None:
    """Record how an application ended, and how far it got.

    Separate from `advance` because an outcome is not a pipeline step: it can
    arrive at any stage, often from an email rather than a click, and it must
    not be overwritten by a later status change.

    Nothing here infers *why*. The employer rarely says, and a guess stored in
    the same field as a stated reason would be indistinguishable from one later.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?", (app_id,)
        ).fetchone()
        conn.execute(
            "UPDATE applications SET outcome=?, outcome_at=COALESCE(outcome_at, ?), "
            "outcome_reason=?, outcome_stage=?, updated_at=? WHERE id=?",
            (
                outcome,
                now(),
                reason.strip() or None,
                stage or (row["status"] if row else None),
                now(),
                app_id,
            ),
        )
        label = f"Outcome: {outcome}"
        if reason.strip():
            label += f" — {reason.strip()[:120]}"
        add_timeline(conn, app_id, label)


def advance(
    app_id: str,
    status: str,
    note: str,
    *,
    at: str | None = None,
    allow_regression: bool = False,
) -> None:
    """Move an application on, stamping the timestamps that transition implies.

    Each stamp is written only if empty, so re-entering a stage keeps the date
    it was first reached — the first response is the one that matters for
    timing, not the most recent.

    `at` is when the transition actually happened, which is not always now. A
    confirmation email can arrive days after the application went out, and
    stamping `submitted_at` with the moment the mail was *read* would put a
    false date on a real application.

    `allow_regression` is off by default. A human correcting a mistake through
    the UI may pass it; an automatic signal may not, because a late or
    misordered email must never undo a stage the application has already
    reached.
    """
    ts = at or now()
    current = (get_application(app_id) or {}).get("status")
    if (
        not allow_regression
        and current
        and current not in _ABSORBING
        and status not in _ABSORBING
        and _rank(status) >= 0
        and _rank(current) > _rank(status)
    ):
        raise StatusRegression(
            f"{app_id} is already at {current!r}; refusing to move it back to "
            f"{status!r}"
        )
    with connect() as conn:
        sets = ["status=?", "updated_at=?"]
        args: list[Any] = [status, ts]

        if status == "submitted":
            sets.append("submitted_at=COALESCE(submitted_at, ?)")
            args.append(ts)
        if status in _RESPONSE_STATUSES:
            sets.append("first_response_at=COALESCE(first_response_at, ?)")
            args.append(ts)
        if status in _TERMINAL:
            sets.append("outcome=?")
            sets.append("outcome_at=COALESCE(outcome_at, ?)")
            args.extend([_TERMINAL[status], ts])

        args.append(app_id)
        conn.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id=?", args)
        add_timeline(conn, app_id, note)


def _optional(row: sqlite3.Row, key: str) -> Any:
    """Read a column that may predate this row's schema."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _row_to_app(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    # Imported here rather than at module scope: liveness_sync imports from this
    # module, and a top-level import would close the loop.
    from .liveness_sync import is_closure_note

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
        # Whether the posting is gone, as a field rather than as a string every
        # caller has to parse for itself. A "ready" application against a closed
        # posting is not ready for anything, and eight of them were sitting in
        # the queue sorted by fit score with nothing to tell them apart.
        "postingClosed": is_closure_note(row["next_action"]),
        # When this application last changed. Already stored on every write;
        # it simply was not returned, so the resume list had no recency signal
        # and "where is the one I was working on" had no answer.
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        # Was a hardcoded None. Real now — and honest about which dates were
        # reconstructed rather than observed.
        "submittedAt": _optional(row, "submitted_at"),
        "firstResponseAt": _optional(row, "first_response_at"),
        "outcome": _optional(row, "outcome"),
        "outcomeAt": _optional(row, "outcome_at"),
        "outcomeReason": _optional(row, "outcome_reason"),
        # The furthest stage reached before it ended — the single most useful
        # field for a later autopsy, because "rejected after interview" and
        # "rejected without a reply" are different failures with different fixes.
        "outcomeStage": _optional(row, "outcome_stage"),
        "timestampsInferred": bool(_optional(row, "timestamps_inferred") or 0),
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
    """Raise an approval, without undoing a decision the candidate already made.

    This was `INSERT OR REPLACE`, which resets `status` to 'pending' on every
    call. Approval ids are deterministic, so any later write for the same job
    resurrected an approval the candidate had already resolved — and
    `POST /api/jobs/{id}/tailor` calls this unconditionally, so merely opening
    a resume for a job already applied to put it back in the queue. Ten stale
    items came back that way after being cleared.

    The payload is still refreshed, because a re-tailored resume genuinely has
    a new score worth showing. Only the resolution is protected: deciding is
    the candidate's act, and nothing automatic should quietly reverse it.
    """
    approval_id = f"appr_{kind}_{job_id}"
    with connect() as conn:
        existing = conn.execute(
            "SELECT status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if existing and existing["status"] != "pending":
            conn.execute(
                "UPDATE approvals SET payload=? WHERE id=?",
                (json.dumps(payload), approval_id),
            )
            return approval_id
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
