"""Tests for role-family framing.

The load-bearing property is not that the router is clever. It is that it
cannot claim a framing the evidence does not defend, and that a posting it
cannot read produces a visible default rather than a confident guess.

A resume variant is the first line a recruiter reads. Getting it wrong is not
a cosmetic miss — it is the document arguing for a job the candidate cannot
support, which is the same failure the containment gate exists to prevent
everywhere else in this pipeline.
"""

from __future__ import annotations

import unittest

from app.resume_variant import FAMILIES, GENERAL, resolve


class FakeClaim:
    """Exactly the surface `scoring._find_evidence` reads: skills, claim text,
    industry, and the approval flag. Kept minimal on purpose — a fake that
    mirrors the whole model stops failing when the real one gains a field."""

    def __init__(self, skills):
        self.skills = skills
        self.claim = " ".join(skills)
        self.industry = "financial services"
        self.approved_for_resume = True


class FakeProfile:
    """Only what `_find_evidence` reads.

    `skills_inventory` matters: it is the weaker "listed but not demonstrated"
    tier the cascade falls back to, and it is only reached when no evidence
    claim matched — which is precisely the path the fallback tests exercise.
    Leaving it off made those tests fail on an AttributeError instead of on
    the behaviour they were written to check.
    """

    def __init__(self, skills, inventory=None):
        self.evidence = [FakeClaim(list(skills))]
        self.headline = "Business Analytics Consultant"
        self.skills = list(skills)
        self.skills_inventory = {"all": list(inventory if inventory is not None else skills)}


FULL = FakeProfile([
    "etl", "data pipelines", "data pipeline design", "python", "sql",
    "machine learning", "statistical modeling", "power bi", "dashboarding",
    "stakeholder management", "requirements gathering", "data analysis",
    "project management",
])

# Someone with no engineering evidence at all.
ANALYST_ONLY = FakeProfile(["data analysis", "stakeholder management", "power bi"])


def job(title: str) -> dict:
    return {"id": "j1", "title": title, "description": "", "company": {"name": "Acme"}}


class TestRealPostings(unittest.TestCase):
    """Titles taken verbatim from the application history."""

    def test_data_engineering_titles_reach_the_engineer_framing(self) -> None:
        for title in (
            "Senior Data Engineer - Finance",
            "Data Engineer",
            "Sr Data Engineer",
            "Vice President, Data Management Engineer",
            "Analytics Engineering Manager",
            "Data Developer",
        ):
            with self.subTest(title=title):
                self.assertEqual(resolve(job(title), FULL).key, "data_engineer")

    def test_product_analyst_titles_reach_the_product_framing(self) -> None:
        for title in (
            "Sr. Product Data Analyst",
            "Data Product Analyst, Private Investor",
            "Digital Data Product Analyst",
            "Product Analyst",
            "Product Operations Analyst Client Delivery Multiple Levels",
            "Product Performance Analyst",
        ):
            with self.subTest(title=title):
                self.assertEqual(resolve(job(title), FULL).key, "product_analyst")

    def test_product_beats_the_generic_engineer_rule(self) -> None:
        # "Data Product Analyst" contains neither an engineer token nor an
        # analytics-engineer shape, but the ordering matters the moment one
        # does. This pins the precedence rather than trusting the regexes.
        v = resolve(job("Data Product Analytics Engineer"), FULL)
        self.assertEqual(v.key, "product_analyst")

    def test_structured_products_is_an_instrument_not_a_product_role(self) -> None:
        # Regression, from the real Point72 posting. An earlier reverse arm in
        # the product pattern matched "Analyst, Structured Product" and framed
        # a quant role as product analytics.
        v = resolve(job("Quantitive Analyst, Structured Products Investment Team"), FULL)
        self.assertNotEqual(v.key, "product_analyst")

    def test_product_qualifies_only_when_it_precedes_the_noun(self) -> None:
        self.assertEqual(resolve(job("Product Data Analyst"), FULL).key, "product_analyst")
        self.assertNotEqual(
            resolve(job("Analyst, Consumer Products Division"), FULL).key, "product_analyst"
        )

    def test_plain_analyst_titles_do_not_narrow(self) -> None:
        # These are real postings too, and none of them justify a narrower
        # framing than the candidate's own headline.
        for title in ("Business Analyst I", "Senior Analyst - Operations",
                      "Sr Inventory Analyst A+A (Minneapolis)"):
            with self.subTest(title=title):
                self.assertTrue(resolve(job(title), FULL).is_default)


