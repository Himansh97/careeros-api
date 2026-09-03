"""STAR bullets: composed from recorded claims, verified against them.

A claim in `career_evidence.json` is usually one action. The situation it
addressed and the result it produced are recorded too, but as separate claims
about the same piece of work -- so a bullet that reads as situation, action and
result is those pieces put back together, not new material. That is the only
honest way to get STAR out of this evidence: of 24 Supreme Lending claims, four
carry a figure, and writing a result onto the other twenty would be exactly the
fabrication non-negotiable #1 exists to stop.

**The composed text is verified, not trusted.** Each bullet declares the claims
it draws on, and `load()` runs `overrides.verify_override` against the union of
those claims. A bullet that introduces a figure, a proper noun or a run of
substantive words the sources do not carry is dropped and reported, not served.
The check is the same one hand-written per-job overrides go through; a composed
bullet gets no more latitude for being longer.

The bullets themselves live in `~/careeros/star_bullets.json`, not here. This
repository is public and they are resume text -- the same boundary `.gitignore`
draws when it blocks `scripts/align_*.py`.

Selection is narrative first. Three bullets that each answer a different
requirement read as a list; three that open on a problem, say what was done and
close on what came of it read as a person who has done the work. Relevance to
the posting still decides *which* candidates compete, it just no longer decides
the order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import CAREEROS_DIR

STAR_PATH = Path(CAREEROS_DIR) / "star_bullets.json"

# The order three bullets are presented in, when the arcs are available.
ARC_ORDER = {"situation": 0, "action": 1, "result": 2}


@dataclass(frozen=True)
class StarBullet:
    """One composed bullet, and the claims it is answerable to."""

    id: str
    employer: str
    text: str
    arc: str
    themes: tuple[str, ...]
    source_claims: tuple[str, ...]
    # Populated when a bullet fails containment, so the caller can report why
    # rather than silently serving fewer bullets.
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "arc": self.arc,
            "sourceClaims": list(self.source_claims),
            "evidence": {
                "source": "composed from " + ", ".join(self.source_claims),
                "verifiedStatement": self.text,
            },
        }


def _claims_by_id(profile: Any) -> dict[str, Any]:
    return {c.claim_id: c for c in (profile.evidence or [])}


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    """The authored file, or an empty bank if it is not present.

    Absent is a normal state: the file is candidate data and lives outside this
    repository, so a checkout without it must still tailor resumes rather than
    fail to start.
    """
    if not STAR_PATH.exists():
        return {}
    try:
        return json.loads(STAR_PATH.read_text()).get("roles", {})
    except (json.JSONDecodeError, OSError):
        return {}


def load(profile: Any) -> tuple[list[StarBullet], list[StarBullet]]:
    """Every composed bullet, split into those that pass containment and those
    that do not. Rejections are returned rather than dropped: a bullet failing
    its own sources is a signal that the claim was edited underneath it, and
    swallowing that would let the resume drift from the evidence silently."""
    from .overrides import verify_override

    known = _claims_by_id(profile)
    good: list[StarBullet] = []
    bad: list[StarBullet] = []

    for employer, entries in _raw().items():
        for entry in entries:
            ids = tuple(entry.get("sourceClaims") or ())
            missing = [i for i in ids if i not in known]
            text = entry.get("text", "")

            if missing:
                problems = (f"source claims not in the evidence file: {', '.join(missing)}",)
            else:
                # Containment is checked against everything the bullet draws on,
                # so a figure is allowed only if one of its own sources states
                # it. Concatenating the claims is what makes a multi-source
                # composition checkable by a single-source rule.
                sources = " ".join(known[i].claim for i in ids)
                problems = tuple(verify_override(sources, text))

            bullet = StarBullet(
                id=entry.get("id", ""),
                employer=employer,
                text=text,
                arc=entry.get("arc", "action"),
                themes=tuple(t.lower() for t in entry.get("themes") or ()),
                source_claims=ids,
                problems=problems,
            )
            (good if bullet.ok else bad).append(bullet)

    return good, bad


def _relevance(bullet: StarBullet, wanted: set[str], description: str) -> int:
    """How much of what this posting asks for the bullet actually speaks to."""
    score = 0
    text = bullet.text.lower()
    for want in wanted:
        w = want.lower()
        if w in bullet.themes:
            score += 4
        elif any(w in theme or theme in w for theme in bullet.themes):
            score += 2
        elif w in text:
            score += 1
    # The posting's own words, for the specifics no requirement label carries.
    # "Ginnie Mae" never appears as an extracted requirement, so on a mortgage
    # posting the pool-delivery bullet would otherwise tie with everything else.
    body = (description or "").lower()
    if body:
        score += sum(1 for theme in bullet.themes if len(theme) > 3 and theme in body)
    return score


def select(
    employer: str,
    profile: Any,
    wanted: list[str],
    description: str = "",
    limit: int = 3,
) -> list[StarBullet]:
    """The bullets for one role on one posting, ordered as a story.

    Relevance decides which bullets compete. The arc decides the order they are
    read in, because three bullets that open on a problem and close on an
    outcome carry more than three independently strong ones. Where two bullets
    share an arc the more relevant leads.
    """
    good, _ = load(profile)
    mine = [b for b in good if b.employer == employer]
    if not mine:
        return []

    ranked = sorted(
        mine,
        key=lambda b: (-_relevance(b, set(wanted), description), b.id),
    )
    chosen = ranked[:limit]

    # Narrative order, relevance breaking ties within an arc. Bullets keep their
    # relevance rank as the secondary key so this never reshuffles arbitrarily.
    rank = {b.id: i for i, b in enumerate(ranked)}
    return sorted(chosen, key=lambda b: (ARC_ORDER.get(b.arc, 1), rank[b.id]))
