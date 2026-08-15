"""A headline may not position the candidate as something unevidenced.

`_headline` matched the posting title and returned a role-family label, while
its docstring claimed it "only ever narrows to a family the candidate has
genuine evidence in". It did not check the evidence file at all, so a posting
titled "ML Engineer" described the candidate as an "AI/ML Analyst" regardless
of what `career_evidence.json` contained.

The headline is the first line a recruiter reads. It is the worst place in the
document to assert something unbacked, and — unlike a bullet — it goes through
no override verifier at all.

Fixtures are synthetic, per the note in tests/test_overrides.py.

    ./.venv/bin/python tests/test_headline.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.profile import CandidateProfile, EvidenceClaim  # noqa: E402
from app.tailor import _headline  # noqa: E402


def _profile(claims: list[tuple[str, list[str]]]) -> CandidateProfile:
    """A profile carrying only the claims named, and nothing else."""
    return CandidateProfile(
        name="Test Candidate",
        email="test@example.com",
        phone="+1 (000) 000-0000",
        location="Nowhere, TX",
        linkedin_url="",
        work_authorization="",
        education=[],
        certifications=[],
        # Deliberately empty: this fixture isolates the evidence check, and a
        # populated inventory would satisfy `_find_evidence` through a
        # different branch and hide the thing under test.
        skills_inventory={},
        employment_history=[],
        headline="Business Analytics Consultant",
        evidence=[
            EvidenceClaim(
                claim_id=f"c{i}",
                employer="Depot Co",
                claim=text,
                skills=skills,
                industry="logistics",
                date_range="2020-01 to 2021-01",
                classification="PRESENT_AND_EXPLICIT",
                approved_for_resume=True,
                source="test",
            )
            for i, (text, skills) in enumerate(claims)
        ],
    )


ML_EVIDENCE = _profile([
    ("Built machine learning models in Python for demand forecasting.",
     ["machine learning", "Python"]),
])

NO_ML_EVIDENCE = _profile([
    ("Gathered requirements from finance stakeholders and built SQL reports.",
     ["SQL", "requirements gathering"]),
])


def test_positions_as_ml_when_evidenced():
    assert _headline({"title": "Machine Learning Engineer"}, ML_EVIDENCE) == "AI/ML Analyst"


def test_refuses_ml_positioning_without_evidence():
    """The regression this file exists for. A title alone must not decide it."""
    got = _headline({"title": "Machine Learning Engineer"}, NO_ML_EVIDENCE)
    assert got == "Business Analytics Consultant", got


def test_falls_back_rather_than_inventing_a_family():
    assert _headline({"title": "Quantum Cryptographer"}, ML_EVIDENCE) == (
        "Business Analytics Consultant"
    )


def test_unevidenced_family_does_not_leak_into_another():
    """Failing the ML check must fall back, not try the next family down."""
    got = _headline({"title": "ML Engineer"}, NO_ML_EVIDENCE)
    assert got != "Data Analyst", "fell through to a family the title never matched"


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
