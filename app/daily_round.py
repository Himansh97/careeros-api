"""Three things a day, taken from what the pipeline is actually asking for.

Why not the deck
----------------
The concept deck holds 158 terms off the resume, alphabetically. That is the
right reference surface and the wrong thing to practise, because it studies what
was written once rather than what is about to be asked. Measured: the thirty jobs
currently staged name sixty distinct requirements between them, and the curated
boards matched none of them. SQL is named by eighteen of those jobs. Simpson's
paradox by none.

So the round draws from live demand — requirements named by jobs sitting at
`ready` or `draft` right now. Submit an application and its requirements stop
being weighted; add a role and its requirements start. The thing being practised
tracks the thing about to happen.

Two kinds of item, routed differently
-------------------------------------
Of those sixty requirements, fifty-three are definable and seven are behavioural
— but the behavioural seven carry a third of the total demand, and hold the top
two places outright: cross-functional collaboration and process improvement, each
named by twenty-one jobs.

Those are not concepts to define. Asking someone to explain "stakeholder
management" produces a sentence nobody has ever wanted to hear. They are stories
to evidence, and `interview_practice` already grades exactly that against the
evidence file — every figure checked, every name checked. So a behavioural item
routes there instead, and the round becomes the thing that finally makes those
ten generic questions demand-driven.

`/prep`'s docstring defends generic questions on the grounds that "a posting does
not list 'tell me about a conflict'". True. But a posting does list
cross-functional collaboration, and that maps onto one.

Deterministic per day
---------------------
Seeded by the date, so the same three are there after lunch and cannot be
rerolled until they get easy. The items are re-derived rather than stored, so a
round opened this afternoon reflects a job submitted this morning; only the
outcome is written down.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import connect

ITEMS_PER_DAY = 3

# Requirements that can only be answered with a story. Asking someone to define
# these produces a sentence nobody wants to hear; asking for a time they did it
# produces something checkable against the evidence file.
BEHAVIOURAL_REQUIREMENTS = {
    "cross-functional collaboration": "beh-conflict",
    "cross-functional coordination": "beh-conflict",
    "stakeholder management": "beh-influence",
    "process improvement": "beh-process",
    "mentoring": "beh-influence",
    "requirements gathering": "beh-ambiguity",
    "project management": "beh-scale",
    "agile": "beh-deadline",
    "communication": "beh-influence",
    "leadership": "beh-influence",
    "business analysis": "beh-ambiguity",
    "problem solving": "beh-data-decision",
}


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass
class RoundItem:
    term: str
    # How many staged jobs name this requirement. The whole reason it is here.
    demand: int
    kind: str            # "concept" | "behavioural"
    question_id: str = ""   # set for behavioural items
    companies: list[str] = field(default_factory=list)
    box: int = 0
    has_card: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "demand": self.demand,
            "kind": self.kind,
            "questionId": self.question_id,
            "companies": self.companies[:4],
            "box": self.box,
            "hasCard": self.has_card,
        }


def live_demand() -> dict[str, dict[str, Any]]:
    """Requirements named by jobs staged right now, and who is asking.

    Reads `ready` and `draft` only. A submitted application has already been
    sent, so what it asked for is no longer what is about to be asked — it moves
    to the interview pack's problem, not this one.
    """
    from .discovery_store import current_snapshot
    from .profile import load_profile
    from .scoring import score_job

    jobs = {j.get("id"): j for j in current_snapshot().jobs}
    with connect() as conn:
        rows = conn.execute(
            "SELECT job_id, company FROM applications WHERE status IN ('ready','draft')"
        ).fetchall()

    profile = load_profile()
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        job = jobs.get(row["job_id"])
        if not job:
            # Aged out of the snapshot. Its requirements are unknowable now, and
            # guessing them from the title would be inventing demand.
            continue
        for req in score_job(job, profile).get("requirements", []):
            label = str(req.get("label") or "").strip()
            if not label:
                continue
            entry = found.setdefault(label, {"demand": 0, "companies": []})
            entry["demand"] += 1
            company = row["company"] or ""
            if company and company not in entry["companies"]:
                entry["companies"].append(company)
    return found


def _rank(term: str, demand: int, due: bool, seed: str) -> tuple:
    """Order candidates for one day: due first, then by demand.

    The obvious ranking — demand first — is wrong, and it was wrong in the first
    version of this file. Demand alone served the same three terms every single
    morning, because reviewing one does not lower how many employers want it.
    Cross-functional collaboration is named by eighteen jobs today and will be
    named by eighteen jobs tomorrow; sorting on that produces a round that never
    moves.

    So the Leitner schedule decides *whether* a term is in play and demand
    decides the order *among* the ones that are. Answer something and it steps
    aside until it is due again, which is the whole point of having a schedule.
    The hash breaks remaining ties deterministically per day.
    """
    digest = hashlib.sha256(f"{seed}:{term}".encode()).hexdigest()
    return (0 if due else 1, -demand, digest)


def build(day: str | None = None) -> list[RoundItem]:
    """Today's three, derived rather than stored."""
    from .concepts import _latest_reviews, _notes

    day = day or today()
    demand = live_demand()
    if not demand:
        return []

    notes = _notes()
    reviews = _latest_reviews()
    now = datetime.now(timezone.utc).isoformat()

    candidates: list[tuple[tuple, RoundItem]] = []
    for label, info in demand.items():
        key = label.lower()
        behavioural = key in BEHAVIOURAL_REQUIREMENTS
        review = reviews.get(label) or {}
        box = int(review.get("box") or 0)
        # Never reviewed counts as due, same rule as `concepts.Card.due`.
        due = box == 0 or str(review.get("due_at") or "") <= now
        item = RoundItem(
            term=label,
            demand=info["demand"],
            kind="behavioural" if behavioural else "concept",
            question_id=BEHAVIOURAL_REQUIREMENTS.get(key, ""),
            companies=info["companies"],
            box=box,
            has_card=label in notes,
        )
        candidates.append((_rank(label, item.demand, due, day), item))

    candidates.sort(key=lambda pair: pair[0])
    return [item for _, item in candidates[:ITEMS_PER_DAY]]


