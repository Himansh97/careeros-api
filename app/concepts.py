"""Recall practice for the terms the candidate put on their own resume.

Why this exists
---------------
The evidence file names 165 distinct technical terms across 45 claims, and 121
of them appear exactly once. The single-mention tail is the exposure: ULDD,
MISMO schema, Purchase Advice, SimCLR, contrastive learning, ECDSA, Ed25519,
SMOTE, Bicep, OIDC, bootstrap confidence intervals. Those are the terms an
interviewer reaches for to find out whether a resume is real, and they are the
same ones that have gone cold, because each came from one project and has not
been said out loud since.

Nothing else in this system covers that. `/prep` drills ten generic behavioural
questions. The technical lab is a hardcoded twenty-one-drill SQL and Python
curriculum that imports nothing from `app.profile`. The one place a resume term
becomes a question — `interview._questions_from_requirements` — takes its labels
from the *job posting*, and the text it produces is never answered or stored.

The deck is derived, never stored
---------------------------------
A written-down card list drifts the moment a claim is added or retired, and this
system has a whole containment apparatus premised on the evidence file being the
only source of truth about the candidate. So the terms are read from
`career_evidence.json` on every request and canonicalised through the alias table
`skills.py` already maintains.

Deliberately **not** `EvidenceClaim.skill_tokens`. That property mines every word
over two characters out of the claim prose, which is right for its job — matching
a posting's requirement against evidence — and useless here: it would produce
cards for "the", "built" and "with". The curated `skills` array on each claim is
the list a human wrote, and it is the list worth learning.

What is stored is what the evidence file cannot know
----------------------------------------------------
Two things. The sourced general meaning of a term, and how well the candidate
recalls it.

The split between those and the claim itself is the whole design, and it is
copied from `question_research`, whose seeder states it plainly: the shape is
general and sourced, the substance is the candidate's own claims. A card shows
the claim *verbatim* — nothing generated — and then, if one has been seeded, a
definition carrying real sources. `save_note` refuses an empty source list for
the same reason `save_research` does.

A term with no definition yet is still a usable card. It shows what the candidate
did with it, which is the half that answers the follow-up question. Degrading to
"here is your own claim" is honest; inventing a definition is not.

Leitner, not SM-2
-----------------
Five boxes at 1, 3, 7, 21 and 60 days. Recall moves a card up one box; failure
sends it back to box 1. Chosen because the schedule has to be sayable in the UI:
"box 3 of 5, back in a week" is a sentence a person can act on, where an ease
factor of 2.36 is not. The same instinct that made `progress_overview` define
mastery as a plain conjunction rather than a score.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect

# Days until a card in each box comes back. Index is box - 1.
BOX_DAYS = (1, 3, 7, 21, 60)
MAX_BOX = len(BOX_DAYS)

RATINGS = ("again", "hard", "good", "easy")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alias_map() -> dict[str, str]:
    """Surface form -> canonical label, from the table `skills.py` maintains.

    Reused rather than rebuilt so a term is spelled the same here as it is in a
    requirement match. It merges little on this evidence file — three pairs — but
    the three it merges are real: `statistical modeling` against `Statistical
    modeling` would otherwise be two cards for one idea.
    """
    from .skills import SKILL_ALIASES

    out: dict[str, str] = {}
    for canonical, surfaces in SKILL_ALIASES.items():
        out[canonical.lower()] = canonical
        for surface in surfaces:
            out[surface.lower()] = canonical
    return out


@dataclass
class Card:
    """One term, everything known about it, and when it is next due."""

    term: str
    # Every claim the term is declared on, so the card can show what the
    # candidate actually did with it rather than only what it means.
    claims: list[dict[str, str]] = field(default_factory=list)
    employers: list[str] = field(default_factory=list)
    definition: str = ""
    sources: list[str] = field(default_factory=list)
    box: int = 0          # 0 = never reviewed
    due_at: str = ""
    reviewed_at: str = ""

    @property
    def due(self) -> bool:
        """Unseen cards are due. Not knowing is not the same as not being owed."""
        if self.box == 0:
            return True
        return self.due_at <= _now().isoformat()

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "claims": self.claims,
            "employers": self.employers,
            "definition": self.definition,
            "sources": self.sources,
            "hasDefinition": bool(self.definition),
            "box": self.box,
            "maxBox": MAX_BOX,
            "dueAt": self.due_at,
            "reviewedAt": self.reviewed_at,
            "due": self.due,
            "mentions": len(self.claims),
        }


def _terms_from_evidence(profile: Any) -> dict[str, list[dict[str, str]]]:
    """Canonical term -> the claims that declare it."""
    alias = _alias_map()
    found: dict[str, list[dict[str, str]]] = {}
    for claim in profile.evidence:
        if not getattr(claim, "approved_for_resume", False):
            # A retired claim is not on the resume, so its terms are not what an
            # interviewer will be reading from.
            continue
        for raw in getattr(claim, "skills", None) or []:
            surface = str(raw).strip()
            if not surface:
                continue
            term = alias.get(surface.lower(), surface)
            found.setdefault(term, []).append(
                {
                    "claimId": claim.claim_id,
                    "employer": claim.employer,
                    "claim": claim.claim,
                }
            )
    return found


def _notes() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT term, definition, sources FROM concept_note"
        ).fetchall()
    return {
        r["term"]: {
            "definition": r["definition"],
            "sources": json.loads(r["sources"] or "[]"),
        }
        for r in rows
    }


def _latest_reviews() -> dict[str, dict[str, Any]]:
    """The most recent review per term. The index makes this the cheap direction."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT term, box, due_at, reviewed_at FROM concept_review "
            "WHERE id IN (SELECT MAX(id) FROM concept_review GROUP BY term)"
        ).fetchall()
    return {r["term"]: dict(r) for r in rows}


