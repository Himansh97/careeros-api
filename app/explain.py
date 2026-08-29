"""Resolve a requirement, as a posting words it, to something that teaches it.

This is the half of "learn while applying" that was missing. `LearnableTerm`
already turned requirement labels on a job page into doors, but it matched a
term against concept cards by exact string and against lessons by track name or
title substring — so of the sixty-odd requirements a real posting produces, a
handful lit up and the rest stayed plain text. A door that opens one time in ten
is not a feature, it is a tease.

The matching problem here is the one `skills.py` already solved for scoring: a
posting says "data pipelines", "ETL", or "Airflow" for the thing the curriculum
files under the pipelines track. So this reuses `SKILL_ALIASES` rather than
inventing a second vocabulary that would drift from the first.

Ranking is deliberately conservative. An exact concept card wins, because it
quotes the candidate's own work back at them; then a lesson whose concept or
skill slug names the term; then an alias hit. Anything below that returns
nothing at all, because a requirement silently routed to a vaguely related
lesson is worse than one left alone — the reader trusts the link precisely
because it has been quiet elsewhere.
"""
from __future__ import annotations

import re
from typing import Any

from app.concepts import deck
from app.skills import SKILL_ALIASES
from app.technical_learning.curriculum import load_curriculum


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _canonical(term: str) -> set[str]:
    """Every canonical skill name this term could be naming.

    Both directions, for the reason `AGENTS.md` records: one-directional alias
    matching once filed a posting's phrasing under a canonical name and then
    failed to find that same canonical name in the evidence.
    """
    want = _norm(term)
    hits: set[str] = set()
    for canonical, aliases in SKILL_ALIASES.items():
        if _norm(canonical) == want:
            hits.add(canonical)
            continue
        for alias in aliases:
            alias_norm = _norm(alias)
            if alias_norm and alias_norm == want:
                hits.add(canonical)
                break
    return hits


def _lesson_keys(lesson: Any) -> set[str]:
    """Everything a lesson answers to, authored phrasings first."""
    keys = {_norm(t) for t in lesson.teaches}
    keys |= {
        _norm(lesson.concept),
        _norm(lesson.skill),
        _norm(lesson.track),
        _norm(lesson.title),
    }
    return keys - {""}


def explain(term: str, profile: Any) -> dict[str, Any]:
    """The best card and the best lesson for a requirement, or neither."""
    term = (term or "").strip()
    if not term:
        return {"term": term, "card": None, "lesson": None, "matched": None}

    want = _norm(term)
    names = {want} | {_norm(c) for c in _canonical(term)}

    card = None
    for candidate in deck(profile):
        if not candidate.definition:
            continue
        if _norm(candidate.term) in names:
            card = candidate.as_dict()
            break

    # Lessons: exact slug match first, then a slug that contains the term as a
    # whole word. `order` keeps the entry point of a track ahead of its advanced
    # material, so a bare "SQL" opens grain rather than window frames.
    lessons = sorted(load_curriculum().lessons, key=lambda l: (l.track, l.order))
    exact = None
    partial = None
    for lesson in lessons:
        keys = _lesson_keys(lesson)
        if names & keys:
            exact = lesson
            break
        if partial is None and any(
            re.search(rf"\b{re.escape(name)}\b", key)
            for key in keys
            for name in names
            if len(name) > 2
        ):
            partial = lesson
    lesson = exact or partial

    return {
        "term": term,
        "card": card,
        "lesson": (
            {
                "id": lesson.id,
                "title": lesson.title,
                "track": lesson.track,
                "level": lesson.level,
                "hook": lesson.hook,
                # The visual is the payoff — it is what makes a thirty-second
                # look worth taking. Key points stay server-side, as
                # `test_lessons.py` requires.
                "visual": lesson.visual.model_dump() if lesson.visual else None,
            }
            if lesson
            else None
        ),
        "matched": ("exact" if exact else "alias") if lesson else ("card" if card else None),
    }