def _row(day: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM daily_round WHERE day=?", (day,)).fetchone()
    return dict(row) if row else None


def streak(*, now: datetime | None = None) -> int:
    """Consecutive completed days, counting back from the most recent one.

    Counted back from the last completed day rather than from today, so a round
    not done *yet* today does not read as a broken streak at nine in the morning.
    Same rule as `interview_practice.overview`, deliberately — two different
    definitions of a streak in one app is how a number stops being believed.
    """
    with connect() as conn:
        days = [
            r["day"] for r in conn.execute(
                "SELECT day FROM daily_round WHERE completed_at IS NOT NULL "
                "ORDER BY day DESC"
            ).fetchall()
        ]
    if not days:
        return 0

    today_d = (now or datetime.now(timezone.utc)).date()
    latest = date.fromisoformat(days[0])
    # A gap of more than one day from today means the streak is already over.
    if (today_d - latest).days > 1:
        return 0

    count = 1
    previous = latest
    for value in days[1:]:
        current = date.fromisoformat(value)
        if (previous - current).days != 1:
            break
        count += 1
        previous = current
    return count


def state(day: str | None = None) -> dict[str, Any]:
    """Today's round: the items, whether it is done, and the streak."""
    day = day or today()
    row = _row(day)
    done = bool(row and row.get("completed_at"))

    # A finished day is shown back as served, not as it would be derived now —
    # the pipeline moves during the day and a completed round should not.
    if done and row:
        items = json.loads(row["items"] or "[]")
    else:
        items = [i.as_dict() for i in build(day)]

    return {
        "day": day,
        "items": items,
        "completed": done,
        "scored": int(row.get("scored") or 0) if row else 0,
        "streak": streak(),
        "total": len(items),
    }


def complete(day: str | None = None, scored: int = 0,
             items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Record that today's round was worked."""
    day = day or today()
    served = items if items is not None else [i.as_dict() for i in build(day)]
    at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO daily_round (day, items, scored, completed_at) VALUES (?,?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET items=excluded.items, "
            "scored=excluded.scored, completed_at=excluded.completed_at",
            (day, json.dumps(served), int(scored), at),
        )
    return state(day)
