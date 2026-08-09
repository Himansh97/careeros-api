"""Containment tests for hand-tailored bullet overrides.

An override is the one place in the pipeline where wording is written by hand
rather than derived from the evidence file, so it is also the one place where
fabrication could enter a resume. `verify_override` is the gate. These tests
pin the four things it must never let through, and the two legitimate kinds of
rewrite it must not block.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.overrides import verify_override  # noqa: E402

CLAIM = (
    "Coordinated cross-functional analytics engagements across 20+ regional markets, "
    "gathering requirements and consolidating fragmented data into a unified "
    "SQL-to-Power BI reporting infrastructure, reducing cycle time by 40%."
)


def rejected(rewrite: str, claim: str = CLAIM) -> bool:
    return bool(verify_override(claim, rewrite))


def test_rejects_inflated_figures():
    assert rejected(
        "Gathered business requirements across 50+ regional markets and built Power BI "
        "dashboards, reducing reporting cycle time by 40%."
    )


def test_rejects_invented_tools():
    assert rejected(
        "Gathered business requirements across 20+ regional markets and built Tableau "
        "dashboards on a SQL data layer, reducing cycle time by 40%."
    )


def test_rejects_padding():
    assert rejected(
        "Gathered business requirements across 20+ regional markets and built Power BI "
        "dashboards and reports on a unified SQL data layer, reducing reporting cycle "
        "time by 40% while owning the analytics roadmap, chairing the governance "
        "council, and managing a team of analysts across three time zones."
    )


def test_rejects_new_outcome_metric():
    assert rejected(
        "Gathered business requirements across 20+ regional markets and built Power BI "
        "dashboards, cutting cycle time 40% and raising adoption 85%."
    )


def test_allows_reordering_to_lead_with_jd_language():
    """The whole point of an override: same facts, posting's emphasis."""
    assert not rejected(
        "Gathered business requirements across 20+ regional markets and built Power BI "
        "dashboards and reports on a unified SQL data layer, reducing reporting cycle "
        "time by 40%."
    )


def test_allows_changing_the_opening_verb():
    """A capitalised first word is positional, not a proper noun."""
    claim = (
        "Used statistical modeling and feature engineering to build a standardized "
        "data-cleansing pipeline across three inconsistent source systems, improving "
        "data quality by 70%."
    )
    assert not rejected(
        "Standardized data quality and cleansing checks across three inconsistent "
        "source systems, using statistical modeling and feature engineering to improve "
        "data quality by 70%.",
        claim=claim,
    )


def test_dropping_detail_is_allowed():
    """A rewrite may omit facts; it may only never add them."""
    assert not rejected(
        "Gathered business requirements across 20+ regional markets and built Power BI "
        "reports on a SQL data layer."
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
