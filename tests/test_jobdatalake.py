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
    """Records the request so the query built for a region can be asserted."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return FakeResponse(self.payload)


ROW = {
    "handle": "acme-data-analyst-ab12c",
    "title": "Data Analyst",
    "company": {"name": "Acme"},
    "locations": ["Mumbai, India"],
    "remote_type": "on_site",
    "description": "<p>SQL and Python.</p>",
    "apply_url": "https://acme.wd1.myworkdayjobs.com/job/123",
    "ats_source": "workday",
    "posted_at": "2026-09-01",
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
        self.assertEqual(job["applyUrl"], ROW["apply_url"])
        # The stamp is the label, which is what test_handshake_source pins:
        # a source an adapter writes but the coverage page cannot name is the
        # drift that test exists to catch, and it caught this one.
        self.assertEqual(job["source"], "JobDataLake")
        self.assertIn(job["source"], sources.SOURCE_LABELS)
        self.assertEqual(job["atsPlatform"], "workday")
        self.assertNotIn("<p>", job["description"])

    def test_a_row_with_no_apply_url_is_dropped(self):
        self.assertEqual(self._fetch({"jobs": [{**ROW, "apply_url": ""}]}), [])

    def test_a_row_with_no_title_is_dropped(self):
        self.assertEqual(self._fetch({"jobs": [{**ROW, "title": "  "}]}), [])

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
