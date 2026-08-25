"""Filtering by board, and the two ways a facet filter goes wrong.

**The filter must run before the scoring budget.** This is the same lesson as
the `newest` sort, one layer up. Only SCORE_BUDGET of ~7,900 matched postings
are ever scored, so a board filter applied afterwards filters the sample rather
than the pool. Measured on the live feed: Handshake carries 29 postings and 11
of them survive into the scored 600, so a client-side chip would have shown
fewer than half and presented it as the lot.

**The counts on the chips must be computed before the filter.** Counting after
it zeroes every board the candidate has not selected, so choosing "Handshake"
makes every other chip read (0) and there is no way back except clearing the
filter. That is the classic facet bug and it is invisible until someone clicks.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _job(jid: str, source: str, title: str = "Data Analyst", days: float = 1.0) -> dict:
    return {
        "id": jid,
        "title": title,
        "company": {"id": "acme", "name": "Acme"},
        "location": "Dallas, TX",
        "workArrangement": "onsite",
        "source": source,
        "atsPlatform": None,
        "postedAt": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "discoveredAt": None,
        "description": "Build reports in SQL for stakeholders. " * 20,
        "applyUrl": "https://example.test/apply",
    }


POOL = (
    [_job(f"gh{i}", "Greenhouse") for i in range(30)]
    + [_job(f"hs{i}", "Handshake") for i in range(5)]
    + [_job(f"lv{i}", "Lever") for i in range(3)]
)


class SourceFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("app.main.fetch_all_jobs", return_value=POOL)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = TestClient(app)

    def _search(self, **body) -> dict:
        response = self.client.post("/api/jobs/search", json={"limit": 50, **body})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_selecting_a_board_returns_only_that_board(self) -> None:
        data = self._search(sources=["Handshake"])
        self.assertTrue(data["jobs"])
        self.assertEqual({j["source"] for j in data["jobs"]}, {"Handshake"})

    def test_the_counts_do_not_collapse_when_a_board_is_selected(self) -> None:
        """The facet bug. Counting after the filter leaves every other chip at
        (0) and no way back except clearing the filter."""
        unfiltered = self._search()
        filtered = self._search(sources=["Handshake"])
        self.assertEqual(unfiltered["sourceCounts"], filtered["sourceCounts"])
        self.assertEqual(filtered["sourceCounts"]["Greenhouse"], 30)
        self.assertEqual(filtered["sourceCounts"]["Handshake"], 5)

    def test_the_counts_are_pool_counts_not_page_counts(self) -> None:
        """A chip that counted the returned rows would be describing the sample."""
        data = self._search(limit=2)
        self.assertEqual(len(data["jobs"]), 2)
        self.assertEqual(sum(data["sourceCounts"].values()), len(POOL))

    def test_total_reflects_the_filtered_pool(self) -> None:
        """`total` is what the filter matched, so the UI can say 29 of 29 rather
        than implying the rest were held back."""
        self.assertEqual(self._search(sources=["Handshake"])["total"], 5)
        self.assertEqual(self._search()["total"], len(POOL))

    def test_several_boards_can_be_selected_at_once(self) -> None:
        data = self._search(sources=["Handshake", "Lever"])
        self.assertEqual({j["source"] for j in data["jobs"]}, {"Handshake", "Lever"})
        self.assertEqual(data["total"], 8)

    def test_board_names_match_case_insensitively(self) -> None:
        """The chip sends what the API served, but a saved search or a hand-made
        request should not fail on capitalisation."""
        self.assertEqual(self._search(sources=["handshake"])["total"], 5)

    def test_an_empty_or_absent_list_means_every_board(self) -> None:
        self.assertEqual(self._search(sources=[])["total"], len(POOL))
        self.assertEqual(self._search(sources=["  "])["total"], len(POOL))
        self.assertEqual(self._search()["total"], len(POOL))

    def test_an_unknown_board_returns_nothing_rather_than_everything(self) -> None:
        """Failing open here would silently ignore the filter and show the whole
        pool as though it were one board's."""
        data = self._search(sources=["Monster"])
        self.assertEqual(data["jobs"], [])
        self.assertEqual(data["total"], 0)


class BudgetOrderTests(unittest.TestCase):
    def test_the_filter_runs_before_the_scoring_budget(self) -> None:
        """The whole point. Filtering after selection filters the sample: on the
        live feed that was 11 of Handshake's 29 postings."""
        import inspect

        from app import main

        body = inspect.getsource(main.search)
        filter_at = body.index("j[\"source\"].lower() in wanted")
        budget_at = body.index("rank_for_scoring(")
        self.assertLess(
            filter_at, budget_at,
            "the source filter must be applied before the scoring budget is spent",
        )

    def test_the_counts_are_taken_before_the_filter(self) -> None:
        import inspect

        from app import main

        body = inspect.getsource(main.search)
        self.assertLess(
            body.index("source_counts[job[\"source\"]]"),
            body.index("j[\"source\"].lower() in wanted"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
