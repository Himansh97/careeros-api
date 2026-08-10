"""Every role must stay substantiated, and tailoring must survive that.

Relevance ranking alone once handed 5 bullets to the current job and 1 each to
the older ones, so those positions read as filler. The floor in _select_bullets
prevents that — but a floor set too high would flatten every resume into the
same document, which is the opposite failure. Both directions are asserted here.
"""
from __future__ import annotations

import inspect
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.profile import load_profile  # noqa: E402
from app.scoring import score_job  # noqa: E402
from app.tailor import _select_bullets, tailor_resume  # noqa: E402

# Read the real defaults rather than restating them, so tuning the layout can't
# leave the test asserting a shape the code no longer aims for.
_DEFAULTS = {
    name: param.default
    for name, param in inspect.signature(_select_bullets).parameters.items()
    if param.default is not inspect.Parameter.empty
}
FLOOR = _DEFAULTS["floor"]
CAP_PER_ROLE = _DEFAULTS["cap_per_role"]

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

        # Assert the selection RULE, not a fixed shape. An earlier version
        # pinned the spread, which failed the moment the budget grew for a
        # two-page resume — even though 7/2/3/2 was correct there: the thin
        # roles were showing every claim they have, not being starved.
        available = {
            s["employer"]: sum(
                1 for c in profile.evidence
                if c.employer == s["employer"]
                and c.approved_for_resume
                and c.classification != "LEARNED_OR_ACADEMIC"
            )
            for s in resume["sections"]
        }

        for i, section in enumerate(resume["sections"]):
            kept = len(section["bullets"])
            have = available[section["employer"]]
            if kept < 1:
                failures.append(f"[{jid}] {section['employer']} kept no bullets")
            # A role may fall short of the floor only by running out of evidence.
            if kept < min(FLOOR, have) and i < 2:
                failures.append(
                    f"[{jid}] {section['employer']} kept {kept} of {have} available "
                    f"— starved below the floor: {counts}"
                )
            # And no role may run away with the page. This is the guard against
            # the original 5/1/1/2 defect, expressed as the cap that prevents it.
            if kept > FLOOR + CAP_PER_ROLE:
                failures.append(
                    f"[{jid}] {section['employer']} took {kept} bullets, "
                    f"above floor+cap ({FLOOR}+{CAP_PER_ROLE}): {counts}"
                )

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
