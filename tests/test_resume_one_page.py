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
        # Distinct per case: score_job_cached memoises on job id alone.
        "id": f"probe-{abs(hash((title, description)))}",
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
        self.assertIn("EDUCATION", text)
        # Read from the profile rather than named here. This repo is public and
        # the profile is not, so a literal would move candidate data across
        # that line -- which is what non-negotiable #3 and the .gitignore rules
        # on scripts/align_*.py are both about.
        from app.profile import load_profile

        employers = [r.get("employer", "") for r in load_profile().employment_history]
        for employer in employers[:2]:
            self.assertIn(employer.split(" (")[0], text)


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


class TestSkillsAreCappedButRelevanceSurvives(unittest.TestCase):
    """The cap is only safe because the ordering earns it.

    Dropping groups off the end is fine when the ones that fall off are the
    ones this posting never mentioned. It would be a real loss if a posting's
    own specialism could be cut, so that is what this checks rather than the
    cap arithmetic.
    """

    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def _groups(self, title: str, description: str):
        from app.documents import _prioritised_skills
        from app.scoring import score_job_cached as score_job

        # A distinct id per job. `score_job_cached` memoises on job id alone
        # and only clears when the profile object changes, so reusing one id
        # for several different postings hands back the first one's score.
        # Two of these tests passed on that stale result before this was fixed.
        job = {"id": f"probe-{abs(hash((title, description)))}", "title": title,
               "description": description,
               "company": {"name": "Acme"}, "location": "Remote"}
        resume = tailor_resume(job, score_job(job, self.profile), self.profile)
        return [name for name, _ in _prioritised_skills(resume, self.profile)]

    def test_the_page_shows_at_most_the_cap(self) -> None:
        from app.documents import MAX_SKILL_GROUPS

        groups = self._groups("Senior Data Engineer", "ETL, SQL, Python, Airflow.")
        self.assertLessEqual(len(groups), MAX_SKILL_GROUPS)

    def test_a_mortgage_posting_keeps_the_mortgage_group(self) -> None:
        groups = self._groups(
            "Sr Data Analyst Mortgage Analytics",
            "Mortgage servicing analytics. Encompass, MISMO, escrow reconciliation, "
            "Ginnie Mae pool delivery, Purchase Advice, SQL, Power BI.",
        )
        self.assertIn("mortgage_domain", groups)
        self.assertEqual(groups[0], "mortgage_domain", "the specialism should lead")

    def test_an_engineering_posting_keeps_the_engineering_group(self) -> None:
        groups = self._groups(
            "Senior Data Engineer",
            "Build ETL pipelines with PySpark and Airflow. Data pipeline design, "
            "data quality control, dimensional modeling, BigQuery.",
        )
        self.assertIn("data_engineering_and_big_data", groups)

    def test_the_inventory_itself_is_not_trimmed(self) -> None:
        """Scoring reads the full inventory; only the page is capped.

        Deleting entries would turn skills the candidate genuinely has into
        gaps, which is the opposite of what non-negotiable #2 asks for.
        """
        from app.documents import MAX_SKILL_GROUPS

        self.assertGreater(len(self.profile.skills_inventory), MAX_SKILL_GROUPS)


