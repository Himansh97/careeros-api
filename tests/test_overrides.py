"""Containment tests for hand-tailored bullet overrides.

An override is the one place in the pipeline where wording is written by hand
rather than derived from the evidence file, so it is also the one place where
fabrication could enter a resume. `verify_override` is the gate. These tests
pin the four things it must never let through, and the two legitimate kinds of
rewrite it must not block.

**The fixtures are synthetic, and must stay that way.** They used to be verbatim
claims lifted out of `career_evidence.json` — which `careeros` gitignores
precisely because it is personal data — sitting in a file that is tracked and
pushed. That breaks non-negotiable #3 in AGENTS.md: resume text must not be
inlined in Python. What these tests actually exercise is *structure* — a figure,
a hyphenated compound, a capitalised tool name, a spelled numeral, roughly
thirty words — and structure is reproducible without anyone's real history. The
claims below describe a fictional logistics analyst and carry every property the
gate is being tested against.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.overrides import verify_override  # noqa: E402

CLAIM = (
    "Coordinated cross-functional planning reviews across 20+ distribution depots, "
    "gathering requirements and consolidating fragmented shipment records into a "
    "unified SQL-to-Power BI reporting layer, reducing planning cycle time by 40%."
)


def rejected(rewrite: str, claim: str = CLAIM) -> bool:
    return bool(verify_override(claim, rewrite))


def test_rejects_inflated_figures():
    assert rejected(
        "Gathered planning requirements across 50+ distribution depots and built Power BI "
        "dashboards, reducing planning cycle time by 40%."
    )


def test_rejects_invented_tools():
    assert rejected(
        "Gathered planning requirements across 20+ distribution depots and built Tableau "
        "dashboards on a SQL data layer, reducing cycle time by 40%."
    )


def test_rejects_padding():
    assert rejected(
        "Gathered planning requirements across 20+ distribution depots and built Power BI "
        "dashboards and reports on a unified SQL layer, reducing planning cycle "
        "time by 40% while owning the analytics roadmap, chairing the governance "
        "council, and managing a team of analysts across three time zones."
    )


def test_rejects_new_outcome_metric():
    assert rejected(
        "Gathered planning requirements across 20+ distribution depots and built Power BI "
        "dashboards, cutting cycle time 40% and raising adoption 85%."
    )


def test_allows_reordering_to_lead_with_jd_language():
    """The whole point of an override: same facts, posting's emphasis."""
    assert not rejected(
        "Gathered planning requirements across 20+ distribution depots and consolidated "
        "fragmented shipment records into a unified SQL-to-Power BI reporting layer, "
        "reducing planning cycle time by 40%."
    )


def test_allows_changing_the_opening_verb():
    """A capitalised first word is positional, not a proper noun."""
    claim = (
        "Applied regression models and feature engineering to build a standardized "
        "data-cleansing routine across three inconsistent depot feeds, improving "
        "data quality by 70%."
    )
    assert not rejected(
        "Standardized data quality and cleansing checks across three inconsistent "
        "depot feeds, using regression models and feature engineering to improve "
        "data quality by 70%.",
        claim=claim,
    )


def test_dropping_detail_is_allowed():
    """A rewrite may omit facts; it may only never add them."""
    assert not rejected(
        "Gathered planning requirements across 20+ distribution depots and built Power BI "
        "reports on a SQL layer."
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError:
                failures += 1
                print(f"FAIL {name}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
