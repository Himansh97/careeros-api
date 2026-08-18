"""The generated-writing layer, and the gate that decides what ships.

These run without an API key. Generation itself is exercised by hand against
real postings; what is pinned here is everything that decides whether a
generated message is allowed out, because that is the part where a mistake
reaches a stranger with the candidate's name on it.

The live gate has already earned its place: on the first Chime draft the model
wrote a 35% figure that appears in no claim, and the figure check discarded the
whole generation. That is the failure this module exists to make impossible.

Fixtures are synthetic. `career_evidence.json` is gitignored personal data and
must not be inlined in tests — the gate tests structure, not identity.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.compose import (  # noqa: E402
    BANNED_PHRASES,
    Composition,
    _drop_signoff,
    _prose_problems,
    check_style,
    select_evidence,
    verify_sentences,
)
from app.profile import EvidenceClaim  # noqa: E402


def _claim(**over) -> EvidenceClaim:
    base = dict(
        claim_id="acme-01",
        employer="Acme Logistics (Northwind Group, Inc.)",
        claim=(
            "Built a routing dashboard in Python and SQL covering 12 depots, "
            "cutting dispatch planning time by 30%."
        ),
        skills=["Python", "SQL", "dashboarding"],
        industry="logistics",
        date_range="2024-01 to 2024-12",
        classification="PRESENT_AND_EXPLICIT",
        approved_for_resume=True,
        source="test",
    )
    base.update(over)
    return EvidenceClaim(**base)


class _Profile:
    """Enough of CandidateProfile for the selector and the sign-off stripper."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.name = "Dana Whitfield"
        self.headline = "Analytics Engineer"
        self.location = "Austin, TX"
        self.work_authorization = "US citizen"
        self.phone = "+1 (555) 010-0000"
        self.email = "dana@example.com"
        self.linkedin_url = "https://www.linkedin.com/in/dana"


# ----------------------------------------------------------------- the gate


def test_an_invented_figure_is_rejected() -> None:
    """The live failure this was built for."""
    claim = _claim()
    rejects, _ = verify_sentences(
        [{"text": "The dashboard cut planning time by 35%.", "claim_id": "acme-01"}],
        {"acme-01": claim},
    )
    assert any("35%" in r for r in rejects), rejects


def test_a_figure_from_the_claim_survives() -> None:
    claim = _claim()
    rejects, _ = verify_sentences(
        [{"text": "It cut dispatch planning time by 30%.", "claim_id": "acme-01"}],
        {"acme-01": claim},
    )
    assert rejects == []


def test_naming_the_employer_is_not_invention() -> None:
    """"At Acme Logistics, I..." must pass.

    The resume-bullet gate rejected exactly this, because the employer appears
    in the claim's record rather than in its sentence, and because a sentence
    with a conversational lead-in is longer than the bullet it came from.
    """
    claim = _claim()
    rejects, _ = verify_sentences(
        [{
            "text": "At Acme Logistics (Northwind Group, Inc.), I built a routing "
                    "dashboard in Python and SQL covering 12 depots.",
            "claim_id": "acme-01",
        }],
        {"acme-01": claim},
    )
    assert rejects == [], rejects


def test_naming_the_recipients_company_is_not_invention() -> None:
    claim = _claim()
    rejects, _ = verify_sentences(
        [{"text": "That work at Acme is close to what Globex needs.", "claim_id": "acme-01"}],
        {"acme-01": claim},
        allowed=frozenset({"globex"}),
    )
    assert rejects == [], rejects


def test_an_unsourced_tool_is_rejected() -> None:
    claim = _claim()
    rejects, _ = verify_sentences(
        [{"text": "I rebuilt the pipeline in Snowflake and Databricks.", "claim_id": "acme-01"}],
        {"acme-01": claim},
    )
    assert rejects, "an invented tool passed the gate"


def test_a_factual_sentence_with_no_claim_is_rejected() -> None:
    """Unattributed fact is the fabrication route, so it is treated as one."""
    rejects, _ = verify_sentences(
        [{"text": "I have shipped 14 production systems.", "claim_id": ""}], {}
    )
    assert rejects


def test_a_non_factual_sentence_needs_no_claim() -> None:
    rejects, _ = verify_sentences(
        [{"text": "Would a short call make sense?", "claim_id": ""}], {}
    )
    assert rejects == []


