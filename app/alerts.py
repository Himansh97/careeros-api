"""Things the candidate meant to send and did not.

`/api/follow-ups` answers "what did I send that needs chasing". It cannot
answer the opposite question, which is the one that actually loses
opportunities: what did I write, approve, or receive — and then never act on.

Six outreach emails have sat at `drafted` since 9 Aug. A recruiter reply was
approved and never sent. A SoFi application stopped on a security-code step
and was never resubmitted. None of those appear anywhere in the app, because
every existing view is keyed on things that *happened*.

Nothing here contacts Gmail. The API holds no Gmail credentials by design, so
facts that only Gmail knows — whether a reply went out, whether an inbox
message is still unanswered — are written in by `scripts/check_replies.py`
from an agent session and read back here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import connect

# How long something may sit unsent before it is worth raising. A draft written
# an hour ago is work in progress; one from three days ago has been forgotten.
STALE_DRAFT_HOURS = 24
STALE_APPROVAL_HOURS = 6


@dataclass(frozen=True)
class Alert:
    kind: str
    severity: str  # "high" | "medium"
    title: str
    detail: str
    action: str
    ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "ref": self.ref,
        }


def _age_hours(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def _humanise(hours: float) -> str:
    if hours < 48:
        return f"{int(hours)} hours"
    return f"{int(hours // 24)} days"


def unsent_recruiter_replies() -> list[Alert]:
    """Replies approved for sending that never went out.

    Approving is the candidate deciding to reply. If nothing followed, the
    decision was made and then dropped — the worst case, because the app shows
    it as handled.
    """
    from .recruiter_messages import list_messages

    alerts: list[Alert] = []
    for message in list_messages():
        draft = message.get("draft") or {}
        if draft.get("sentAt") or draft.get("status") in (None, "dismissed", "awaiting_approval"):
            continue
        age = _age_hours(draft.get("approvedAt") or draft.get("updatedAt"))
        if age < STALE_APPROVAL_HOURS:
            continue
        who = message.get("senderName") or message.get("senderEmail") or "a recruiter"
        alerts.append(
            Alert(
                kind="recruiter_reply_unsent",
                severity="high",
                title=f"Reply to {who} was approved but never sent",
                detail=(
                    f"Approved {_humanise(age)} ago and still not in your Sent folder. "
                    "CareerOS never sends — this one is waiting on you."
                ),
                action="Open the draft and send it from Gmail",
                ref=message.get("gmailMessageId"),
            )
        )
    return alerts


def unsent_outreach() -> list[Alert]:
    """Outreach written but never sent."""
    from .outreach_store import list_outreach

    alerts: list[Alert] = []
    for record in list_outreach():
        if record.get("status") != "drafted":
            continue
        age = _age_hours(record.get("createdAt"))
        if age < STALE_DRAFT_HOURS:
            continue
        alerts.append(
            Alert(
                kind="outreach_unsent",
                severity="medium",
                title=f"{record.get('company', 'Outreach')} draft never sent",
                detail=(
                    f"Written {_humanise(age)} ago for {record.get('jobTitle') or 'a role'} "
                    "and still unsent."
                ),
                action="Send it, or dismiss it if you've moved on",
                ref=record.get("id"),
            )
        )
    return alerts


# `next_action` carries ordinary pipeline prompts as well as real blockers —
# "Review and approve" sits on 22 applications and means the system is working,
# not that something is wrong. Alerting on the column wholesale produced 35
# alerts from 29 applications, which is the same noise that made the approval
# queue skimmable. Only these phrases describe something stuck.
_BLOCKING_NOTES = ("closed", "no longer accepting", "security code", "resubmit",
                   "action required", "expired", "verification")


def blocked_applications() -> list[Alert]:
    """Applications that cannot progress without the candidate.

    `next_action` is written by the liveness check and by the reply checker.
    A posting that closed and an application that stopped on a verification
    step are both things only the candidate can decide about.
    """
    alerts: list[Alert] = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT job_id, company, title, next_action, status FROM applications "
            "WHERE next_action IS NOT NULL AND next_action != ''"
        ).fetchall()

    for row in rows:
        note = row["next_action"]
        low = note.lower()
        if not any(marker in low for marker in _BLOCKING_NOTES):
            continue
        closed = "closed" in low or "no longer accepting" in low
        alerts.append(
            Alert(
                kind="application_closed" if closed else "application_blocked",
                # A closed posting is information; a half-finished application
                # is a lost opportunity that is still recoverable.
                severity="medium" if closed else "high",
                title=f"{row['company']} — {note}",
                detail=f"{row['title']} is at status '{row['status']}'.",
                action=(
                    "Nothing to send — the posting is gone"
                    if closed
                    else "Finish the outstanding step to complete this application"
                ),
                ref=row["job_id"],
            )
        )
    return alerts


def build_alerts() -> list[dict[str, Any]]:
    """Everything outstanding, most urgent first."""
    alerts = unsent_recruiter_replies() + blocked_applications() + unsent_outreach()
    order = {"high": 0, "medium": 1}
    alerts.sort(key=lambda a: (order.get(a.severity, 9), a.kind))
    return [a.as_dict() for a in alerts]
