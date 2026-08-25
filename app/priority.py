"""Which application is worth doing next.

A ranked list of fit scores answers "where am I strongest". It does not answer
"where should I spend the next twenty minutes", and those differ: a 78 posted
three hours ago on a two-minute form beats an 84 posted three weeks ago behind a
Workday account creation and seventeen questions.

**There is no interview-probability term here, and that is deliberate.** The
candidate has zero recorded interviews. Any estimate would be a number invented
from nothing, and this codebase has spent its life removing exactly that — a
mortgage role scoring 98/100 "no gaps", a toast reporting a save that never
happened, an approvals queue reporting "all caught up" when it had failed to
load. When `alerts.funnel()` reports enough outcomes, a learned term becomes one
more factor here rather than a rewrite.

Every factor below is observable today: fit is computed from evidence, posting
age is a timestamp, friction is a property of the ATS, and trust is what the
liveness check already looks at.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# Minutes of work the candidate cannot avoid, by platform. These are the parts
# no pre-fill can remove: account creation, re-typing work history a resume
# already states, and bespoke screener questions.
#
# Workday is the outlier and the reason this exists. Greenhouse and Lever take
# a resume and a handful of fields; Workday asks the candidate to become a user
# of Workday, once per employer.
PLATFORM_MINUTES: dict[str, int] = {
    "Greenhouse": 4,
    "Lever": 4,
    "Ashby": 4,
    "SmartRecruiters": 6,
    "The Muse": 6,
    "Arbeitnow": 5,
    "RemoteOK": 3,
    "Workday": 22,
    "iCIMS": 18,
    "Taleo": 20,
    "BrassRing": 20,
}
DEFAULT_MINUTES = 10

# Phrases in a posting that add real work regardless of platform.
EXTRA_WORK = (
    ("cover letter", 12, "cover letter"),
    ("writing sample", 15, "writing sample"),
    ("portfolio", 10, "portfolio"),
    ("assessment", 30, "assessment"),
    ("take-home", 90, "take-home exercise"),
    ("case study", 60, "case study"),
)

# A requisition id is a strong authenticity signal: scam postings rarely carry
# one, because they are not backed by a real hiring system.
_REQ_ID = re.compile(r"\b(?:req(?:uisition)?|job)\s*(?:id|#|no\.?|number)\s*[:#]?\s*\w+", re.I)


def friction(job: dict[str, Any]) -> dict[str, Any]:
    """How much unavoidable manual work this application costs."""
    platform = job.get("atsPlatform") or job.get("source") or ""
    minutes = PLATFORM_MINUTES.get(platform, DEFAULT_MINUTES)

    description = (job.get("description") or "").lower()
    extras = []
    for phrase, cost, label in EXTRA_WORK:
        if phrase in description:
            minutes += cost
            extras.append(label)

    # 0-10, where 10 is an hour or more of work.
    score = min(10, round(minutes / 6))
    return {
        "minutes": minutes,
        "score": score,
        "platform": platform or "unknown",
        "extras": extras,
        "note": (
            f"About {minutes} minutes on {platform or 'this site'}"
            + (f", plus {', '.join(extras)}" if extras else "")
        ),
    }


def trust(job: dict[str, Any], on_its_board: bool | None = None) -> dict[str, Any]:
    """How much this posting looks like a real, currently-open role.

    Signals only — never a verdict on the employer. A low score means "check
    this before spending time on it", not "this is a scam", and the reasons are
    always listed so the candidate can judge for themselves.
    """
    from .liveness import API_PREFIXES

    signals: list[str] = []
    concerns: list[str] = []
    score = 60

    job_id = job.get("id") or ""
    if job_id.startswith(API_PREFIXES):
        score += 25
        signals.append("listed on the employer's own applicant-tracking board")
    elif job.get("origin") == "pasted":
        signals.append("you added this one yourself")
    else:
        score -= 10
        concerns.append("only seen on a third-party board, not the employer's own")

    if on_its_board is False:
        score -= 30
        concerns.append("no longer appears on the board it came from")

    description = job.get("description") or ""
    if _REQ_ID.search(description):
        score += 10
        signals.append("carries a requisition id")

    apply_url = (job.get("applyUrl") or "").lower()
    if apply_url.startswith("https://"):
        score += 5
    elif apply_url:
        score -= 10
        concerns.append("apply link is not HTTPS")

    if len(description) < 400:
        score -= 15
        concerns.append("posting text is unusually thin")

    posted = job.get("postedAt")
    age_days_value = age_days(posted)
    if age_days_value is not None and age_days_value > 45:
        score -= 10
        concerns.append(f"posted {int(age_days_value)} days ago")

    score = max(0, min(100, score))
    return {
        "score": score,
        "signals": signals,
        "concerns": concerns,
        # Deliberately not "safe" / "scam". This looks at provenance, not intent.
        "verdict": "verified" if score >= 80 else "unclear" if score >= 50 else "check first",
    }


def age_days(stamp: str | None) -> float | None:
    """Age of a posting in days, or None when the date is absent or unreadable.

    Public because the prescreen budget needs the same definition of "how old
    is this posting" that the ranking uses. Two definitions of that would drift.
    """
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def freshness(job: dict[str, Any]) -> dict[str, Any]:
    """Newer postings are worth more attention, with no claim about odds.

    A role posted today has not yet accumulated a stack of applications. That is
    an observation about queue position, not a prediction about outcome, and it
    is stated that way.
    """
    age = age_days(job.get("postedAt"))
    if age is None:
        return {"days": None, "factor": 0.85, "note": "posting date unknown"}
    # One decimal. The raw value carries sub-second precision that renders as
    # "3.114156778298611d" in the API and means nothing about a posting date.
    age = round(age, 1)
    if age <= 1:
        return {"days": age, "factor": 1.0, "note": "posted in the last day"}
    if age <= 7:
        return {"days": age, "factor": 0.92, "note": f"posted {int(age)} days ago"}
    if age <= 21:
        return {"days": age, "factor": 0.78, "note": f"posted {int(age)} days ago"}
    return {"days": age, "factor": 0.6, "note": f"posted {int(age)} days ago"}


def skill_gaps(
    scored_jobs: list[tuple[dict[str, Any], dict[str, Any]]], limit: int = 12
) -> list[dict[str, Any]]:
    """Which missing requirement costs the most across the roles being targeted.

    A single resume's gap list says what one employer wanted. Aggregated across
    everything worth applying to, it says which one thing to go and learn — and
    those are different answers. "dbt" appearing in three postings the candidate
    scores 60 on matters less than one appearing in twenty they score 85 on.

    Ranked by reach weighted by fit, because a gap only costs something on roles
    the candidate could otherwise get.

    Deliberately absent: any estimate of hours to learn a skill. That number
    would be invented, it would differ per person by an order of magnitude, and
    presenting it beside real counts would lend it the same authority.
    """
    tally: dict[str, dict[str, Any]] = {}

    for job, score in scored_jobs:
        fit = score.get("rawFitScore", 0)
        for req in score.get("requirements", []):
            if req["match"] == "exact":
                continue
            # A partial match backed by a real claim is not a gap — the
            # evidence exists and only the wording is loose. Counting it here
            # would send the candidate to learn something they already do.
            if req["match"] == "partial" and req.get("evidence"):
                continue

            entry = tally.setdefault(
                req["label"],
                {
                    "skill": req["label"],
                    "jobs": 0,
                    "requiredIn": 0,
                    "fitSum": 0,
                    "examples": [],
                    "hasPartialEvidence": False,
                },
            )
            entry["jobs"] += 1
            entry["fitSum"] += fit
            if req["importance"] == "required":
                entry["requiredIn"] += 1
            if req["match"] == "partial":
                entry["hasPartialEvidence"] = True
            if len(entry["examples"]) < 3:
                entry["examples"].append(
                    f"{job['company']['name']} — {job['title'][:44]}"
                )

    total = len(scored_jobs) or 1
    out = []
    for entry in tally.values():
        mean_fit = entry["fitSum"] / entry["jobs"]
        out.append({
            "skill": entry["skill"],
            "jobs": entry["jobs"],
            "shareOfTargets": round(entry["jobs"] / total * 100),
            "requiredIn": entry["requiredIn"],
            "meanFit": round(mean_fit),
            "examples": entry["examples"],
            # Reach × how good those roles otherwise are. A gap on roles the
            # candidate cannot get anyway is not worth closing first.
            "weight": round(entry["jobs"] * (mean_fit / 100), 1),
            "note": (
                "Listed in your skills inventory but no accomplishment demonstrates it"
                if entry["hasPartialEvidence"]
                else "No evidence at all"
            ),
        })

    out.sort(key=lambda e: (-e["weight"], -e["requiredIn"]))
    return out[:limit]


def priority(job: dict[str, Any], fit: int, on_its_board: bool | None = None) -> dict[str, Any]:
    """Rank by what is worth doing next. Not a probability, and never labelled one."""
    fr = friction(job)
    tr = trust(job, on_its_board)
    fs = freshness(job)

    # Fit dominates: a fast form for a role you don't fit is not a good use of
    # twenty minutes either. Friction and trust adjust; they never rescue.
    effort = 1.0 - min(0.35, fr["minutes"] / 180)
    score = (fit / 100) * fs["factor"] * (0.65 + 0.35 * effort) * (0.5 + tr["score"] / 200)

    return {
        "score": round(score * 10, 1),
        "fit": fit,
        "friction": fr,
        "trust": tr,
        "freshness": fs,
        # Named so nobody reads it as a forecast. The factors are listed so the
        # ranking can be argued with rather than taken on faith.
        "basis": "fit, posting age, application effort, and posting trust",
        "excludes": (
            "No interview-likelihood term: there is not enough outcome history "
            "to compute one honestly."
        ),
    }
