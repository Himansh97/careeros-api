"""Advancing an application from things that already happened.

The pipeline is `ready -> applying -> submitted -> recruiter_contacted -> ...`
and until now every step of it was a button the candidate pressed. That is
absurd for two of them, because the system already holds the evidence:

* **Opening the application** is the only unambiguous "I am now applying"
  moment. It needs no inference — you cannot apply without going to the ATS.
* **A confirmation email** is the employer stating the application arrived.
  `recruiter_messages` has been classifying these as `application confirmation`
  since the beginning; nothing read the label. Adobe and CVS both confirmed
  receipt and neither advanced anything.
* **A progression email** is the employer saying it moved on. Dallas College's
  "Hiring Supervisor Review" is sitting in the database classified as
  `application_progressed` with `application_id` NULL — matched to nothing, so
  it did nothing.

Two rules govern all of it, and both come from failures already recorded in this
repository rather than from caution in the abstract:

**Never walk an application backwards.** `store.advance` now refuses it. A
confirmation that arrives after a recruiter has already called must not reset
the application to `submitted`.

**Never invent a send.** Plenty of ATSs never email at all. An application with
no confirmation stays at `applying` and raises an alert asking whether it went
out. Nothing here marks something `submitted` because time passed — that would
write a false date onto a real application, which is the one thing this system
is built not to do.
"""
from __future__ import annotations

import re
from typing import Any

from .store import StatusRegression, advance, connect, list_applications

# Classifications the message classifier already produces, mapped to the stage
# each one is evidence of. Anything not listed here advances nothing.
SIGNALS: dict[str, str] = {
    "application confirmation": "submitted",
    "application_confirmation": "submitted",
    "application_progressed": "recruiter_contacted",
    "application status": "recruiter_contacted",
}

# How long after an application was opened a confirmation can still plausibly
# belong to it. Beyond this the match is more likely a coincidence of company
# name than a real link.
MATCH_WINDOW_DAYS = 45

# Below this, the match is reported rather than acted on. A wrong auto-advance
# is worse than no advance: it silently moves the wrong application and the
# candidate has no reason to look.
CONFIDENCE_FLOOR = 0.6

_WORD = re.compile(r"[A-Za-z0-9&]+")
_NOISE = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "the", "group",
    "holdings", "technologies", "technology", "solutions", "services", "global",
    "international", "usa", "us", "america",
})


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")} - _NOISE


def _domain_name(email: str) -> str:
    """The company part of a sender address.

    `adobe@myworkday.com` is Workday's domain carrying Adobe's name in the local
    part, which is why the local part is read as well as the host — a great many
    ATS senders look like this and matching on host alone finds "myworkday" for
    every one of them.
    """
    local, _, host = (email or "").partition("@")
    host_name = host.split(".")[0] if host else ""
    if host_name in {"myworkday", "greenhouse", "lever", "icims", "taleo",
                     "successfactors", "smartrecruiters", "ashbyhq",
                     "eprivatemail", "notifications"}:
        return local
    return host_name


def score_match(message: dict[str, Any], application: dict[str, Any]) -> float:
    """How strongly this message looks like it belongs to this application.

    Company agreement carries the weight; the role title only breaks ties. Two
    applications to the same company are common and their confirmations read
    almost identically, so a title hit is the difference between advancing the
    right one and advancing whichever came first.
    """
    company = (application.get("company") or {})
    company_name = company.get("name") if isinstance(company, dict) else str(company)
    company_tokens = _tokens(company_name)
    if not company_tokens:
        return 0.0

    haystack = _tokens(
        f"{message.get('senderEmail', '')} {message.get('senderName', '')} "
        f"{message.get('subject', '')} {message.get('synopsis', '')}"
    )
    haystack |= _tokens(_domain_name(message.get("senderEmail", "")))

    overlap = company_tokens & haystack
    if not overlap:
        return 0.0

    score = 0.7 * (len(overlap) / len(company_tokens))

    title_tokens = _tokens(application.get("title", ""))
    if title_tokens:
        score += 0.3 * (len(title_tokens & haystack) / len(title_tokens))
    return round(min(score, 1.0), 3)


