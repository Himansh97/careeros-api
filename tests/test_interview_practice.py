"""Practising an answer must never call the candidate a liar.

This is the one place in the system where the *candidate* is the author, and
`overrides.py` already settled what that means: their own history is theirs, and
a figure the evidence file cannot find is a gap in the file, not a fabrication.
Everywhere else an unbacked number is a rejection that discards the whole
generation. Here it is a note with an offer to record it.

Both halves of that have to hold, and they pull against each other:

* a figure the evidence *does* back must be traced to the claim that backs it,
  or the check is decorative
* a figure it does not back must be reported as **unverified**, never as false,
  and must never block anything

Synthetic claims throughout. The real evidence file is gitignored personal data
and must not be inlined here (AGENTS.md non-negotiable 3), which also means
these tests can run in CI where that file does not exist.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.interview_practice import (  # noqa: E402
    BEHAVIOURAL,
    TARGET_SECONDS,
    check_answer,
)
from app.profile import EvidenceClaim  # noqa: E402


def _claim(**over) -> EvidenceClaim:
    base = {
        "claim_id": "acme-01",
        "employer": "Acme Logistics",
        "claim": "Built a reporting layer across 20+ regional markets, cutting "
                 "reporting cycle time by 40%.",
        "skills": ["SQL", "Power BI"],
        "industry": "logistics",
        "date_range": "2022-2023",
        "classification": "PRESENT_AND_EXPLICIT",
        "approved_for_resume": True,
        "source": "self-reported",
        "project": "",
    }
    base.update(over)
    return EvidenceClaim(**base)


class _Profile:
    """The two attributes check_answer reads, and nothing else."""

    def __init__(self, claims, name="Dana Whitfield", location="Dallas, TX"):
        self.evidence = claims
        self.name = name
        self.location = location
        self.all_skills = {"SQL", "Power BI", "Python"}


def _profile(*claims) -> _Profile:
    return _Profile(list(claims) or [_claim()])


# ----------------------------------------------------------------- figures


def test_a_figure_the_evidence_backs_is_traced_to_its_claim() -> None:
    out = check_answer("I cut reporting cycle time by 40%.", _profile())
    assert out["unverifiedFigures"] == []
    assert len(out["backedFigures"]) == 1
    backed = out["backedFigures"][0]
    assert backed["figure"] == "40%"
    assert backed["claimId"] == "acme-01"
    assert backed["employer"] == "Acme Logistics", "a match must name who backs it"


def test_a_figure_nothing_backs_is_unverified_not_rejected() -> None:
    """The whole asymmetry. This must be reported, and must not block."""
    out = check_answer("I cut reporting time by 85%.", _profile())
    assert out["unverifiedFigures"] == ["85%"]
    assert "rejections" not in out, "practice must not produce rejections"
    assert "verdict" not in out, "the evidence check does not judge the candidate"


def test_both_kinds_of_figure_can_appear_in_one_answer() -> None:
    out = check_answer(
        "Across 20+ markets I cut cycle time 40%, and later by 85% elsewhere.",
        _profile(),
    )
    assert {b["figure"] for b in out["backedFigures"]} == {"20+", "40%"}
    assert out["unverifiedFigures"] == ["85%"]


def test_a_figure_from_the_date_range_is_backed() -> None:
    """Saying when the work happened is not a fabricated statistic — the year
    lives in date_range, not in the claim sentence."""
    out = check_answer("I did that work in 2022.", _profile())
    assert out["unverifiedFigures"] == [], out["unverifiedFigures"]


def test_an_unapproved_claim_cannot_back_a_figure() -> None:
    """Evidence held back from the resume is held back here too, or the gate
    means something different in two places."""
    profile = _profile(_claim(approved_for_resume=False))
    out = check_answer("I cut reporting cycle time by 40%.", profile)
    assert out["unverifiedFigures"] == ["40%"]


def test_a_repeated_figure_is_reported_once() -> None:
    out = check_answer("It was 85% faster. Really, 85% faster.", _profile())
    assert out["unverifiedFigures"] == ["85%"]


# ------------------------------------------------------------- named things


def test_naming_your_own_employer_is_not_invention() -> None:
    out = check_answer("At Acme Logistics I owned the reporting layer.", _profile())
    assert out["unsourcedNames"] == []


def test_naming_a_tool_from_the_claim_is_not_invention() -> None:
    out = check_answer("I built it in Power BI on top of SQL.", _profile())
    assert out["unsourcedNames"] == []


def test_a_system_no_claim_mentions_is_flagged() -> None:
    out = check_answer("At Acme I ran the Snowflake migration.", _profile())
    assert "Snowflake" in out["unsourcedNames"]


def test_the_candidates_own_name_and_city_are_allowed() -> None:
    out = check_answer("Dana Whitfield, based in Dallas, owned it.", _profile())
    assert out["unsourcedNames"] == []


def test_the_interviewers_company_can_be_allowed_explicitly() -> None:
    """A behavioural answer names the company being interviewed at constantly."""
    bare = check_answer("This is why I applied to Stripe.", _profile())
    assert "Stripe" in bare["unsourcedNames"]
    allowed = check_answer(
        "This is why I applied to Stripe.", _profile(), allowed=frozenset({"stripe"})
    )
    assert allowed["unsourcedNames"] == []


# ----------------------------------------------------------------- delivery


def test_a_spoken_duration_is_used_over_the_estimate() -> None:
    out = check_answer("Three words only", _profile(), duration_s=88)
    assert out["seconds"] == 88.0
    assert out["length"] == "good"


def test_a_typed_answer_is_estimated_from_word_count() -> None:
    out = check_answer(" ".join(["word"] * 140), _profile())
    assert 55 <= out["seconds"] <= 65, out["seconds"]


def test_length_is_judged_against_the_target_band() -> None:
    assert check_answer("short", _profile(), duration_s=20)["length"] == "short"
    assert check_answer("fine", _profile(), duration_s=80)["length"] == "good"
    assert check_answer("rambling", _profile(), duration_s=200)["length"] == "long"
    assert TARGET_SECONDS[0] < TARGET_SECONDS[1]


def test_filler_words_survive_punctuation() -> None:
    """" um " never matched "So, um, at Acme" because of the comma, so an answer
    visibly full of fillers counted zero."""
    out = check_answer("So, um, at Acme I basically owned it, you know.", _profile())
    assert out["fillerCount"] >= 3, out["fillerWords"]
    assert "um" in out["fillerWords"]
    assert "you know" in out["fillerWords"]


def test_a_clean_answer_reports_no_fillers() -> None:
    out = check_answer("At Acme Logistics I owned the reporting layer.", _profile())
    assert out["fillerCount"] == 0


# ----------------------------------------------------------------- questions


def test_every_behavioural_question_is_distinct_and_complete() -> None:
    ids = [q["id"] for q in BEHAVIOURAL]
    assert len(ids) == len(set(ids)), "duplicate question id"
    for q in BEHAVIOURAL:
        assert q["prompt"].strip() and q["competency"].strip()


def test_an_empty_answer_does_not_crash() -> None:
    out = check_answer("", _profile())
    assert out["backedFigures"] == []
    assert out["unverifiedFigures"] == []
    assert out["words"] == 0


# --------------------------------------------------------- researched shape


def test_research_without_sources_is_refused() -> None:
    """The shape is the one thing here not drawn from the candidate's own
    evidence, so it has to carry its provenance. Advice with no source cannot be
    told apart from advice a model invented, and the whole design rests on being
    able to tell those apart. Same rule as interview_intel.save_intel."""
    import tempfile
    from unittest.mock import patch

    from app import store
    from app.db import initialize
    from app.interview_practice import get_research, save_research

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(store, "DB_PATH", pathlib.Path(tmp) / "t.db"):
            initialize(path=store.DB_PATH)
            try:
                save_research("beh-failure", {"assesses": "x"}, [])
            except ValueError as exc:
                assert "sources" in str(exc)
            else:
                raise AssertionError("research stored with no sources")

            # And with sources it round-trips, provenance intact.
            save_research(
                "beh-failure",
                {"assesses": "accountability", "traps": ["blaming others"]},
                [{"title": "HBR", "url": "https://hbr.org/example"}],
            )
            got = get_research("beh-failure")
            assert got["assesses"] == "accountability"
            assert got["sources"][0]["url"] == "https://hbr.org/example"
            assert got["researchedAt"]


def test_research_is_upserted_not_duplicated() -> None:
    import tempfile
    from unittest.mock import patch

    from app import store
    from app.db import initialize
    from app.interview_practice import get_research, save_research

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(store, "DB_PATH", pathlib.Path(tmp) / "t.db"):
            initialize(path=store.DB_PATH)
            src = [{"title": "a", "url": "https://a.test"}]
            save_research("beh-scale", {"assesses": "first"}, src)
            save_research("beh-scale", {"assesses": "second"}, src)
            assert get_research("beh-scale")["assesses"] == "second"


def test_missing_research_returns_none_rather_than_an_empty_shape() -> None:
    """An empty shape would render as a panel with nothing in it, which reads as
    'no advice' rather than 'not researched yet'."""
    import tempfile
    from unittest.mock import patch

    from app import store
    from app.db import initialize
    from app.interview_practice import get_research

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(store, "DB_PATH", pathlib.Path(tmp) / "t.db"):
            initialize(path=store.DB_PATH)
            assert get_research("beh-conflict") is None


def test_claim_selection_never_reaches_for_irrelevant_evidence() -> None:
    """A question with no matching evidence must return nothing, so the caller
    says so rather than drafting an answer out of whatever was nearest."""
    from app.interview_practice import BEHAVIOURAL, _claims_for

    unrelated = _claim(
        claim_id="x-01",
        claim="Won the office table tennis tournament.",
        skills=["table tennis"],
        employer="Acme Logistics",
    )
    question = next(q for q in BEHAVIOURAL if q["id"] == "beh-process")
    assert _claims_for(_Profile([unrelated]), question) == []


def test_claim_selection_excludes_unapproved_and_non_explicit_claims() -> None:
    from app.interview_practice import BEHAVIOURAL, _claims_for

    question = next(q for q in BEHAVIOURAL if q["id"] == "beh-process")
    assert _claims_for(_Profile([_claim(approved_for_resume=False)]), question) == []
    assert _claims_for(
        _Profile([_claim(classification="IN_PROGRESS_OR_DESIGNED")]), question
    ) == []


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
