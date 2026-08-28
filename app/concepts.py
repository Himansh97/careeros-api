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
    # Layers, in the order they are meant to be read: the precise sentence, the
    # plain one, the same thing in Hindi, where it shows up in practice, and a
    # picture. A term you have never met is not made familiar by precision.
    simple: str = ""
    hindi: str = ""
    application: str = ""
    visual: dict[str, Any] | None = None
    derived: list[str] = field(default_factory=list)
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
            "simple": self.simple,
            "hindi": self.hindi,
            "application": self.application,
            "visual": self.visual,
            # Which layers a model restated rather than a source asserting them.
            "derived": self.derived,
            "layers": sum(
                1 for v in (self.definition, self.simple, self.hindi,
                            self.application, self.visual) if v
            ),
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


# The whole diagram vocabulary. Four shapes cover most technical concepts, and
# a closed set is the point: a concept has to be understood well enough to say
# which shape it is, and every card then renders in the app's own type and
# colours rather than as 158 unrelated pictures.
VISUAL_KINDS = ("flow", "layers", "compare", "cycle")

# Layers a model wrote, restated from the sourced definition. Named so the UI
# can label them rather than implying every line carries a citation.
DERIVABLE = ("simple", "hindi", "application", "visual")


def _notes() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT term, definition, sources, simple, hindi, application, "
            "visual, derived FROM concept_note"
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            visual = json.loads(r["visual"]) if r["visual"] else None
        except ValueError:
            visual = None
        out[r["term"]] = {
            "definition": r["definition"],
            "sources": json.loads(r["sources"] or "[]"),
            "simple": r["simple"],
            "hindi": r["hindi"],
            "application": r["application"],
            "visual": visual,
            "derived": json.loads(r["derived"] or "[]"),
        }
    return out


