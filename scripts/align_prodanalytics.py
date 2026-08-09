"""Hand-tailor the ProdAnalytics Business Analyst I resume to its JD.

The posting asks, in its own words, for: SQL querying, building dashboards
and reports, gathering business requirements from stakeholders, data quality
checks, and supporting analytics projects.

Each rewrite below re-leads a bullet with whichever of those the underlying
claim genuinely demonstrates. Nothing is added: every figure, employer, tool
and outcome already appears in the claim, which `verify_override` enforces
before anything is stored.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.overrides import clear_overrides, save_override  # noqa: E402
from app.profile import load_profile  # noqa: E402

JOB_ID = "indeed_JOBSEARCH_533"

REWRITES = [
    (
        "supreme-lending-01",
        "Gathered business requirements from accounting stakeholders and delivered an "
        "analytics solution that automated the matching of financial records across "
        "thousands of accounts, replacing a fully manual process.",
        "JD leads with 'gathering business requirements from stakeholders' — bullet now "
        "opens on that instead of burying it mid-sentence.",
    ),
    (
        "supreme-lending-02",
        "Wrote SQL and Python (PySpark) queries to extract and process thousands of "
        "transaction records, applying statistical matching logic in a production data "
        "pipeline.",
        "JD's first named responsibility is 'SQL querying'; the claim led with pipeline "
        "deployment, which this entry-level posting never asks for.",
    ),
    (
        "supreme-lending-04",
        "Presented findings and project status to business stakeholders including "
        "executive leadership, translating complex data workflows into clear, "
        "actionable insights.",
        "JD requires 'strong analytical and communication skills'; dropped "
        "'technical'/'client-facing' framing that narrows this to a technical audience.",
    ),
    (
        "syracuse-01",
        "Ran data quality checks at every stage of ETL pipelines processing 1M+ records, "
        "using PySpark and Airflow.",
        "JD names 'data quality checks' as a core duty — promoted from a trailing "
        "qualifier to the subject of the bullet.",
    ),
    (
        "syracuse-03",
        "Wrote and automated SQL and Python workflows across 50,000+ records, replacing "
        "manual data preparation steps.",
        "Reinforces SQL querying, the JD's top requirement.",
    ),
    (
        "freyr-01",
        "Gathered business requirements across 20+ regional markets and built Power BI "
        "dashboards and reports on a unified SQL data layer, reducing reporting cycle "
        "time by 40%.",
        "This is the bullet that answers 'building dashboards and reports' plus "
        "'reporting tools such as Tableau or Power BI'. Reordered so both land in the "
        "first line rather than behind 'coordinated cross-functional engagements'.",
    ),
    (
        "freyr-02",
        "Mentored junior analysts on data quality checks, reducing inconsistency in "
        "weekly client deliverables.",
        "Matches the JD's exact phrase 'data quality checks' in place of the vaguer "
        "'data-quality best practices'.",
    ),
    (
        "omnicals-01",
        "Standardized data quality and cleansing checks across three inconsistent "
        "source systems, using statistical modeling and feature engineering to improve "
        "data quality by 70%.",
        "Second data-quality proof point, led with the JD's vocabulary; the modeling "
        "detail moves to support rather than headline. Opens on 'Standardized' so it "
        "does not repeat the next bullet's verb.",
    ),
    (
        "omnicals-02",
        "Built a regression-based demand-forecasting model in Python and SQL, validating "
        "business hypotheses against historical sales data to improve planning accuracy "
        "by 25%.",
        "Trimmed hyperparameter tuning — irrelevant to an entry-level reporting role — "
        "keeping the analytical-rigour signal the JD does ask for.",
    ),
]


def main() -> int:
    profile = load_profile()
    claims = {c.claim_id: c.claim for c in profile.evidence}

    clear_overrides(JOB_ID)
    failed = 0
    for claim_id, text, rationale in REWRITES:
        original = claims.get(claim_id)
        if original is None:
            print(f"SKIP {claim_id}: no such claim")
            failed += 1
            continue
        result = save_override(JOB_ID, claim_id, text, original, rationale)
        if result["ok"]:
            print(f"OK   {claim_id}")
        else:
            failed += 1
            print(f"FAIL {claim_id}: {'; '.join(result['problems'])}")

    print(f"\n{len(REWRITES) - failed}/{len(REWRITES)} stored")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
