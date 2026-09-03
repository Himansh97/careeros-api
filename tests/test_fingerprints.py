"""Tests for the machine-fingerprint checks.

A checker that has only ever returned "none" has not been shown to work. Each
test here builds a document that should fail and asserts it does, then the last
one asserts the real pipeline output passes.

What this deliberately does not test: what a third-party AI detector returns.
Nothing local predicts that, and a test claiming otherwise would be a lie in
the test suite.
"""

from __future__ import annotations

import io
import unittest

from app.resume_qa import check_fingerprints


def _pdf(text: str, producer: str = "", creator: str = "") -> bytes:
    """A minimal one-page PDF carrying `text`, via the real renderer."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, producer=producer, creator=creator)
    doc.build([Paragraph(text, getSampleStyleSheet()["Normal"])])
    return buf.getvalue()


def _resume(bullets: list[str]) -> dict:
    return {"sections": [{"bullets": [{"text": b} for b in bullets]}]}


# Genuinely varied: 8 to 30 words, four different constructions, four different
# kinds of opening word. The first draft of this fixture ran 10/12/10/14 words
# and the length check caught it, which is the check working.
NEUTRAL = _resume([
    "Shipped it. Four pools went out in the first week.",
    "Interviewed the specialist who owned the process and wrote down the rules she "
    "had been carrying in her head for two years, then turned them into something "
    "the team could test and argue with.",
    "Cut the check time roughly in half by running the scans in parallel.",
    "A pipeline in Python now processes the transaction records nightly, matches them "
    "against the ledger and routes whatever does not reconcile to the person who owns "
    "that account.",
])


class TestTypography(unittest.TestCase):
    def test_an_em_dash_is_caught(self):
        f = check_fingerprints(_pdf("Built a pipeline — in Python."), NEUTRAL)
        self.assertTrue(any(x["type"] == "fingerprint_typography" for x in f), f)

    def test_smart_quotes_are_caught(self):
        f = check_fingerprints(_pdf("The “matching” step."), NEUTRAL)
        self.assertTrue(any(x["type"] == "fingerprint_typography" for x in f), f)

    def test_plain_punctuation_passes(self):
        f = check_fingerprints(_pdf("Built a pipeline - in Python, with \"quotes\"."), NEUTRAL)
        self.assertEqual([x for x in f if x["type"] == "fingerprint_typography"], [])


class TestMetadata(unittest.TestCase):
    def test_a_named_producer_is_caught(self):
        f = check_fingerprints(_pdf("Text.", producer="ReportLab PDF Library"), NEUTRAL)
        self.assertTrue(any(x["type"] == "fingerprint_metadata" for x in f), f)

    def test_blank_producer_passes(self):
        f = check_fingerprints(_pdf("Text."), NEUTRAL)
        self.assertEqual([x for x in f if x["type"] == "fingerprint_metadata"], [])


class TestVocabulary(unittest.TestCase):
    def test_tell_words_are_caught(self):
        f = check_fingerprints(
            _pdf("Leveraged robust pipelines to streamline delivery."), NEUTRAL)
        hit = [x for x in f if x["type"] == "fingerprint_vocabulary"]
        self.assertTrue(hit)
        for word in ("leverage", "robust", "streamline"):
            self.assertIn(word, hit[0]["detail"])


class TestUniformity(unittest.TestCase):
    """The property text classifiers are actually built around."""

    def test_the_same_construction_everywhere_is_caught(self):
        # The real first draft of the STAR bullets: 18 of 19 used a semicolon.
        uniform = _resume([
            "The process was manual; automated it and cut the time in half.",
            "Data sat in three systems; unified them and improved quality.",
            "Reporting was ad hoc; built dashboards and gave them a standing view.",
            "Delays recurred; analysed the cause and redesigned the workflow.",
        ])
        f = check_fingerprints(_pdf("Text."), uniform)
        self.assertTrue(
            any(x["type"] == "fingerprint_uniform_construction" for x in f), f)

    def test_identical_bullet_lengths_are_caught(self):
        same = _resume(["one two three four five six seven eight nine ten"] * 5)
        f = check_fingerprints(_pdf("Text."), same)
        self.assertTrue(any(x["type"] == "fingerprint_uniform_length" for x in f), f)

    def test_repeated_openers_are_caught(self):
        repeated = _resume([
            "Built a pipeline that processes records nightly across many systems.",
            "Built a dashboard for the operations team to watch throughput daily.",
            "Built a parser that reads correspondence and extracts settlement dates.",
            "Built a gate that fails the build on a critical severity finding today.",
        ])
        f = check_fingerprints(_pdf("Text."), repeated)
        self.assertTrue(any(x["type"] == "fingerprint_uniform_opener" for x in f), f)

    def test_varied_writing_passes(self):
        f = check_fingerprints(_pdf("Text."), NEUTRAL)
        self.assertEqual([x for x in f if x["type"].startswith("fingerprint_uniform")], [])


class TestTheRealPipelineOutput(unittest.TestCase):
    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def test_a_generated_resume_carries_no_fingerprints(self):
        from app.documents import build_pdf
        from app.scoring import score_job_cached as score_job
        from app.tailor import tailor_resume

        for title, desc in (
            ("Sr Data Analyst, Mortgage Analytics",
             "SQL, Power BI, requirements gathering, data quality, Python, Encompass."),
            ("Senior Data Engineer",
             "ETL, Airflow, PySpark, pipelines, CI/CD, Azure, schema validation."),
        ):
            with self.subTest(title=title):
                job = {"id": f"fp-{abs(hash(title))}", "title": title,
                       "description": desc, "company": {"name": "Acme"},
                       "location": "Remote"}
                resume = tailor_resume(job, score_job(job, self.profile), self.profile)
                pdf = build_pdf(resume, self.profile)
                self.assertEqual(check_fingerprints(pdf, resume), [])


if __name__ == "__main__":
    unittest.main()
