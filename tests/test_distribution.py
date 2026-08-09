"""Every role must stay substantiated, and tailoring must survive that.

Relevance ranking alone once handed 5 bullets to the current job and 1 each to
the older ones, so those positions read as filler. The floor in _select_bullets
prevents that — but a floor set too high would flatten every resume into the
same document, which is the opposite failure. Both directions are asserted here.
"""
from __future__ import annotations

import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.profile import load_profile  # noqa: E402
from app.scoring import score_job  # noqa: E402
from app.tailor import tailor_resume  # noqa: E402

FIXTURES = [
    ("bi-heavy", "Seeking a BI Analyst. Required: SQL, Power BI, Tableau, "
     "dashboarding, reporting, requirements gathering. Preferred: stakeholder "
     "management, data quality."),
    ("ml-heavy", "Data Scientist. Required: Python, statistical modeling, "
     "hypothesis testing, regression analysis, machine learning. Preferred: "
     "PySpark, Airflow, feature engineering."),
    ("pm-heavy", "Analytics Delivery Manager. Required: stakeholder management, "
     "requirements gathering, Agile, cross-functional coordination. Preferred: "
     "mentoring, root cause analysis."),
    ("eng-heavy", "Analytics Engineer. Required: data pipelines, ETL, SQL, "
     "PySpark, CI/CD. Preferred: Docker, data quality, Airflow."),
]


def _job(jid: str, description: str) -> dict:
    return {
        "id": jid,
        "title": "Analyst",
        "company": {"id": "c", "name": "Fixture Co"},
        "location": "Remote",
        "description": description,
        "applyUrl": "https://example.com",
    }


def main() -> int:
    profile = load_profile()
    failures: list[str] = []
    fingerprints: dict[str, tuple] = {}

    for jid, description in FIXTURES:
        job = _job(jid, description)
        resume = tailor_resume(job, score_job(job, profile), profile)
        counts = [len(s["bullets"]) for s in resume["sections"]]

        # Every role a resume shows must justify its place on the page.
        if any(c < 2 for c in counts):
            failures.append(f"[{jid}] a role kept fewer than 2 bullets: {counts}")

        # No role may dominate: the gap between the fullest and thinnest role
        # is what made the earlier resumes look lopsided.
        if counts and max(counts) - min(counts) > 2:
            failures.append(f"[{jid}] uneven spread {counts}")

        # Roles stay reverse-chronological regardless of relevance.
        if resume["sections"][0]["employer"] != "Supreme Lending (Everett Financial, Inc.)":
            failures.append(f"[{jid}] newest role is not first")

        fingerprints[jid] = tuple(
            b["text"] for s in resume["sections"] for b in s["bullets"]
        )

    # Tailoring must still differentiate: four postings this dissimilar should
    # not all produce the same document.
    if len(set(fingerprints.values())) < 3:
        failures.append(
            f"only {len(set(fingerprints.values()))} distinct resumes across "
            f"{len(FIXTURES)} very different postings — tailoring collapsed"
        )

    for f in failures:
        print(f"FAIL {f}")
    if not failures:
        for jid in fingerprints:
            print(f"PASS {jid}")
        print(f"PASS {len(set(fingerprints.values()))} distinct resumes from {len(FIXTURES)} postings")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
