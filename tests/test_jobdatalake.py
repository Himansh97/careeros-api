"""Tests for the JobDataLake source.

Two properties matter. It must be off, silently and completely, when no key is
configured -- the nine direct board readers are the system and this is an
addition. And a row it cannot turn into something applicable must be dropped
rather than shown, because the pipeline's entire output is somewhere to click.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app import sources


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """Routes list and detail, because the adapter makes both calls.

    The search endpoint returns no description and the detail endpoint does, so
    a fake that answered both with the same payload would pass while the real
    adapter dropped every job. It did, before these were rewritten against the
    live schema.
    """

    def __init__(self, payload, detail=None):
        self.payload = payload
        self.detail = detail if detail is not None else DETAIL
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url.rstrip("/").endswith("/jobs"):
            return FakeResponse(self.payload)
        return FakeResponse(self.detail)


# The real search response: no description, company_name flat, `url` not
# `apply_url`, `job_handle` not `handle`, posted_at in Unix milliseconds.
ROW = {
    "id": "6a99bac471c4b76e23ef5e1d",
    "job_handle": "acme-data-analyst-ab12c",
    "title": "Data Analyst",
    "company_name": "Acme",
    "domain_name": "acme.com",
    "locations": ["Mumbai, India"],
    "countries": ["IN"],
    "remote_type": "on_site",
    "url": "https://acme.wd1.myworkdayjobs.com/job/123",
    "posted_at": 1788459717875,
}

# The real detail response: has the description, and posted_at as ISO.
DETAIL = {
    "id": "6a99bac471c4b76e23ef5e1d",
    "job_handle": "acme-data-analyst-ab12c",
    "title": "Data Analyst",
    "url": "https://acme.wd1.myworkdayjobs.com/job/123",
    "description": "<p>SQL and Python, five years of data analysis.</p>",
    "posted_at": "2026-09-03T18:21:57.875Z",
    "locations": ["Mumbai, India"],
    "company": {"name": "Acme"},
}


def run(coro):
    import asyncio

    return asyncio.run(coro)


class TestOffWithoutAKey(unittest.TestCase):
    def test_no_key_means_no_request_and_no_jobs(self):
        client = FakeClient({"jobs": [ROW]})
        with mock.patch("app.config.jobdatalake_key", return_value=None):
            got = run(sources.fetch_jobdatalake(client))
        self.assertEqual(got, [])
        self.assertEqual(client.calls, [], "made a request with no key configured")


class TestRequestShape(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient({"jobs": [ROW]})

    def _fetch(self, **kw):
        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            return run(sources.fetch_jobdatalake(self.client, **kw))

    def test_the_key_travels_in_the_header_not_the_query(self):
        self._fetch()
        call = self.client.calls[0]
        self.assertEqual(call["headers"]["X-API-Key"], "k")
        self.assertNotIn("k", str(call["params"]))

    def test_a_region_selects_its_country(self):
        self._fetch(region="in")
        self.assertEqual(self.client.calls[0]["params"]["countries"], "IN")

    def test_an_unknown_region_falls_back_rather_than_failing(self):
        self._fetch(region="atlantis")
        self.assertEqual(
            self.client.calls[0]["params"]["countries"],
            sources.REGIONS[sources.DEFAULT_REGION]["countries"],
        )

    def test_page_size_is_capped_at_the_documented_maximum(self):
        self._fetch(per_page=5000)
        self.assertLessEqual(self.client.calls[0]["params"]["per_page"], 100)


class TestRowsBecomeJobs(unittest.TestCase):
    def _fetch(self, payload):
        client = FakeClient(payload)
        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            return run(sources.fetch_jobdatalake(client))

    def test_a_row_becomes_an_applicable_job(self):
        got = self._fetch({"jobs": [ROW]})
        self.assertEqual(len(got), 1)
        job = got[0]
        self.assertEqual(job["title"], "Data Analyst")
        self.assertEqual(job["company"]["name"], "Acme")
        self.assertEqual(job["applyUrl"], ROW["url"])
        # The stamp is the label, which is what test_handshake_source pins:
        # a source an adapter writes but the coverage page cannot name is the
        # drift that test exists to catch, and it caught this one.
        self.assertEqual(job["source"], "JobDataLake")
        self.assertIn(job["source"], sources.SOURCE_LABELS)
        self.assertNotIn("<p>", job["description"])
        self.assertIn("five years", job["description"])
        # ISO from the detail endpoint, milliseconds from search. Handling only
        # one silently nulled every date.
        self.assertEqual(job["postedAt"], "2026-09-03")

    def test_a_job_with_no_description_is_dropped(self):
        """The important one. score_job on an empty description returns 85 with
        no gaps, above a real posting the candidate fits worse, because there
        are no stated requirements to miss. An unscoreable job that outranks
        scoreable ones is worse than an absent one."""
        client = FakeClient({"jobs": [ROW]}, detail={**DETAIL, "description": ""})
        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            self.assertEqual(run(sources.fetch_jobdatalake(client)), [])

    def test_a_posting_that_fails_to_hydrate_is_dropped_not_fatal(self):
        """One posting failing is not a source outage: _safe_result would mark
        the whole adapter unhealthy for the sake of a single bad row."""
        class Flaky(FakeClient):
            async def get(self, url, params=None, headers=None, timeout=None):
                if not url.rstrip("/").endswith("/jobs"):
                    raise RuntimeError("502")
                return FakeResponse(self.payload)

        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            self.assertEqual(run(sources.fetch_jobdatalake(Flaky({"jobs": [ROW]}))), [])

    def test_a_row_with_no_apply_url_is_dropped(self):
        client = FakeClient({"jobs": [{**ROW, "url": ""}]}, detail={**DETAIL, "url": ""})
        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            self.assertEqual(run(sources.fetch_jobdatalake(client)), [])

    def test_a_posting_with_no_title_anywhere_is_dropped(self):
        """Detail is merged over search, so a title missing from the search row
        but present in detail is used rather than dropped -- correct, and the
        reason this blanks both."""
        client = FakeClient({"jobs": [{**ROW, "title": "  "}]},
                            detail={**DETAIL, "title": ""})
        with mock.patch("app.config.jobdatalake_key", return_value="k"):
            self.assertEqual(run(sources.fetch_jobdatalake(client)), [])

    def test_detail_fills_a_field_the_search_row_lacks(self):
        got = self._fetch({"jobs": [{**ROW, "title": ""}]})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["title"], "Data Analyst")

    def test_a_remote_row_says_so_in_its_location(self):
        got = self._fetch({"jobs": [{**ROW, "remote_type": "fully_remote"}]})
        self.assertIn("Remote", got[0]["location"])

    def test_alternative_payload_keys_are_read(self):
        for key in ("jobs", "data", "results"):
            with self.subTest(key=key):
                self.assertEqual(len(self._fetch({key: [ROW]})), 1)


class TestNaukriIsRefusedOnTheRecord(unittest.TestCase):
    def test_naukri_is_listed_as_not_covered_with_a_reason(self):
        self.assertIn("naukri", sources.NOT_COVERED)
        why = sources.NOT_COVERED["naukri"].lower()
        self.assertIn("robots.txt", why)
        self.assertIn("claudebot", why)

    def test_linkedin_and_wellfound_stay_refused(self):
        for board in ("linkedin", "wellfound"):
            self.assertIn(board, sources.NOT_COVERED)


if __name__ == "__main__":
    unittest.main()