def match_application(message: dict[str, Any]) -> dict[str, Any]:
    """The application this message is about, with the confidence and why.

    Returns the best candidate always, and a separate `confident` flag. The
    caller decides what to do with a weak match; this function never silently
    drops one, because a message that matched nothing is the thing worth
    surfacing.
    """
    if message.get("applicationId"):
        return {
            "applicationId": message["applicationId"],
            "score": 1.0,
            "confident": True,
            "why": "the message was already linked to this application",
        }

    scored = []
    for app in list_applications():
        score = score_match(message, app)
        if score > 0:
            scored.append((score, app))
    if not scored:
        return {"applicationId": None, "score": 0.0, "confident": False,
                "why": "no application matches this sender or subject"}

    scored.sort(key=lambda t: -t[0])
    best_score, best = scored[0]

    # A near-tie means two applications to the same employer look equally
    # likely, and picking one is a coin toss the candidate would never see.
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    ambiguous = len(scored) > 1 and (best_score - runner_up) < 0.15

    confident = best_score >= CONFIDENCE_FLOOR and not ambiguous
    if ambiguous:
        why = (f"{len(scored)} applications match about equally "
               f"({best_score} vs {runner_up}) — too close to call")
    elif best_score < CONFIDENCE_FLOOR:
        why = f"best match scored {best_score}, below the {CONFIDENCE_FLOOR} floor"
    else:
        why = f"matched on company and title, score {best_score}"

    return {"applicationId": best["id"], "score": best_score,
            "confident": confident, "why": why}


def apply_signal(message: dict[str, Any]) -> dict[str, Any]:
    """Advance the application this message is evidence about, if it is safe to.

    Returns what happened either way, so a caller can report "matched nothing"
    and "advanced" with the same shape. Nothing here raises on a refusal: a
    message that cannot be matched is an ordinary outcome, not an error.
    """
    classification = (message.get("classification") or "").strip().lower()
    target = SIGNALS.get(classification)
    if not target:
        return {"advanced": False, "reason": "not a status-bearing message",
                "classification": classification}

    match = match_application(message)
    if not match["confident"]:
        return {"advanced": False, "reason": match["why"],
                "applicationId": match["applicationId"], "needsReview": True,
                "score": match["score"]}

    note = f"{target.replace('_', ' ').capitalize()} — {message.get('subject', '')[:90]}"
    try:
        advance(
            match["applicationId"], target, note,
            # The employer's timestamp, not the moment the mail was read. A
            # confirmation can arrive days late and stamping it `now` would put
            # a false submitted date on a real application.
            at=message.get("receivedAt"),
        )
    except StatusRegression as exc:
        return {"advanced": False, "reason": str(exc),
                "applicationId": match["applicationId"]}

    return {"advanced": True, "applicationId": match["applicationId"],
            "status": target, "score": match["score"], "reason": match["why"]}


def mark_applying(app_id: str, note: str = "Application opened") -> bool:
    """Called when the candidate opens the application on the employer's site.

    The one signal in the whole pipeline that requires no inference. Returns
    False rather than raising when the application is already further on —
    re-opening a submitted application to check something is normal and must not
    look like an error.
    """
    try:
        advance(app_id, "applying", note)
        return True
    except StatusRegression:
        return False


def stuck_applying(days: int = 3) -> list[dict[str, Any]]:
    """Applications opened but never confirmed.

    Deliberately a question, not a transition. Many ATSs send no confirmation at
    all, so silence is not evidence of anything and a timer must never produce a
    `submitted`. What the candidate needs is to be asked.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, company, title, updated_at FROM applications "
            "WHERE status='applying'"
        ).fetchall()
    for row in rows:
        try:
            when = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            out.append({
                "id": row["id"], "company": row["company"], "title": row["title"],
                "openedAt": row["updated_at"],
                "days": (datetime.now(timezone.utc) - when).days,
            })
    return out
