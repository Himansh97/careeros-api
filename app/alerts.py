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


def _settled_by_a_send(draft: dict[str, Any]) -> bool:
    """Whether this draft's *current* approval has already gone out.

    The presence of `sentAt` was taken as the answer, and it is not the
    question. A draft row is reused across cycles, so a thread replied to once
    carries `sentAt` forever — and a later approval that failed to produce a
    draft was silently swallowed by that stale timestamp. What matters is which
    came last: an approval after the last send is a cycle that has not been
    sent, whatever the record remembers about August.
    """
    sent = draft.get("sentAt")
    if not sent:
        return False
    approved = draft.get("approvedAt")
    if not approved:
        return True
    # Compared as ages rather than as strings: these arrive in two formats
    # ("...Z" and "...+00:00") that do not sort correctly against each other.
    return _age_hours(sent) <= _age_hours(approved)


def failed_reply_drafts() -> list[Alert]:
    """Replies the candidate approved that never became a draft at all.

    This had no alert of any kind. `unsent_recruiter_replies` covers "approved
    and not sent", which assumes a draft exists to go and send; when draft
    creation itself failed there is nothing in Gmail to open, and the record
    was additionally hidden by a stale `sentAt` from an earlier cycle. So a
    decision to reply could fail, and be reported nowhere, indefinitely.

    Raised regardless of age. Every other alert here waits for something to go
    stale because the thing might still be in progress; a failure is already
    final, and waiting six hours to mention it only delays the retry.
    """
    from .recruiter_messages import list_messages

    alerts: list[Alert] = []
    for message in list_messages():
        draft = message.get("draft") or {}
        if draft.get("status") != "failed":
            continue
        who = message.get("senderName") or message.get("senderEmail") or "a recruiter"
        age = _age_hours(draft.get("updatedAt") or draft.get("approvedAt"))
        reason = (draft.get("lastErrorMessage") or "").strip() or "Gmail rejected the request."
        alerts.append(
            Alert(
                kind="recruiter_reply_failed",
                severity="high",
                title=f"Reply to {who} could not be drafted",
                detail=(
                    f"You approved this reply and Gmail draft creation failed "
                    f"{_humanise(age)} ago, so nothing was created. {reason}"
                ),
                action="Re-approve the reply to try creating the draft again",
                ref=message.get("gmailMessageId"),
            )
        )
    return alerts


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
        # `failed` has its own alert. Reporting it here as well would tell the
        # candidate to open a draft that was never created, and put two
        # entries on the list for one problem.
        if draft.get("status") in (None, "dismissed", "awaiting_approval", "failed"):
            continue
        if _settled_by_a_send(draft):
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
    from .liveness_sync import is_closure_note

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
        closed = is_closure_note(note)
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


# Below this many submitted applications, a conversion rate is noise dressed as
# a statistic. One reply out of four is 25%, and means nothing whatsoever.
MIN_FOR_RATES = 30


def funnel() -> dict[str, Any]:
    """Raw counts through the pipeline, and whether they support a rate yet.

    Deliberately returns counts and not percentages until there is enough to
    divide. The whole learning layer — interview probability, expected value,
    funnel diagnosis — is downstream of this, and every one of them is worthless
    until these numbers grow. Showing the counts makes the wait visible rather
    than leaving it as an unexplained absence.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, submitted_at, first_response_at, outcome, "
            "timestamps_inferred FROM applications"
        ).fetchall()

    submitted = [r for r in rows if r["submitted_at"]]
    responded = [r for r in rows if r["first_response_at"]]
    interviews = sum(1 for r in rows if r["status"] in ("interview", "offer"))
    offers = sum(1 for r in rows if r["outcome"] == "offer")
    rejections = sum(1 for r in rows if r["outcome"] == "rejected")

    return {
        "tracked": len(rows),
        "submitted": len(submitted),
        "responded": len(responded),
        "interviews": interviews,
        "offers": offers,
        "rejections": rejections,
        # How many submit dates were reconstructed rather than observed.
        "inferredTimestamps": sum(1 for r in submitted if r["timestamps_inferred"]),
        "ratesAvailable": len(submitted) >= MIN_FOR_RATES,
        "needForRates": max(0, MIN_FOR_RATES - len(submitted)),
        "note": (
            f"{MIN_FOR_RATES - len(submitted)} more submitted applications before "
            "conversion rates mean anything. Counts only until then."
            if len(submitted) < MIN_FOR_RATES
            else "Enough history to compute conversion rates."
        ),
    }



# A tailored application that sits unsent goes stale in two ways at once: the
# posting ages toward closing, and the queue in front of it grows. Seven days
# is the candidate's own threshold.
STALE_AFTER_DAYS = 7


def aging_applications() -> list[Alert]:
    """Applications prepared but never sent, past the staleness threshold.

    Deliberately counts from when the application record was created rather
    than from the posting date: the clock that matters is how long *this* has
    been sitting ready, not how old the req is. A posting from three weeks ago
    that was tailored this morning is not stale work.

    High severity past double the threshold, because at that point the usual
    outcome is the posting closing before anything is sent — which is exactly
    what nine of the tracked applications did.
    """
    from .store import connect

    from .liveness_sync import is_closure_note

    alerts: list[Alert] = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT job_id, company, title, status, updated_at, created_at, apply_url,"
            " next_action"
            " FROM applications WHERE status IN ('ready','qualified','tailoring')"
        ).fetchall()

    for row in rows:
        # A closed posting is already reported by its own alert; saying it is
        # also stale is noise on something that cannot be acted on. This comment
        # sat above no filter at all, and `next_action` was not even selected,
        # so eight dead postings were telling the candidate to go submit them.
        if is_closure_note(row["next_action"]):
            continue
        stamp = row["created_at"] or row["updated_at"]
        hours = _age_hours(stamp)
        if hours is None:
            continue
        days = hours / 24.0
        if days < STALE_AFTER_DAYS:
            continue
        alerts.append(
            Alert(
                kind="application_aging",
                severity="high" if days >= STALE_AFTER_DAYS * 2 else "medium",
                title=f"{row['company']} — ready for {int(days)} days, not sent",
                detail=(
                    f"{row['title']} has been prepared and unsent since "
                    f"{str(stamp)[:10]}. Postings close while applications wait."
                ),
                action="Open the application and submit it, or dismiss it",
                ref=row["job_id"],
            )
        )
    return alerts


def unconfirmed_applications() -> list[Alert]:
    """Opened on the employer's site, and never confirmed.

    Deliberately a question rather than a transition. Plenty of ATSs send no
    confirmation email at all, so silence is not evidence the application failed
    -- and it is certainly not evidence it succeeded. Nothing may mark an
    application `submitted` because time passed; that would write a false send
    date onto a real application.
    """
    from .pipeline_signals import stuck_applying

    return [
        Alert(
            kind="application_unconfirmed",
            severity="high",
            title=f"{row['company']} — opened {row['days']} days ago, never confirmed",
            detail=(
                f"{row['title']} was opened on the employer's site and no "
                "confirmation has arrived. Some employers never send one."
            ),
            action="Confirm whether you submitted it, or reopen and finish it",
            ref=row["id"],
        )
        for row in stuck_applying()
    ]


def build_alerts() -> list[dict[str, Any]]:
    """Everything outstanding, most urgent first."""
    alerts = (failed_reply_drafts() + unsent_recruiter_replies()
              + blocked_applications() + unsent_outreach() + aging_applications()
              + unconfirmed_applications())
    order = {"high": 0, "medium": 1}
    alerts.sort(key=lambda a: (order.get(a.severity, 9), a.kind))
    return [a.as_dict() for a in alerts]