def test_citing_a_claim_that_was_never_supplied_is_rejected() -> None:
    rejects, _ = verify_sentences(
        [{"text": "I cut planning time by 30%.", "claim_id": "ghost-99"}], {}
    )
    assert rejects


def test_trailing_punctuation_does_not_create_a_false_reject() -> None:
    """"Inc." from the employer field and "Inc.," in a sentence are one word."""
    assert _prose_problems(_claim(), "I worked at Northwind Group, Inc., on this.") == []


def test_a_date_from_the_claims_record_is_not_an_invented_figure() -> None:
    """The year lives in `date_range`, not in the claim sentence.

    Without this, "I built that in 2024" — true, and in the record — was
    reported as a fabricated statistic.
    """
    assert _prose_problems(_claim(), "I built the routing dashboard in 2024.") == []


def test_a_year_the_record_does_not_support_is_still_caught() -> None:
    assert _prose_problems(_claim(), "I built the routing dashboard in 2019.")


# ----------------------------------------------------------------- selection


def test_selection_returns_several_claims_not_one() -> None:
    """One claim was the old behaviour, and why nobody learned what was built."""
    claims = [
        _claim(claim_id="a", claim="Built a routing dashboard in Python.", skills=["Python"]),
        _claim(claim_id="b", claim="Ran ETL in Airflow across 4 systems.", skills=["Airflow"]),
        _claim(claim_id="c", claim="Led a team of 3 analysts.", skills=["leadership"]),
    ]
    score = {"strongMatches": ["Python", "Airflow", "leadership"], "partialMatches": []}
    picked = select_evidence(score, _Profile(claims))
    assert len(picked) >= 2, [c.claim_id for c in picked]


def test_unapproved_and_unproven_claims_are_never_selected() -> None:
    """A designed-but-not-delivered claim can be rewritten into an outcome using
    only words already present, so it is excluded before generation."""
    claims = [
        _claim(claim_id="ok", skills=["Python"]),
        _claim(claim_id="draft", skills=["Python"], classification="IN_PROGRESS_OR_DESIGNED"),
        _claim(claim_id="private", skills=["Python"], approved_for_resume=False),
    ]
    picked = select_evidence({"strongMatches": ["Python"], "partialMatches": []},
                             _Profile(claims))
    ids = {c.claim_id for c in picked}
    assert "draft" not in ids and "private" not in ids, ids


# -------------------------------------------------------------------- style


def test_template_phrasing_is_refused() -> None:
    """Including the phrase the old template used, which would otherwise be
    imitated straight back into the new output."""
    problems = check_style("hi", "I wanted to reach out directly.", has_attachment=False)
    assert problems


def test_every_banned_phrase_is_actually_caught() -> None:
    for phrase in BANNED_PHRASES:
        assert check_style("s", f"Some text {phrase} more text.", has_attachment=False), phrase


def test_promising_an_attachment_that_is_not_there_is_refused() -> None:
    """Seven real emails went out saying "Resume attached" with nothing attached."""
    assert check_style("s", "Resume attached.", has_attachment=False)
    assert check_style("s", "Please see the enclosed CV.", has_attachment=False)


def test_attachment_language_is_fine_when_one_is_attached() -> None:
    assert check_style("s", "Resume attached.", has_attachment=True) == []


# ------------------------------------------------------------------ shaping


def test_a_model_written_signoff_is_stripped() -> None:
    """Otherwise it sits directly above the real signature block."""
    profile = _Profile([])
    out = _drop_signoff(["Hi Alex,", "Some content.", "Best,\nDana"], profile)
    assert out == ["Hi Alex,", "Some content."]


def test_content_is_never_mistaken_for_a_signoff() -> None:
    profile = _Profile([])
    paragraphs = ["Hi Alex,", "Would a short call make sense?"]
    assert _drop_signoff(list(paragraphs), profile) == paragraphs


def test_the_opening_skips_the_greeting() -> None:
    """Comparing greetings would find a collision on every single message."""
    c = Composition(subject="s", body="Hi Alex,\n\nYour posting mentions X. More text.")
    assert c.opening == "Your posting mentions X."


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