class TestEvidenceGate(unittest.TestCase):
    """A family the evidence cannot defend must not be claimed."""

    def test_engineer_title_without_pipeline_evidence_falls_back(self) -> None:
        v = resolve(job("Senior Data Engineer"), ANALYST_ONLY)
        self.assertEqual(v, GENERAL)
        self.assertTrue(v.is_default)

    def test_the_reason_names_the_defending_skill(self) -> None:
        v = resolve(job("Senior Data Engineer - Finance"), FULL)
        self.assertIn(v.defended_by, ("etl", "data pipelines", "data pipeline design"))
        self.assertIn(v.defended_by, v.why())

    def test_an_undefended_family_does_not_fall_through_to_a_looser_one(self) -> None:
        # "AI Data Engineer" matches the engineer family first. With no
        # pipeline evidence it must NOT quietly land on ai_ml instead — that
        # would hide an unevidenced match behind a label that happens to fit.
        profile = FakeProfile(["machine learning", "python"])
        self.assertTrue(resolve(job("AI Data Engineer"), profile).is_default)


class TestDefaultIsVisible(unittest.TestCase):
    def test_an_unreadable_title_is_a_default_not_a_match(self) -> None:
        for title in ("", "Anticipated Weekly Hours", "xyzzy"):
            with self.subTest(title=title):
                v = resolve(job(title), FULL)
                self.assertTrue(v.is_default)
                self.assertEqual(v.matched, "")
                self.assertIn("No role family matched", v.why())

    def test_a_match_reports_what_it_matched_on(self) -> None:
        v = resolve(job("Senior Data Engineer - Finance"), FULL)
        self.assertFalse(v.is_default)
        self.assertTrue(v.matched)
        self.assertIn(v.matched.lower(), "senior data engineer - finance")

    def test_resolve_never_raises_and_never_returns_none(self) -> None:
        for bad in ({}, {"title": None}, {"title": 12345}):
            with self.subTest(job=bad):
                v = resolve(dict(bad), FULL)
                self.assertIsNotNone(v)
                self.assertTrue(v.key)


class TestFamilyTable(unittest.TestCase):
    def test_no_two_families_share_a_key(self) -> None:
        keys = [f.key for f in FAMILIES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_family_declares_at_least_one_defining_skill(self) -> None:
        # A family with no defining skills would bypass the evidence gate
        # entirely and always resolve, which is the exact bug this module was
        # written to prevent.
        for f in FAMILIES:
            with self.subTest(family=f.key):
                self.assertTrue(f.defining)


class TestProjectPreferenceCannotOverridePosting(unittest.TestCase):
    """The nudge must stay smaller than the posting's own evidence."""

    def test_preference_matches_the_recorded_project_name(self) -> None:
        # The bug this pins: projects are recorded as
        # "Custody — signed AI decision ledger", and an exact match against
        # "Custody" never fired. The unit test passed anyway because it used
        # the short name it assumed, so the ordering silently never moved.
        from app.tailor import _variant_preference

        engineer = resolve(job("Senior Data Engineer"), FULL)
        self.assertGreater(
            _variant_preference("Custody — signed AI decision ledger", engineer), 0
        )
        product = resolve(job("Product Analyst"), FULL)
        self.assertGreater(
            _variant_preference("Optionora — options decision-readiness platform", product), 0
        )

    def test_preference_is_worth_less_than_one_vocabulary_hit(self) -> None:
        from app.tailor import _variant_preference

        engineer = resolve(job("Senior Data Engineer"), FULL)
        top = _variant_preference("Enterprise DevSecOps CI/CD Platform", engineer)
        # _project_affinity scores each posting-vocabulary hit at 300.
        self.assertLess(top, 300)
        self.assertGreater(top, 0)

    def test_ordering_within_leads_with_is_preserved(self) -> None:
        from app.tailor import _variant_preference

        engineer = resolve(job("Senior Data Engineer"), FULL)
        self.assertGreater(
            _variant_preference("Enterprise DevSecOps CI/CD Platform", engineer),
            _variant_preference("Custody — signed AI decision ledger", engineer),
        )

    def test_an_unlisted_project_gets_no_boost(self) -> None:
        from app.tailor import _variant_preference

        engineer = resolve(job("Senior Data Engineer"), FULL)
        self.assertEqual(
            _variant_preference("Optionora — options decision-readiness platform", engineer), 0
        )

    def test_a_default_variant_boosts_nothing(self) -> None:
        from app.tailor import _variant_preference

        for name in ("Custody — signed AI decision ledger", "CareerOS", "Optionora"):
            self.assertEqual(_variant_preference(name, GENERAL), 0)

    def test_every_preferred_project_exists_in_the_evidence(self) -> None:
        """A renamed or misspelled project must fail loudly, not silently.

        The whole preference is a no-op when a name does not match, and a
        no-op is invisible. This is the check that would have caught the
        original bug on the first run.
        """
        try:
            from app.profile import load_profile

            profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

        from app.tailor import _project_key

        recorded = {_project_key(c.project) for c in profile.evidence if getattr(c, "project", "")}
        self.assertTrue(recorded, "no projects in the evidence file")
        for family in FAMILIES:
            for name in family.leads_with:
                with self.subTest(family=family.key, project=name):
                    self.assertIn(_project_key(name), recorded)


if __name__ == "__main__":
    unittest.main()
