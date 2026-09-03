"""Tests for per-country work rights.

The dangerous direction is the permissive one. A gate that over-blocks costs
the candidate some postings; a gate that stops blocking puts them in front of
roles they cannot legally take, which is the failure `eligibility.py` exists to
prevent and the reason its Dublin and Sao Paulo comment is in the file.

So every test here that asserts a pass is paired with one that asserts a block.
"""

from __future__ import annotations

import unittest

from app.eligibility import check_eligibility, country_for, may_work_in


class FakeProfile:
    def __init__(self, rights):
        self.work_authorization = "OPT (F-1 Optional Practical Training)"
        self.work_rights = rights


RIGHTS = {
    "IN": {"status": "citizen", "unrestricted": True},
    "US": {"status": "F-1 OPT", "unrestricted": False},
}


def job(location, description="Data analyst. SQL and Python."):
    return {"id": "j", "title": "Data Analyst", "location": location,
            "description": description, "company": {"name": "Acme"}}


class TestCountryMapping(unittest.TestCase):
    def test_indian_cities_map_to_india(self):
        for place in ("Pune, India", "Noida, UP", "Mumbai, MH", "Hyderabad",
                      "Bengaluru", "Pondicherry", "Gurugram"):
            with self.subTest(place=place):
                self.assertEqual(country_for(place), "IN")

    def test_an_unmapped_place_has_no_country(self):
        for place in ("Dublin, Ireland", "Sao Paulo, Brazil", "Remote", ""):
            with self.subTest(place=place):
                self.assertIsNone(country_for(place))


class TestMayWorkIn(unittest.TestCase):
    def test_a_recorded_unrestricted_right_permits(self):
        self.assertTrue(may_work_in(FakeProfile(RIGHTS), "Pune, India"))

    def test_a_recorded_restricted_right_does_not_permit(self):
        """The US entry is unrestricted=False. OPT is a right to work, but not
        an unrestricted one, and this function answers the narrower question."""
        self.assertFalse(may_work_in(FakeProfile(RIGHTS), "Bengaluru")
                         is None)
        self.assertFalse(may_work_in(FakeProfile({"IN": {"unrestricted": False}}),
                                     "Pune, India"))

    def test_an_unrecorded_country_does_not_permit(self):
        """Absent means no. Treating a data gap as permission would turn a
        missing record into a green light on the one question where being
        wrong is expensive."""
        self.assertFalse(may_work_in(FakeProfile(RIGHTS), "London, UK"))
        self.assertFalse(may_work_in(FakeProfile(RIGHTS), "Toronto, Canada"))

    def test_a_profile_with_no_rights_at_all_permits_nothing(self):
        self.assertFalse(may_work_in(FakeProfile({}), "Pune, India"))


class TestTheGate(unittest.TestCase):
    def setUp(self):
        self.profile = FakeProfile(RIGHTS)

    def _blockers(self, location):
        verdict = check_eligibility(job(location), self.profile)
        return [b["type"] for b in verdict["blockers"]]

    def test_india_is_no_longer_blocked_on_location(self):
        self.assertNotIn("work_location", self._blockers("Mumbai, India"))

    def test_dublin_is_still_blocked(self):
        """The comment in eligibility.py records why this rule exists: a
        Dublin People Analytics role scored 96 and led the shortlist."""
        self.assertIn("work_location", self._blockers("Dublin, Ireland"))

    def test_sao_paulo_is_still_blocked(self):
        self.assertIn("work_location", self._blockers("Sao Paulo, Brazil"))

    def test_london_is_blocked_despite_being_mapped(self):
        """Mapped to a country is not the same as having a right there."""
        self.assertIn("work_location", self._blockers("London, UK"))

    def test_us_locations_were_never_blocked_and_still_are_not(self):
        for place in ("Dallas, TX", "Remote, US", "Austin, TX"):
            with self.subTest(place=place):
                self.assertNotIn("work_location", self._blockers(place))

    def test_removing_the_recorded_right_restores_the_block(self):
        """The single most important assertion here: this passes because of a
        recorded fact, not because the rule was weakened."""
        self.profile = FakeProfile({})
        self.assertIn("work_location", self._blockers("Mumbai, India"))


class TestTheRealProfile(unittest.TestCase):
    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover - depends on local PII
            self.skipTest(f"real profile unavailable: {exc}")

    def test_india_is_recorded_and_unrestricted(self):
        self.assertTrue(may_work_in(self.profile, "Mumbai, India"))

    def test_a_real_india_posting_is_eligible(self):
        verdict = check_eligibility(job("Pune, India"), self.profile)
        self.assertEqual(verdict["verdict"], "ELIGIBLE", verdict["blockers"])

    def test_countries_with_no_recorded_right_stay_blocked(self):
        for place in ("Dublin, Ireland", "Toronto, Canada", "London, UK"):
            with self.subTest(place=place):
                verdict = check_eligibility(job(place), self.profile)
                self.assertIn("work_location", [b["type"] for b in verdict["blockers"]])


if __name__ == "__main__":
    unittest.main()