def deck(profile: Any) -> list[Card]:
    """Every term on the resume, with its claims, definition and schedule."""
    notes = _notes()
    reviews = _latest_reviews()
    cards: list[Card] = []

    for term, claims in _terms_from_evidence(profile).items():
        note = notes.get(term, {})
        review = reviews.get(term, {})
        seen: list[str] = []
        for c in claims:
            if c["employer"] not in seen:
                seen.append(c["employer"])
        cards.append(
            Card(
                term=term,
                claims=claims,
                employers=seen,
                definition=note.get("definition", ""),
                sources=note.get("sources", []),
                box=int(review.get("box") or 0),
                due_at=str(review.get("due_at") or ""),
                reviewed_at=str(review.get("reviewed_at") or ""),
            )
        )

    # Alphabetical, deliberately. The order was chosen to be even rather than
    # risk-ranked, and alphabetical is the only order that is obviously not
    # ranking anything. `mentions` is on every card if that changes.
    cards.sort(key=lambda c: c.term.lower())
    return cards


def due_cards(profile: Any) -> list[Card]:
    """What to study now: overdue first, then never seen, then the rest."""
    cards = [c for c in deck(profile) if c.due]
    cards.sort(key=lambda c: (c.box != 0, c.due_at or "", c.term.lower()))
    return cards


def next_box(current: int, rating: str) -> int:
    """Where a card lands after a review.

    `again` returns to the start whatever the streak was — a term you could not
    explain is not one you half-know, and the cost of seeing it again tomorrow is
    a few seconds. `hard` holds position rather than advancing, so a card you
    limped through does not earn a three-week gap.
    """
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
    if rating == "again":
        return 1
    if rating == "hard":
        return max(1, current)
    step = 2 if rating == "easy" else 1
    return min(MAX_BOX, max(1, current) + step)


def review(term: str, rating: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Record one review and return the new schedule."""
    at = now or _now()
    with connect() as conn:
        row = conn.execute(
            "SELECT box FROM concept_review WHERE term=? ORDER BY id DESC LIMIT 1",
            (term,),
        ).fetchone()
        current = int(row["box"]) if row else 0
        box = next_box(current, rating)
        due = at + timedelta(days=BOX_DAYS[box - 1])
        conn.execute(
            "INSERT INTO concept_review (term, rating, box, due_at, reviewed_at) "
            "VALUES (?,?,?,?,?)",
            (term, rating, box, due.isoformat(), at.isoformat()),
        )
    return {
        "term": term,
        "rating": rating,
        "box": box,
        "maxBox": MAX_BOX,
        "dueAt": due.isoformat(),
        "dueInDays": BOX_DAYS[box - 1],
    }


def save_note(term: str, definition: str, sources: list[str]) -> dict[str, Any]:
    """Store the general meaning of a term. Refuses without sources.

    The same refusal `save_research` makes, for the same reason: an unsourced
    definition is a guess wearing a citation's clothes, and this one would be
    recited in an interview.
    """
    term = (term or "").strip()
    definition = (definition or "").strip()
    clean = [s.strip() for s in (sources or []) if s and s.strip()]

    if not term:
        raise ValueError("a concept note needs a term")
    if not definition:
        raise ValueError(f"refusing to store an empty definition for {term!r}")
    if not clean:
        raise ValueError(f"refusing to store a definition for {term!r} with no sources")

    at = _now().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO concept_note (term, definition, sources, researched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(term) DO UPDATE SET "
            "definition=excluded.definition, sources=excluded.sources, "
            "researched_at=excluded.researched_at",
            (term, definition, json.dumps(clean), at),
        )
    return {"term": term, "definition": definition, "sources": clean, "researchedAt": at}


def overview(profile: Any) -> dict[str, Any]:
    """Counts for the map header. No invented readiness number."""
    cards = deck(profile)
    by_box = {b: 0 for b in range(MAX_BOX + 1)}
    for card in cards:
        by_box[card.box] += 1
    return {
        "total": len(cards),
        "unseen": by_box[0],
        "learning": sum(by_box[b] for b in (1, 2, 3)),
        "known": sum(by_box[b] for b in (4, 5)),
        "due": sum(1 for c in cards if c.due),
        "withDefinition": sum(1 for c in cards if c.definition),
        "byBox": by_box,
        "maxBox": MAX_BOX,
        "boxDays": list(BOX_DAYS),
    }