class TestSummaryClaimsNoTenure(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def test_no_year_count_appears_in_the_summary(self) -> None:
        """A hardcoded tenure traces to no evidence and is checkable from the
        dates on the same page, so it must not be asserted."""
        import re

        text = _render(*CASES[0]).pages[0].extract_text()
        self.assertNotIn("3+ years", text)
        self.assertIsNone(
            re.search(r"\d+\+?\s*years of experience", text),
            "the summary is claiming a tenure again",
        )


class TestEducationClosesTheGap(unittest.TestCase):
    """A degree must show the years it covers, not just the year it ended.

    Employment on this resume ends Jul 2023 and resumes Jul 2025. The MS fills
    those two years, but while only the graduation date rendered, the page
    showed a 24-month absence with nothing explaining it. `_edu_period` said in
    its own docstring that it showed the study period, and did not.
    """

    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def test_a_degree_with_a_start_date_renders_a_range(self) -> None:
        from app.documents import _edu_period

        for entry in self.profile.education:
            if entry.get("start_date"):
                with self.subTest(degree=entry.get("degree")):
                    self.assertIn(" - ", _edu_period(entry))

    def test_a_degree_without_a_start_date_still_renders(self) -> None:
        from app.documents import _edu_period

        self.assertEqual(_edu_period({"graduation_date": "2025-05"}), "May 2025")
        self.assertEqual(_edu_period({}), "")

    def test_the_range_reaches_the_rendered_page(self) -> None:
        from app.documents import _edu_period

        text = _render(*CASES[0]).pages[0].extract_text()
        studied = [e for e in self.profile.education if e.get("start_date")]
        self.assertTrue(studied, "no education entry records a start date")
        for entry in studied:
            self.assertIn(_edu_period(entry), text)

    def test_the_date_column_holds_only_a_date(self) -> None:
        """The right-hand column is a fifth of the width, sized for a date.

        A period plus a GPA does not fit and wrapped between "GPA" and the
        number, leaving the value stranded on its own line. GPA belongs with
        the degree.
        """
        from app.documents import _edu_left, _edu_period

        for entry in self.profile.education:
            with self.subTest(degree=entry.get("degree")):
                self.assertNotIn("GPA", _edu_period(entry))
                self.assertLessEqual(len(_edu_period(entry)), 22)
                if entry.get("gpa"):
                    self.assertIn("GPA", _edu_left(entry))

    def test_gpa_survives_into_the_rendered_page(self) -> None:
        # Moving a field between columns is exactly how one quietly disappears.
        text = _render(*CASES[0]).pages[0].extract_text()
        graded = [e for e in self.profile.education if e.get("gpa")]
        self.assertTrue(graded, "no education entry records a GPA")
        for entry in graded:
            self.assertIn(f"GPA {entry['gpa']}", text)


class TestStarBulletsSurviveTheFitter(unittest.TestCase):
    """Three composed bullets per role are the argument the page makes.

    The fitter used to cut employment bullets first, which reduced older roles
    to two and broke the situation-action-result shape on three of four jobs.
    Projects and skills give way before bullets do now, so this pins the order
    of sacrifice rather than the page count alone.
    """

    def setUp(self) -> None:
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def _render(self, title, description):
        from pypdf import PdfReader

        from app.documents import build_pdf
        from app.scoring import score_job_cached as score_job

        job = {"id": f"star-{abs(hash(title))}", "title": title,
               "description": description, "company": {"name": "Acme"},
               "location": "Remote"}
        resume = tailor_resume(job, score_job(job, self.profile), self.profile)
        return resume, PdfReader(io.BytesIO(build_pdf(resume, self.profile)))

    def test_every_role_keeps_three_bullets_on_one_page(self) -> None:
        resume, reader = self._render(
            "Sr Data Analyst, Mortgage Analytics",
            "SQL, Power BI, requirements gathering, data quality, Python, Encompass, MISMO.")
        self.assertEqual(len(reader.pages), 1)
        for section in resume["sections"]:
            with self.subTest(role=section["heading"][:30]):
                self.assertEqual(len(section["bullets"]), 3)

    def test_the_bullets_are_composed_not_raw_claims(self) -> None:
        resume, _ = self._render(
            "Senior Data Engineer",
            "ETL, Airflow, PySpark, pipelines, CI/CD, Azure, schema validation.")
        kinds = {b.get("changeType") for s in resume["sections"] for b in s["bullets"]}
        self.assertEqual(kinds, {"rewritten"})

    def test_every_rendered_bullet_names_its_source_claims(self) -> None:
        """A composed bullet with no cited sources cannot be checked, which is
        the whole reason composing is allowed at all."""
        resume, _ = self._render(
            "Business Analyst",
            "Requirements gathering, stakeholder management, UAT, process improvement.")
        for section in resume["sections"]:
            for bullet in section["bullets"]:
                with self.subTest(bullet=bullet["id"]):
                    self.assertTrue(bullet.get("sourceClaims"))
