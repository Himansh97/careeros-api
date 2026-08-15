"""The resume audit must actually vary with the resume.

Five of the eight audit categories were constants in practice, worth 55 of the
100 points:

* `ats_structure` — literal 1.0
* `education` — literal 1.0
* `keyword_alignment` — the identical expression to `requirement_coverage`
* `relevant_experience` — `pct(bullets, 6)` against a selection budget of 16,
  so it clamped to 1.0 on every resume ever generated
* `achievements` — `pct(quantified, bullets * 0.5)`, satisfied whenever half
  the bullets carried a figure, which every resume managed

The symptom was a pipeline where 20 of 38 applications scored at or above the
SHORTLIST threshold and 11 submissions produced no responses. A score that
cannot go down is not a score, and it cannot tell you whether a change to the
writing helped.

This pins the property rather than the numbers: across dissimilar postings the
audit must produce a range, and the categories that can vary must.

    ./.venv/bin/python tests/test_audit_discrimination.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.tailor import _education_fit, _keyword_alignment  # noqa: E402


def test_education_scores_a_stated_requirement() -> None:
    """A posting that asks for nothing cannot be failed on education."""
    profile = _FakeProfile([{"degree": "Master of Science in Business Analytics"}])
    assert _education_fit({"description": "no schooling mentioned"}, profile) == 1.0


def test_education_credits_holding_more_than_asked() -> None:
    profile = _FakeProfile([{"degree": "Master of Science in Business Analytics"}])
    got = _education_fit({"description": "Bachelor's degree required"}, profile)
    assert got == 1.0, got


def test_education_marks_down_a_shortfall() -> None:
    """A doctorate requirement against a master's is a real gap, not full marks."""
    profile = _FakeProfile([{"degree": "Master of Science in Business Analytics"}])
    got = _education_fit({"description": "PhD required for this role"}, profile)
    assert got < 1.0, got


def test_keyword_alignment_is_not_requirement_coverage() -> None:
    """Evidence for a requirement and naming it are different things.

    This is the whole reason the category exists as its own measure: a bullet
    can be real evidence for "data integration" and never contain the phrase,
    which a keyword-ranking screener scores as absent.
    """
    score = {"requirements": [{"label": "data integration", "match": "exact"}]}
    sections = [{"bullets": [{"text": "Consolidated fragmented records across depots."}]}]
    assert _keyword_alignment(score, sections, "") == 0.0

    sections_named = [{"bullets": [{"text": "Owned data integration across depots."}]}]
    assert _keyword_alignment(score, sections_named, "") == 1.0


def test_keyword_alignment_ignores_unbacked_requirements() -> None:
    """Naming something you cannot support must not earn points — that is
    keyword stuffing, which modern screeners flag as manipulative."""
    score = {"requirements": [{"label": "kubernetes", "match": "gap"}]}
    sections = [{"bullets": [{"text": "Ran kubernetes clusters in production."}]}]
    # No backed requirements at all -> vacuously full marks, not credit for the gap.
    assert _keyword_alignment(score, sections, "") == 1.0


def test_keyword_alignment_uses_word_boundaries() -> None:
    """Substring matching would credit 'R' against 'reporting'."""
    score = {"requirements": [{"label": "r", "match": "exact"}]}
    sections = [{"bullets": [{"text": "Built reporting infrastructure."}]}]
    assert _keyword_alignment(score, sections, "") == 0.0


class _FakeProfile:
    """Minimal stand-in — `_education_fit` reads only `education`."""

    def __init__(self, education: list[dict]) -> None:
        self.education = education


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