def topics() -> list[dict[str, Any]]:
    """Curated subject areas to study beyond the resume.

    The resume deck answers "can you defend what you wrote". This answers "do
    you know the field you say you work in" — the questions that do not quote
    your own bullet back at you.

    Curated rather than generated, for the same reason the technical curriculum
    is: a syllabus assembled by a model is a plausible-looking syllabus.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT slug, title, blurb, terms FROM concept_topic ORDER BY sort_order, title"
        ).fetchall()
    return [
        {
            "slug": r["slug"],
            "title": r["title"],
            "blurb": r["blurb"],
            "terms": json.loads(r["terms"] or "[]"),
        }
        for r in rows
    ]


def save_topic(slug: str, title: str, blurb: str, terms: list[str],
               sort_order: int = 0) -> dict[str, Any]:
    slug = (slug or "").strip()
    clean = [t.strip() for t in (terms or []) if t and t.strip()]
    if not slug or not title.strip():
        raise ValueError("a topic needs a slug and a title")
    if not clean:
        raise ValueError(f"refusing to store topic {slug!r} with no terms")
    with connect() as conn:
        conn.execute(
            "INSERT INTO concept_topic (slug, title, blurb, terms, sort_order) "
            "VALUES (?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
            "title=excluded.title, blurb=excluded.blurb, terms=excluded.terms, "
            "sort_order=excluded.sort_order",
            (slug, title.strip(), blurb.strip(), json.dumps(clean), sort_order),
        )
    return {"slug": slug, "title": title, "blurb": blurb, "terms": clean}


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
                simple=note.get("simple", ""),
                hindi=note.get("hindi", ""),
                application=note.get("application", ""),
                visual=note.get("visual"),
                derived=note.get("derived", []),
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


def topic_cards(slug: str) -> dict[str, Any]:
    """Cards for a curated topic. These have no claim behind them, by definition.

    A resume card answers "can you defend what you wrote" and leads with the
    candidate's own sentence. A topic card answers "do you know this field" and
    has no such sentence to lead with — so it leads with the plain-English layer
    instead. The `claims` list is empty and the UI must not pretend otherwise.
    """
    topic = next((t for t in topics() if t["slug"] == slug), None)
    if topic is None:
        raise KeyError(f"no topic {slug!r}")

    notes = _notes()
    reviews = _latest_reviews()
    cards: list[Card] = []
    for term in topic["terms"]:
        note = notes.get(term, {})
        review = reviews.get(term, {})
        cards.append(
            Card(
                term=term,
                claims=[],
                employers=[],
                definition=note.get("definition", ""),
                sources=note.get("sources", []),
                simple=note.get("simple", ""),
                hindi=note.get("hindi", ""),
                application=note.get("application", ""),
                visual=note.get("visual"),
                derived=note.get("derived", []),
                box=int(review.get("box") or 0),
                due_at=str(review.get("due_at") or ""),
                reviewed_at=str(review.get("reviewed_at") or ""),
            )
        )
    # Teaching order, as curated — not alphabetical. A syllabus has a sequence.
    return {**topic, "cards": [c.as_dict() for c in cards]}


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


def check_visual(visual: dict[str, Any] | None) -> dict[str, Any] | None:
    """A diagram spec, or None. Refuses a shape the renderer cannot draw.

    Structured rather than an image so every concept renders in the app's own
    type and colours, and so a wrong diagram is a data fix rather than an asset
    to redraw. The closed vocabulary is deliberate: having to say which of four
    shapes a concept is forces the concept to be understood.
    """
    if not visual:
        return None
    kind = str(visual.get("kind") or "").strip()
    if kind not in VISUAL_KINDS:
        raise ValueError(f"visual kind must be one of {', '.join(VISUAL_KINDS)}")
    nodes = visual.get("nodes") or []
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 6:
        # One box is not a diagram and seven is a diagram nobody reads.
        raise ValueError("a visual needs between 2 and 6 nodes")
    clean_nodes = []
    for node in nodes:
        label = str((node or {}).get("label") or "").strip()
        if not label:
            raise ValueError("every visual node needs a label")
        clean_nodes.append({
            "label": label[:60],
            "note": str((node or {}).get("note") or "").strip()[:120],
        })
    return {"kind": kind, "caption": str(visual.get("caption") or "").strip()[:160],
            "nodes": clean_nodes}


def save_note(
    term: str,
    definition: str,
    sources: list[str],
    *,
    simple: str = "",
    hindi: str = "",
    application: str = "",
    visual: dict[str, Any] | None = None,
    derived: list[str] | None = None,
) -> dict[str, Any]:
    """Store what a term means, in layers. Refuses a definition without sources.

    The same refusal `save_research` makes, for the same reason: an unsourced
    definition is a guess wearing a citation's clothes, and this one would be
    recited in an interview.

    The other layers are restatements of that definition — plainer, in Hindi, in
    practice, as a picture — and are recorded in `derived` so the UI can say a
    model wrote them rather than implying every line carries a citation. They
    assert nothing the sourced definition does not, which is why they are allowed
    to be generated at all.
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

    shape = check_visual(visual)
    at = _now().isoformat()
    marks = json.dumps(sorted(set(derived or [])))

    with connect() as conn:
        conn.execute(
            "INSERT INTO concept_note (term, definition, sources, researched_at, "
            "simple, hindi, application, visual, derived) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET "
            "definition=excluded.definition, sources=excluded.sources, "
            "researched_at=excluded.researched_at, simple=excluded.simple, "
            "hindi=excluded.hindi, application=excluded.application, "
            "visual=excluded.visual, derived=excluded.derived",
            (term, definition, json.dumps(clean), at, simple.strip(), hindi.strip(),
             application.strip(), json.dumps(shape) if shape else "", marks),
        )
    return {
        "term": term, "definition": definition, "sources": clean,
        "simple": simple, "hindi": hindi, "application": application,
        "visual": shape, "derived": json.loads(marks), "researchedAt": at,
    }


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
