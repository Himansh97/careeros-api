"""Fabrications that a set-comparison gate cannot see.

`verify_override` compares token sets — numbers, capitals, novel words,
length. Three fabrications pass it cleanly, and they are exactly the three an
eager rewriter produces. All three were confirmed clean against the live gate
before this module existed:

    "Supported a migration..."       -> "Drove the migration..."
    "...reducing cycle time by 40%"  -> "...driving adoption by 40%"
    "...using Python and SQL"        -> "...using python, sql, dbt"

Seniority inflation uses only words already present. Metric re-attribution
leaves the token set identical. Lowercase tool names escape a capitals-only
check.

Fixtures are synthetic — see the note in tests/test_overrides.py. What is
under test is structure, and structure does not need anyone's real history.

    ./.venv/bin/python tests/test_containment.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.containment import (  # noqa: E402
    invented_tools,
    lead_verb,
    semantic_findings,
    seniority_escalation,
)
from app.overrides import verdict_for  # noqa: E402

CLAIM = (
    "Supported a data warehouse migration across three source systems, "
    "reducing cycle time by 40%."
)
CEILING = "support"


def _verdict(rewrite: str, claim: str = CLAIM, ceiling: str = CEILING) -> str:
    return verdict_for(semantic_findings(claim, rewrite, seniority_ceiling=ceiling))


# ── the three bypasses ───────────────────────────────────────────────────
def test_seniority_inflation_is_rejected():
    assert _verdict("Drove the data warehouse migration across three source "
                    "systems, reducing cycle time by 40%.") == "reject"


def test_two_tier_jump_is_rejected():
    assert _verdict("Built the data warehouse migration across three source "
                    "systems, reducing cycle time by 40%.") == "reject"


def test_one_tier_step_goes_to_review_not_reject():
    """The genuinely ambiguous case belongs to a human, not to a threshold."""
    findings = semantic_findings(
        "Built a reporting layer across three source systems.",
        "Led the reporting layer build across three source systems.",
        seniority_ceiling="built",
    )
    assert verdict_for(findings) == "review"


def test_metric_re_attribution_is_rejected():
    """The token set is unchanged; only what the number describes moved."""
    assert _verdict("Supported a data warehouse migration across three source "
                    "systems, driving adoption by 40%.") == "reject"


def test_lowercase_tool_invention_is_rejected():
    assert _verdict("Supported a data warehouse migration using python, dbt "
                    "and airflow, reducing cycle time by 40%.") == "reject"


def test_attribution_theft_is_rejected():
    """"supported the team that led X" must not licence "Led X"."""
    claim = "Supported the team that led the platform rebuild across three systems."
    assert _verdict("Led the platform rebuild across three systems.",
                    claim=claim, ceiling="support") == "reject"


# ── what must NOT be rejected ────────────────────────────────────────────
def test_identity_is_clean():
    assert _verdict(CLAIM) == "pass"


def test_dropping_detail_is_clean():
    assert _verdict("Supported a data warehouse migration, reducing cycle "
                    "time by 40%.") == "pass"


def test_same_tier_substitution_is_clean():
    """Re-leading with the posting's verb is the entire point of an override."""
    assert _verdict("Maintained a data warehouse migration across three source "
                    "systems, reducing cycle time by 40%.") == "pass"


def test_participle_is_not_an_authority_claim():
    """"driving a 40% reduction" is ordinary writing, not a claim of command.

    Scanning the whole sentence for ranked verbs flagged this as a four-tier
    escalation in a bullet that opened with "Supported". Only the leading verb
    sets seniority now.
    """
    findings = semantic_findings(
        CLAIM,
        "Supported a data warehouse migration across three source systems, "
        "driving a 40% cycle time reduction.",
        seniority_ceiling=CEILING,
    )
    assert not [f for f in findings if f.tier == "reject"], [f.detail for f in findings]


def test_practice_words_are_not_tools():
    """The vocabulary was derived from SKILL_ALIASES once, which mixes tools
    with practices — "forecast", "reports" and "mortgage" then rejected three
    of the candidate's own shipped bullets."""
    for word in ("forecast", "reports", "mortgage", "dashboarding", "reporting"):
        assert word not in invented_tools(CLAIM, CLAIM + f" and {word} work")


def test_real_tools_are_tools():
    assert "dbt" in invented_tools(CLAIM, CLAIM + " using dbt")
    assert "snowflake" in invented_tools(CLAIM, CLAIM + " using snowflake")


# ── mechanics ────────────────────────────────────────────────────────────
def test_lead_verb_reads_only_the_opening():
    assert lead_verb("Led the migration, supporting three teams") == "led"


def test_missing_ceiling_does_not_licence_escalation():
    """An unrecorded seniority_verb must not read as "anything goes"."""
    got = seniority_escalation("Supported a migration.", "Drove a migration.", "")
    assert got >= 2, got


def test_three_reviews_escalate_to_reject():
    """Accumulated doubt is one large doubt, mirroring the padding check."""
    from app.containment import Finding

    assert verdict_for([Finding("a", "review", ""), Finding("b", "review", "")]) == "review"
    assert verdict_for([Finding("a", "review", ""), Finding("b", "review", ""),
                        Finding("c", "review", "")]) == "reject"


def test_one_reject_beats_any_number_of_reviews():
    from app.containment import Finding

    assert verdict_for([Finding("a", "review", ""), Finding("b", "reject", "")]) == "reject"


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
