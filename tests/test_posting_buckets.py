"""A posting says more than it screens on, and matching must not invent skills.

Two things are tested here.

**Word-boundary extraction.** `extract_requirements` counted aliases with
`str.count`, so matching was plain substring — the exact failure
`scoring._contains` exists to prevent, left in place on the extraction side.
Across 800 live postings that pulled "Excel" out of "excellent communication
skills", "Hive" out of "archive" and "AWS" out of "laws", then scored the
candidate against requirements no employer had stated. A strict `\\b` boundary
would over-correct and lose "dashboards" and "forecasting", which are real
mentions, so ordinary inflection is allowed and arbitrary prefixes are not.

**Three buckets.** A posting contains requirements, padding, and language that
carries no requirement at all. Showing them separately is the difference
between "you meet 5 of 11 things this posting lists" and "you meet 5 of 6
things it screens on" — and a candidate who reads "5+ years" as a wall
withdraws from roles they can do.

    ./.venv/bin/python tests/test_posting_buckets.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.skills import classify_posting, extract_requirements  # noqa: E402

# (text, skill, should_be_found)
MATCHING = [
    # Prefix and suffix collisions — the bug.
    ("Excellent communication skills are essential.", "Excel", False),
    ("Data is kept in an archive for seven years.", "Hive", False),
    ("You must comply with all applicable laws.", "AWS", False),
    ("Closing the books each month.", "Loan origination system (LOS)", False),
    # Genuine mentions, including ordinary inflection.
    ("We use Excel daily for reconciliation.", "Excel", True),
    ("Build dashboards for the leadership team.", "Dashboarding", True),
    ("Forecasting monthly revenue.", "Forecasting", True),
    ("Experience auditing financial controls.", "Audit", True),
    ("Deploy services on AWS.", "AWS", True),
    ("Maintain data pipelines in production.", "Data pipelines", True),
]

POSTING = """
About the team. We are a fast-paced environment and you will wear many hats.

What you'll do:
- Build and maintain data pipelines
- Requirements: 7+ years of experience in analytics
- Strong SQL and Python are required
- Must have experience with Tableau

Nice to have:
- Exposure to Snowflake

About you:
- Excellent communication skills, a self-starter with a can-do attitude
- Detail oriented and thrives in ambiguity
"""


def main() -> int:
    failures: list[str] = []

    for text, skill, want in MATCHING:
        found = skill in [s for s, _ in extract_requirements(text)]
        if found != want:
            verb = "invented" if found else "missed"
            failures.append(f"{verb} {skill!r} in {text!r}")
    if not failures:
        print(f"PASS {len(MATCHING)} matching cases (no invented skills, no lost inflections)")

    result = classify_posting(POSTING, "Data Analyst")

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    check("years-of-experience is captured", result["yearsRequested"], 7)

    # The filler bucket must catch the mood language, and nothing else.
    for phrase in ("fast-paced environment", "wear many hats", "self-starter",
                   "can-do attitude", "thrives in ambiguity"):
        if phrase not in result["filler"]:
            failures.append(f"filler bucket missed {phrase!r}")
    if not any(f in result["filler"] for f in ("fast-paced environment",)):
        pass
    else:
        print(f"PASS filler bucket caught {len(result['filler'])} mood phrases")

    # Real requirements must not leak into filler, and vice versa.
    overlap = set(result["filler"]) & set(result["required"] + result["preferred"])
    check("no phrase is both a requirement and filler", overlap, set())

    for skill in ("SQL", "Python", "Tableau"):
        if skill not in result["required"] + result["preferred"]:
            failures.append(f"{skill} should be screened on")
    if not failures:
        print("PASS stated technologies land in the screened-on buckets")

    # "Excellent communication skills" must not produce Excel here either.
    check("filler text does not create a skill",
          "Excel" in result["required"] + result["preferred"], False)

    check("screenedOn counts only real requirements",
          result["screenedOn"],
          len(result["required"]) + len(result["preferred"]))

    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
