"""Achievements must count the figures the resume already states.

The audit's quantification test was
`\\d+\\s*%|\\b\\d[\\d,]{2,}\\+?\\b|\\b\\d+\\+?\\s+(?:markets|accounts|records)`.
It demanded three digits unless the noun happened to be one of three hardcoded
words, so "processing 1M+ records", "20+ regional markets" and "six failed
deployments and four distinct root causes" all read as unquantified.

The signature was unmistakable: Achievements scored exactly 7/10 on every
resume in the pipeline, and Readability exactly 9/10 — a fixed deduction on
every document is a broken measure, not a consistent weakness. Both are tested
here so neither can silently regress.

False positives matter more than false negatives. A version string is not an
achievement, and counting one would inflate a score against no evidence —
which is the failure this whole system exists to avoid.

    ./.venv/bin/python tests/test_quantification.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.tailor import (  # noqa: E402
    MAX_BULLETS_PER_ROLE,
    TARGET_SCORE,
    _readability,
    _select_bullets,
    is_quantified,
)

QUANTIFIED = [
    "Reduced turnaround by 40%",
    "Cut reconciliation time by 12.5 %",
    "Ran ETL pipelines processing 1M+ records with data-quality checks",
    "Consolidated fragmented data across 20+ regional markets",
    "Processed 1,200 loan files a month",
    "Recovered $2M in mis-posted loan volume",
    "Saved 12 hours per week of manual reconciliation",
    "Delivered 3x faster reconciliation for the servicing team",
    "Diagnosed a production issue spanning six failed deployments",
    "Traced four distinct root causes across the pipeline",
]

NOT_QUANTIFIED = [
    # Version strings — the dangerous case. These carry digits and mean nothing
    # about outcomes.
    "Integrated Encompass Developer Connect V3 REST APIs for loan data",
    "Built the service on Python 3 and Airflow",
    "Migrated to Spark 4 for the nightly load",
    # Ordinary unquantified work.
    "Gathered requirements from accounting stakeholders",
    "Presented technical findings to client-facing business stakeholders",
    "Mentored junior analysts on data-quality best practices",
    "Partnered with cross-functional teams on delivery planning",
    # A count reference rather than a count.
    "Reviewed three of the regional dashboards",
]


def main() -> int:
    failures: list[str] = []

    for text in QUANTIFIED:
        if not is_quantified(text):
            failures.append(f"missed a real figure: {text!r}")
    for text in NOT_QUANTIFIED:
        if is_quantified(text):
            failures.append(f"counted a non-achievement: {text!r}")

    if not failures:
        print(f"PASS {len(QUANTIFIED)} quantified bullets detected")
        print(f"PASS {len(NOT_QUANTIFIED)} non-achievements correctly ignored")

    # Readability deducted on every resume because a role could hold
    # floor(3) + cap_per_role(4) = 7 bullets, one over the penalty threshold.
    sections = [
        {
            "company": f"Role {i}",
            "bullets": [
                {"text": f"Bullet {i}-{n} delivering work", "hits": [f"r{n}"]}
                for n in range(10)
            ],
        }
        for i in range(4)
    ]
    _select_bullets(sections)
    worst = max(len(s["bullets"]) for s in sections)
    if worst > MAX_BULLETS_PER_ROLE:
        failures.append(
            f"a role kept {worst} bullets, over the {MAX_BULLETS_PER_ROLE} "
            "readability threshold"
        )
    else:
        print(f"PASS no role exceeds {MAX_BULLETS_PER_ROLE} bullets (worst: {worst})")

    if _readability(sections) < 1.0:
        failures.append(
            f"selection still produces a readability penalty ({_readability(sections)})"
        )
    else:
        print("PASS selection leaves no readability penalty")

    # Ties should prefer the bullet that states an outcome. Both bullets below
    # cover the same requirement, so marginal gain cannot separate them.
    tie = [
        {
            "company": "Role",
            "bullets": [
                {"text": "Floor bullet one", "hits": ["a"]},
                {"text": "Floor bullet two", "hits": ["a"]},
                {"text": "Floor bullet three", "hits": ["a"]},
                {"text": "Improved a process for the team", "hits": ["z"]},
                {"text": "Improved a process by 40% for the team", "hits": ["z"]},
            ],
        }
    ]
    _select_bullets(tie, budget=4)
    kept = [b["text"] for b in tie[0]["bullets"]]
    if any("40%" in t for t in kept):
        print("PASS a tie prefers the bullet stating an outcome")
    else:
        failures.append(f"tie-break ignored the quantified bullet; kept {kept}")

    if TARGET_SCORE != 85:
        failures.append(f"TARGET_SCORE drifted to {TARGET_SCORE}")

    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
