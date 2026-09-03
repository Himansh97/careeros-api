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


class TestSponsorshipIsAThirdState(unittest.TestCase):
    """Blocked, warned, or clear. Two states could not express the UAE.

    India is unrestricted: citizenship, no employer involvement. Dublin has
    nothing recorded and blocks. The UAE is neither -- the employer sponsors
    the residence permit as a matter of routine, which is a real condition
    worth stating and not a reason to hide the market.
    """

    RIGHTS = {
        "IN": {"status": "citizen", "unrestricted": True},
        "AE": {"status": "employer-sponsored", "unrestricted": False,
               "sponsorship_required": True, "workable": True},
    }

    def setUp(self):
        self.profile = FakeProfile(self.RIGHTS)

    def _verdict(self, location):
        return check_eligibility(job(location), self.profile)

    def test_dubai_is_not_blocked(self):
        v = self._verdict("Dubai, United Arab Emirates")
        self.assertNotIn("work_location", [b["type"] for b in v["blockers"]])

    def test_dubai_carries_a_sponsorship_warning(self):
        v = self._verdict("Dubai, United Arab Emirates")
        self.assertIn("sponsorship_required", [w["type"] for w in v["warnings"]])

    def test_india_carries_no_such_warning(self):
        """Citizenship means no employer involvement, so saying otherwise would
        invent a condition that does not exist."""
        v = self._verdict("Mumbai, India")
        self.assertNotIn("sponsorship_required", [w["type"] for w in v["warnings"]])

    def test_an_unrecorded_country_still_blocks_rather_than_warning(self):
        v = self._verdict("Dublin, Ireland")
        self.assertIn("work_location", [b["type"] for b in v["blockers"]])
        self.assertEqual([w["type"] for w in v["warnings"]], [])

    def test_removing_the_ae_right_restores_the_block(self):
        self.profile = FakeProfile({"IN": self.RIGHTS["IN"]})
        v = self._verdict("Dubai, United Arab Emirates")
        self.assertIn("work_location", [b["type"] for b in v["blockers"]])


class TestTheMarketDecidesTheResumeHeader(unittest.TestCase):
    """Nationality and visa status belong on a Gulf CV and are a liability on
    a US one, so this is keyed to where the role is, never to a preference."""

    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"real profile unavailable: {exc}")

    def test_a_uae_resume_states_nationality_and_visa_status(self):
        from app.documents import _market_header

        lines = _market_header({"market": "ae"}, self.profile)
        self.assertTrue(any("Nationality" in x for x in lines), lines)
        self.assertTrue(any("visa status" in x.lower() for x in lines), lines)

    def test_a_us_resume_states_neither(self):
        """Nationality on a US application invites exactly the discrimination
        US hiring law exists to prevent."""
        from app.documents import _market_header

        self.assertEqual(_market_header({"market": "us"}, self.profile), [])
        self.assertEqual(_market_header({}, self.profile), [])

    def test_an_india_resume_states_neither(self):
        from app.documents import _market_header

        self.assertEqual(_market_header({"market": "in"}, self.profile), [])

    def test_nationality_is_never_inferred(self):
        """Only a recorded citizenship produces one. Guessing from a name, a
        location or an education history would be a fabricated fact on a
        document a visa decision is read from."""
        from app.documents import _market_header

        class NoRights:
            work_rights = {"AE": {"sponsorship_required": True, "workable": True}}

        lines = _market_header({"market": "ae"}, NoRights())
        self.assertFalse(any("Nationality" in x for x in lines), lines)

    def test_the_market_comes_from_the_posting_not_a_setting(self):
        from app.tailor import _market_for

        self.assertEqual(_market_for({"location": "Dubai"}), "ae")
        self.assertEqual(_market_for({"location": "Mumbai, India"}), "in")
        self.assertEqual(_market_for({"location": "Dallas, TX"}), "us")
        self.assertEqual(_market_for({}), "us")


