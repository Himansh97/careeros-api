"""Tests for composed STAR bullets.

The load-bearing property is not that the bullets read well. It is that a
composed bullet cannot say anything its source claims do not, because a bullet
built from several claims is exactly where an invented result would be easiest
to slip in and hardest to notice.
"""

from __future__ import annotations

import unittest

from app import star
from app.overrides import verify_override


class FakeClaim:
    def __init__(self, cid, text):
        self.claim_id = cid
        self.claim = text
        self.skills = []
        self.industry = "financial services"
        self.approved_for_resume = True


class FakeProfile:
    def __init__(self, claims):
        self.evidence = [FakeClaim(c, t) for c, t in claims]
        self.skills_inventory = {"all": []}
        self.headline = "x"


class TestContainmentOnComposedText(unittest.TestCase):
    """A composition is checked against everything it draws on, not one claim."""

    SOURCES = (
        ("a", "Replaced a manual reconciliation process in which a specialist spent "
              "2-3 hours daily across 100+ open cases."),
        ("b", "Codified the process as a 16-rule classification engine in Python."),
    )

    def test_a_figure_from_either_source_is_allowed(self):
        joined = " ".join(t for _, t in self.SOURCES)
        text = ("A specialist spent 2-3 hours daily across 100+ open cases; codified "
                "the process as a 16-rule classification engine in Python.")
        self.assertEqual(verify_override(joined, text), [])

    def test_a_figure_in_neither_source_is_refused(self):
        joined = " ".join(t for _, t in self.SOURCES)
        text = ("A specialist spent 2-3 hours daily across 100+ open cases; codified "
                "it as a 16-rule engine, cutting processing time by 80%.")
        problems = verify_override(joined, text)
        self.assertTrue(any("80" in p for p in problems), problems)

    def test_an_invented_system_is_refused(self):
        joined = " ".join(t for _, t in self.SOURCES)
        text = "Codified the process as a 16-rule engine in Python and Snowflake."
        problems = verify_override(joined, text)
        self.assertTrue(any("Snowflake" in p for p in problems), problems)


class TestTheAuthoredBankPasses(unittest.TestCase):
    """Every shipped bullet must survive its own sources.

    This is the check that catches a claim being edited underneath a
    composition. It is skipped, not passed, when the private evidence file is
    unavailable -- a green run that proved nothing would be worse than a skip.
    """

    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")
        star._raw.cache_clear()

    def test_no_bullet_fails_containment(self):
        good, bad = star.load(self.profile)
        self.assertEqual([f"{b.id}: {b.problems}" for b in bad], [])
        self.assertGreater(len(good), 0)

    def test_every_source_claim_exists(self):
        good, bad = star.load(self.profile)
        known = {c.claim_id for c in self.profile.evidence}
        for b in good:
            for cid in b.source_claims:
                self.assertIn(cid, known, f"{b.id} cites a claim that is gone")

    def test_each_role_can_field_three(self):
        good, _ = star.load(self.profile)
        from collections import Counter

        for employer, n in Counter(b.employer for b in good).items():
            with self.subTest(employer=employer):
                self.assertGreaterEqual(n, 3, "not enough composed bullets to choose from")


class TestSelection(unittest.TestCase):
    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"real profile unavailable: {exc}")
        star._raw.cache_clear()
        self.employer = "Supreme Lending (Everett Financial, Inc.)"

    def test_three_bullets_are_returned(self):
        got = star.select(self.employer, self.profile, ["sql", "python"], "", limit=3)
        self.assertEqual(len(got), 3)

    def test_the_posting_changes_which_bullets_are_chosen(self):
        analyst = star.select(self.employer, self.profile,
                              ["requirements gathering", "stakeholder management"],
                              "requirements, UAT, acceptance criteria", limit=3)
        engineer = star.select(self.employer, self.profile,
                               ["etl", "ci/cd", "data pipelines"],
                               "airflow, pyspark, github actions, security scanning", limit=3)
        self.assertNotEqual([b.id for b in analyst], [b.id for b in engineer])

    def test_bullets_are_ordered_as_a_story_not_by_score(self):
        """Situation before action before result, whatever the relevance order."""
        got = star.select(self.employer, self.profile, ["python", "sql"], "", limit=3)
        arcs = [star.ARC_ORDER.get(b.arc, 1) for b in got]
        self.assertEqual(arcs, sorted(arcs), [b.arc for b in got])

    def test_an_unknown_employer_returns_nothing(self):
        self.assertEqual(star.select("Nowhere Ltd", self.profile, ["sql"], ""), [])


class TestMissingBankIsNotAnError(unittest.TestCase):
    def test_absent_file_yields_an_empty_bank(self):
        """The bullets are candidate data and live outside this repository, so a
        checkout without them must still tailor resumes."""
        from pathlib import Path
        from unittest import mock

        star._raw.cache_clear()
        with mock.patch.object(star, "STAR_PATH", Path("/nonexistent/star.json")):
            self.assertEqual(star._raw(), {})
        star._raw.cache_clear()


if __name__ == "__main__":
    unittest.main()
