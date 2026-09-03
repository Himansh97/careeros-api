"""The rendered resume must be one page.

There was no test for this. `MAX_PAGES` sat at 2, `AGENTS.md` documented two
pages as the rule, and the generated documents duly ran to two while the
hand-cut versions fit on one -- a difference nothing would have caught, because
the only place the page count was asserted was a sentence in a markdown file.

These render real documents through the real pipeline. That is the point: the
fitting loop is measure-and-re-render, so a unit test against the constant
would prove nothing about what an employer receives.
"""

from __future__ import annotations

import io
import unittest

from app.documents import MAX_PAGES, build_pdf
from app.tailor import tailor_resume


def _render(title: str, description: str):
    from pypdf import PdfReader

    from app.profile import load_profile
    from app.scoring import score_job_cached as score_job

    profile = load_profile()
    job = {
        "id": "probe",
        "title": title,
        "description": description,
        "company": {"name": "Acme"},
        "location": "Remote",
    }
    pdf = build_pdf(tailor_resume(job, score_job(job, profile), profile), profile)
    return PdfReader(io.BytesIO(pdf))


# The four role families the application history actually contains, plus a
# posting with a long requirement list, which is the case that overflows.
CASES = [
    ("Senior Data Engineer", "ETL, SQL, Python, Airflow, PySpark, dimensional modeling."),
    ("Sr. Product Data Analyst", "Metric definition, SQL, dashboards, stakeholder management."),
    ("Business Analyst I", "Requirements gathering, SQL, Excel, reporting."),
    ("Principal Data Analyst", "SQL, Python, Tableau, Power BI, experimentation, forecasting."),
    (
        "Senior Data Engineer",
        "SQL Python PySpark Airflow Azure AWS dbt Snowflake Kafka Spark Databricks "
        "Terraform Kubernetes Docker CI/CD data modeling data quality governance "
        "orchestration streaming batch warehousing lakehouse Tableau Power BI "
        "stakeholder management requirements gathering forecasting regression "
        "machine learning experimentation A/B testing statistics " * 3,
    ),
]


class TestOnePage(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def test_the_constant_says_one(self) -> None:
        self.assertEqual(MAX_PAGES, 1)

    def test_every_role_family_renders_one_page(self) -> None:
        for title, description in CASES:
            with self.subTest(title=title, length=len(description)):
                self.assertEqual(len(_render(title, description).pages), 1)

    def test_a_one_page_resume_still_carries_real_evidence(self) -> None:
        """Fitting must not be achieved by emptying the document.

        A trim loop that satisfies the page rule by dropping everything would
        pass the test above and produce a useless resume.
        """
        reader = _render(*CASES[0])
        text = reader.pages[0].extract_text()
        self.assertGreater(len(text.split()), 400, "page is nearly empty")
        for expected in ("Supreme Lending", "Syracuse", "EDUCATION"):
            self.assertIn(expected, text)


class TestNoToolchainFingerprint(unittest.TestCase):
    """Punctuation and metadata that mark a document as machine-composed."""

    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")
        self.reader = _render(*CASES[0])
        self.text = " ".join(p.extract_text() for p in self.reader.pages)

    def test_no_em_or_en_dashes(self) -> None:
        self.assertEqual(self.text.count("—"), 0, "em dash")
        self.assertEqual(self.text.count("–"), 0, "en dash")

    def test_no_smart_quotes(self) -> None:
        for char in ("‘", "’", "“", "”"):
            self.assertEqual(self.text.count(char), 0, repr(char))

    def test_producer_and_creator_are_blank(self) -> None:
        meta = {str(k): str(v) for k, v in (self.reader.metadata or {}).items()}
        self.assertEqual(meta.get("/Producer", ""), "")
        self.assertEqual(meta.get("/Creator", ""), "")
        self.assertNotIn("(unspecified)", " ".join(meta.values()))


if __name__ == "__main__":
    unittest.main()
