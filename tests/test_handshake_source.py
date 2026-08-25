"""Handshake: the one source with no search endpoint.

Every other adapter narrows server-side — Greenhouse and Ashby are one employer
each, Workday and SmartRecruiters take a query. Handshake takes nothing, so
~59,000 public postings have to be narrowed here, and everything that keeps the
cost bounded is a decision that can silently stop working:

* ids are opened newest-first, because ids are monotonic with `datePosted`
* an id already opened is never opened again, hit or miss
* a transient network failure must NOT be recorded as a miss, or the source
  quietly shrinks over a week of timeouts
* an empty sitemap must raise rather than be cached as "no jobs for six hours"

The parsing has its own live-data hazards. `jobLocation` arrives as a dict on
most postings and a list on some; reading `.get` on the list form raised
AttributeError on the very first posting sampled. And the page title is
"Title | Employer | Handshake", which split-on-pipe mangles for any title
containing a pipe of its own.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import sources  # noqa: E402


def _page(posting: dict) -> str:
    return (
        "<html><head><title>x</title>"
        '<script type="application/ld+json">' + json.dumps(posting) + "</script>"
        "</head><body>ignored</body></html>"
    )


def _posting(**overrides) -> dict:
    base = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Data Analyst | Acme Corp | Handshake",
        "description": "<p>Build dashboards in SQL.</p>",
        "datePosted": "2026-08-20T17:09:03Z",
        "validThrough": "2027-08-20T17:09:03Z",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Dallas",
                "addressRegion": "Texas",
                "addressCountry": "United States",
            },
        },
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    """Records every URL asked for, so the request budget is testable."""

    def __init__(self, routes: dict[str, FakeResponse], fail: set[str] | None = None):
        self.routes = routes
        self.fail = fail or set()
        self.requested: list[str] = []

    async def get(self, url: str, **_):
        self.requested.append(url)
        if url in self.fail:
            import httpx

            raise httpx.ConnectTimeout("boom")
        return self.routes.get(url, FakeResponse("", 404))


def _sitemap(ids: list[int]) -> dict[str, FakeResponse]:
    index = (
        "<sitemapindex><sitemap><loc>https://example.test/s/1</loc></sitemap>"
        "</sitemapindex>"
    )
    urls = "".join(
        f"<url><loc>https://app.joinhandshake.com/public/jobs/{i}</loc></url>"
        for i in ids
    )
    return {
        sources.HANDSHAKE_SITEMAP: FakeResponse(index),
        "https://example.test/s/1": FakeResponse(f"<urlset>{urls}</urlset>"),
    }


class ParsingTests(unittest.TestCase):
    def test_extracts_the_fields_the_pipeline_needs(self) -> None:
        job = sources._handshake_job(11329265, _posting())
        assert job is not None
        self.assertEqual(job["id"], "hs_11329265")
        self.assertEqual(job["title"], "Data Analyst")
        self.assertEqual(job["company"]["name"], "Acme Corp")
        self.assertEqual(job["location"], "Dallas, Texas")
        self.assertEqual(job["source"], "Handshake")
        self.assertEqual(job["postedAt"], "2026-08-20T17:09:03Z")
        self.assertEqual(
            job["applyUrl"], "https://app.joinhandshake.com/public/jobs/11329265"
        )
        self.assertIn("SQL", job["description"])

    def test_title_keeps_its_own_pipes(self) -> None:
        """Split-on-pipe would leave "Data" here."""
        self.assertEqual(
            sources._handshake_title("Data | AI Engineer | Acme | Handshake", "Acme"),
            "Data | AI Engineer",
        )

    def test_job_location_as_a_list_does_not_raise(self) -> None:
        """The live feed sends both shapes. The list form crashed on first use."""
        posting = _posting()
        posting["jobLocation"] = [posting["jobLocation"]]
        job = sources._handshake_job(1, posting)
        assert job is not None
        self.assertEqual(job["location"], "Dallas, Texas")

    def test_foreign_country_is_named_so_us_only_can_act(self) -> None:
        posting = _posting()
        posting["jobLocation"]["address"].update(
            {"addressLocality": "Dublin", "addressRegion": "", "addressCountry": "Ireland"}
        )
        job = sources._handshake_job(1, posting)
        assert job is not None
        self.assertEqual(job["location"], "Dublin, Ireland")

        from app.discovery import us_only

        self.assertEqual(us_only([job]), [])

    def test_telecommute_reads_as_remote(self) -> None:
        posting = _posting(jobLocationType="TELECOMMUTE")
        job = sources._handshake_job(1, posting)
        assert job is not None
        self.assertEqual(job["location"], "Remote")
        self.assertEqual(job["workArrangement"], "remote")

    def test_off_target_titles_are_dropped(self) -> None:
        """97 postings in 100 are hourly work. Without this the pool drowns."""
        self.assertIsNone(
            sources._handshake_job(
                1,
                _posting(
                    title="Warehouse Order Selector | Acme Corp | Handshake",
                    hiringOrganization={"name": "Acme Corp"},
                ),
            )
        )

    def test_expired_postings_never_enter_the_pool(self) -> None:
        self.assertIsNone(
            sources._handshake_job(1, _posting(validThrough="2020-01-01T00:00:00Z"))
        )

    def test_an_unreadable_valid_through_does_not_drop_the_job(self) -> None:
        """A posting is never discarded on the strength of a field we couldn't read."""
        self.assertFalse(sources._handshake_expired(_posting(validThrough="soon")))
        self.assertIsNotNone(sources._handshake_job(1, _posting(validThrough="soon")))

    def test_naive_valid_through_is_compared_as_utc(self) -> None:
        """Not all dates carry a Z. Comparing naive to aware raises TypeError."""
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).replace(
            tzinfo=None
        )
        self.assertTrue(sources._handshake_expired({"validThrough": past.isoformat()}))

    def test_description_free_postings_are_dropped(self) -> None:
        """The scorer reads descriptions. A blank one produces a meaningless score."""
        self.assertIsNone(sources._handshake_job(1, _posting(description="")))

    def test_non_jobposting_ld_blocks_are_ignored(self) -> None:
        page = (
            '<script type="application/ld+json">{"@type":"Organization"}</script>'
            '<script type="application/ld+json">' + json.dumps(_posting()) + "</script>"
        )
        found = sources.parse_handshake_page(page)
        assert found is not None
        self.assertEqual(found["@type"], "JobPosting")

    def test_malformed_ld_json_does_not_raise(self) -> None:
        self.assertIsNone(
            sources.parse_handshake_page('<script type="application/ld+json">{</script>')
        )
        self.assertIsNone(sources.parse_handshake_page(""))