class TestGulfHeaderFields(unittest.TestCase):
    """Languages and availability belong on a Gulf CV and nowhere else.

    Both are rendered only from stated facts. A citizenship implies no
    language -- India has twenty-two official ones -- and an invented
    availability is a commitment the candidate never made.
    """

    class Profile:
        work_rights = {"AE": {"sponsorship_required": True, "workable": True},
                       "IN": {"status": "citizen", "unrestricted": True}}
        languages = [{"language": "English", "proficiency": "Fluent"},
                     {"language": "Hindi", "proficiency": "Native"}]
        availability = ""

    def _header(self, market, **overrides):
        from app.documents import _market_header

        p = self.Profile()
        for k, v in overrides.items():
            setattr(p, k, v)
        return _market_header({"market": market}, p)

    def test_languages_render_with_proficiency(self):
        line = next(x for x in self._header("ae") if x.startswith("Languages"))
        self.assertIn("English (Fluent)", line)
        self.assertIn("Hindi (Native)", line)

    def test_languages_do_not_render_on_other_markets(self):
        for market in ("us", "in"):
            with self.subTest(market=market):
                self.assertEqual(self._header(market), [])

    def test_no_languages_recorded_means_no_line(self):
        self.assertFalse(any(x.startswith("Languages")
                             for x in self._header("ae", languages=[])))

    def test_an_incomplete_language_entry_is_skipped(self):
        """A language with no stated proficiency is not a Gulf-CV language."""
        header = self._header("ae", languages=[{"language": "Arabic", "proficiency": ""}])
        self.assertFalse(any(x.startswith("Languages") for x in header), header)

    def test_availability_is_omitted_when_unknown(self):
        """Omitted rather than guessed: 'Immediate' is a commitment."""
        self.assertFalse(any(x.startswith("Availability") for x in self._header("ae")))

    def test_availability_renders_once_stated(self):
        header = self._header("ae", availability="30 days")
        self.assertIn("Availability: 30 days", header)


class TestSectionOrderFollowsTheMarket(unittest.TestCase):
    """UAE guidance puts education and certifications ahead of projects.

    US convention leads with projects, because published work outranks a degree
    there. Two markets, two orders, decided by where the role is.
    """

    def setUp(self):
        try:
            from app.profile import load_profile

            self.profile = load_profile()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"real profile unavailable: {exc}")

    def _headings(self, location):
        import io

        from pypdf import PdfReader

        from app.documents import build_pdf
        from app.scoring import score_job
        from app.tailor import tailor_resume

        job = {"id": f"ord-{abs(hash(location))}", "title": "Data Analyst",
               "description": "SQL, Power BI, requirements gathering, data quality.",
               "company": {"name": "Acme"}, "location": location}
        resume = tailor_resume(job, score_job(job, self.profile), self.profile)
        text = PdfReader(io.BytesIO(build_pdf(resume, self.profile))).pages[0].extract_text()
        return [
            l.strip() for l in text.splitlines()
            if l.strip().isupper() and len(l.strip()) > 4 and "SRIVASTAVA" not in l
        ]

    def test_uae_puts_education_and_certifications_before_projects(self):
        h = self._headings("Dubai, United Arab Emirates")
        self.assertLess(h.index("EDUCATION"), h.index("SELECTED PROJECTS"), h)
        self.assertLess(h.index("CERTIFICATIONS"), h.index("SELECTED PROJECTS"), h)
        self.assertLess(h.index("EDUCATION"), h.index("CERTIFICATIONS"), h)

    def test_uae_gives_certifications_their_own_heading(self):
        self.assertIn("CERTIFICATIONS", self._headings("Dubai"))

    def test_us_leads_with_projects_and_has_no_certifications_heading(self):
        h = self._headings("Dallas, TX")
        self.assertLess(h.index("SELECTED PROJECTS"), h.index("EDUCATION"), h)
        self.assertNotIn("CERTIFICATIONS", h)

    def test_india_keeps_the_us_order(self):
        """Nothing researched says India differs, so it should not."""
        self.assertEqual(self._headings("Mumbai, India"), self._headings("Dallas, TX"))

    def test_the_common_opening_is_the_same_everywhere(self):
        for place in ("Dubai", "Dallas, TX", "Mumbai, India"):
            with self.subTest(place=place):
                self.assertEqual(
                    self._headings(place)[:3],
                    ["PROFESSIONAL SUMMARY", "SKILLS", "PROFESSIONAL EXPERIENCE"],
                )