class CrawlTests(unittest.TestCase):
    def setUp(self) -> None:
        sources._handshake_seen.clear()
        sources._handshake_index = (0.0, ())

    def test_ids_come_back_newest_first(self) -> None:
        """Ids are monotonic with datePosted, so descending order IS newest-first.
        Spending a fixed budget from the top only finds fresh roles if this holds."""
        client = FakeClient(_sitemap([500, 900, 100]))
        self.assertEqual(
            asyncio.run(sources.handshake_ids(client)), (900, 500, 100)
        )

    def test_an_empty_sitemap_raises_instead_of_caching_nothing(self) -> None:
        """Caching an empty answer would hide a format change for six hours."""
        client = FakeClient(_sitemap([]))
        with self.assertRaises(ValueError):
            asyncio.run(sources.handshake_ids(client))
        self.assertEqual(sources._handshake_index, (0.0, ()))

    def test_the_budget_caps_how_many_pages_a_crawl_opens(self) -> None:
        ids = list(range(1, 60))
        routes = _sitemap(ids)
        client = FakeClient(routes)
        original = sources.HANDSHAKE_DETAIL_CAP
        sources.HANDSHAKE_DETAIL_CAP = 5
        try:
            asyncio.run(sources.handshake(client))
        finally:
            sources.HANDSHAKE_DETAIL_CAP = original
        opened = [u for u in client.requested if "/public/jobs/" in u]
        self.assertEqual(len(opened), 5)

    def test_a_second_crawl_only_opens_ids_it_has_not_seen(self) -> None:
        """Without the memo, 391 hourly-work pages get re-read every 15 minutes."""
        routes = _sitemap([1, 2])
        routes["https://app.joinhandshake.com/public/jobs/1"] = FakeResponse(
            _page(_posting())
        )
        routes["https://app.joinhandshake.com/public/jobs/2"] = FakeResponse(
            _page(_posting(title="Cashier | Acme Corp | Handshake"))
        )
        client = FakeClient(routes)
        first = asyncio.run(sources.handshake(client))
        self.assertEqual([j["id"] for j in first], ["hs_1"])

        client.requested.clear()
        second = asyncio.run(sources.handshake(client))
        self.assertEqual([u for u in client.requested if "/public/jobs/" in u], [])
        # And the match is still returned, not just the ids opened this pass.
        self.assertEqual([j["id"] for j in second], ["hs_1"])

    def test_a_timeout_is_retried_and_a_404_is_not(self) -> None:
        """A blip must not blacklist a posting for the life of the process."""
        routes = _sitemap([1, 2])
        routes["https://app.joinhandshake.com/public/jobs/2"] = FakeResponse("", 404)
        client = FakeClient(
            routes, fail={"https://app.joinhandshake.com/public/jobs/1"}
        )
        asyncio.run(sources.handshake(client))
        self.assertNotIn(1, sources._handshake_seen)
        self.assertIn(2, sources._handshake_seen)

        client.requested.clear()
        asyncio.run(sources.handshake(client))
        opened = [u for u in client.requested if "/public/jobs/" in u]
        self.assertEqual(opened, ["https://app.joinhandshake.com/public/jobs/1"])

    def test_results_are_returned_newest_posted_first(self) -> None:
        routes = _sitemap([1, 2])
        routes["https://app.joinhandshake.com/public/jobs/1"] = FakeResponse(
            _page(_posting(datePosted="2026-01-01T00:00:00Z"))
        )
        routes["https://app.joinhandshake.com/public/jobs/2"] = FakeResponse(
            _page(_posting(datePosted="2026-08-01T00:00:00Z"))
        )
        jobs = asyncio.run(sources.handshake(FakeClient(routes)))
        self.assertEqual([j["id"] for j in jobs], ["hs_2", "hs_1"])

    def test_the_sitemap_is_not_refetched_within_its_ttl(self) -> None:
        """It is ~5MB. Refetching it every 15-minute refresh is 480MB a day."""
        client = FakeClient(_sitemap([1]))
        asyncio.run(sources.handshake_ids(client))
        before = len(client.requested)
        asyncio.run(sources.handshake_ids(client))
        self.assertEqual(len(client.requested), before)


class RegisteredTests(unittest.TestCase):
    def test_handshake_is_wired_into_the_crawl(self) -> None:
        """An adapter nothing calls is the failure this repo keeps rediscovering."""
        import inspect

        self.assertIn(
            'handshake(client), "handshake"',
            inspect.getsource(sources.fetch_source_results),
        )

    def test_every_source_an_adapter_stamps_is_reported(self) -> None:
        """The health endpoint's source list was hand-maintained and drifted two
        adapters out of date — Workday and SmartRecruiters had been feeding the
        pipeline for weeks while the page that lists coverage did not name them.
        Handshake would have been the third."""
        import re

        stamped = set(re.findall(r'source="([^"]+)"', inspect_module_source()))
        self.assertTrue(stamped, "no source= literals found; the regex has rotted")
        # Both directions. A label nothing stamps is the same drift the other
        # way round: the page would advertise coverage that does not exist.
        self.assertEqual(stamped, set(sources.SOURCE_LABELS))

    def test_the_health_endpoint_does_not_restate_the_list(self) -> None:
        import inspect

        from app import main

        body = inspect.getsource(main.health)
        self.assertIn("SOURCE_LABELS", body)
        self.assertNotIn('"Arbeitnow"', body)

    def test_wellfound_is_not_implemented(self) -> None:
        """Its terms name 'automated or non-automated harvesting, collection or
        "scraping"', and /company/*/jobs answers a non-browser client with a bot
        challenge. Same line as LinkedIn and Indeed. If someone adds it, this fails."""
        source = inspect_module_source()
        self.assertNotIn("wellfound.com/role", source)
        self.assertNotIn("apolloState", source)
        self.assertIn("Wellfound", sources.__doc__ or "")
        # And the reason is served to the UI, not just left in a docstring.
        self.assertIn("wellfound", sources.NOT_COVERED)


def inspect_module_source() -> str:
    import inspect

    return inspect.getsource(sources)


if __name__ == "__main__":
    unittest.main(verbosity=1)
