"""The scoring budget decides what the candidate can see.

Full scoring parses a whole description, so only SCORE_BUDGET of the ~8,000
matched postings ever reach the scorer. Everything else is invisible — not
low-ranked, absent. That makes the selection rule as important as the sort
rule, and this repo has now got it wrong twice in the same shape.

The first time, search scored the first 120 in FETCH order and sorted those by
fit: a list ordered by fit but chosen arbitrarily, with a 98-scoring role
sitting unscored at position 452. `rank_for_scoring` fixed that.

The second time is `sort="newest"`. The budget still selected on title fit, so
sorting the result by date reordered a fit-shaped subset and called it recency.
Measured on the live pool: 517 postings were both from the last day and past
the relevance floor, and 441 of them never reached the scorer. "Newest" was
showing the newest 76 of the best-fitting 600.

The rule these tests pin: **the budget must be spent on the axis the caller is
sorting by.**
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.prescreen import build_terms, prescreen_score, rank_for_scoring  # noqa: E402
from app.priority import age_days  # noqa: E402


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class FakeProfile:
    """The three attributes `build_terms` reads. No real candidate data."""

    preferences = {"target_roles": ["Data Analyst", "Business Analyst"]}
    all_skills = frozenset({"sql", "python", "tableau"})
    evidence = ()


def _job(jid: str, title: str, days_old: float | None) -> dict:
    return {
        "id": jid,
        "title": title,
        "postedAt": None if days_old is None else _ago(days_old),
    }


class FitOrderTests(unittest.TestCase):
    """The existing behaviour, so the new mode cannot quietly replace it."""

    def test_the_strongest_titles_win_regardless_of_age(self) -> None:
        jobs = [
            _job("old-strong", "Data Analyst", 40.0),
            _job("new-weak", "Warehouse Associate", 0.1),
        ]
        kept, aside = rank_for_scoring(jobs, FakeProfile(), 1)
        self.assertEqual([j["id"] for j in kept], ["old-strong"])
        self.assertEqual(aside, 1)

    def test_nothing_is_set_aside_when_the_pool_fits(self) -> None:
        jobs = [_job("a", "Data Analyst", 1.0), _job("b", "Sales Rep", 1.0)]
        kept, aside = rank_for_scoring(jobs, FakeProfile(), 10)
        self.assertEqual(kept, jobs)
        self.assertEqual(aside, 0)

    def test_ties_keep_fetch_order(self) -> None:
        """Fetch order interleaves sources, so a tie must not reorder arbitrarily."""
        jobs = [_job(f"j{i}", "Data Analyst", 5.0) for i in range(6)]
        kept, _ = rank_for_scoring(jobs, FakeProfile(), 3)
        self.assertEqual([j["id"] for j in kept], ["j0", "j1", "j2"])


class NewestOrderTests(unittest.TestCase):
    def test_the_budget_goes_to_recent_postings_not_the_best_titles(self) -> None:
        """This is the bug. Under order="fit" the fresh roles never get scored,
        so no amount of sorting afterwards can surface them."""
        jobs = [_job(f"old{i}", "Senior Data Analyst", 30.0) for i in range(5)]
        jobs += [_job(f"new{i}", "Business Analyst", 0.2) for i in range(3)]

        by_fit, _ = rank_for_scoring(jobs, FakeProfile(), 3)
        self.assertTrue(all(j["id"].startswith("old") for j in by_fit))

        by_date, _ = rank_for_scoring(jobs, FakeProfile(), 3, order="newest")
        self.assertEqual({j["id"] for j in by_date}, {"new0", "new1", "new2"})

    def test_recent_but_irrelevant_titles_still_do_not_get_in(self) -> None:
        """"Newest" reorders the roles this candidate would consider. It is not a
        request to abandon the filter — recency alone fills the budget with
        warehouse and sales roles posted this morning."""
        jobs = [_job(f"junk{i}", "Sales Account Executive", 0.01) for i in range(5)]
        jobs += [_job("real", "Data Analyst", 10.0)]
        kept, _ = rank_for_scoring(jobs, FakeProfile(), 2, order="newest")
        self.assertIn("real", [j["id"] for j in kept])

    def test_undated_postings_sort_last_rather_than_first(self) -> None:
        """An absent date is not evidence of freshness, and the sources that omit
        it are aggregators — they would otherwise take the whole budget."""
        jobs = [_job(f"undated{i}", "Data Analyst", None) for i in range(4)]
        jobs += [_job("dated", "Data Analyst", 3.0)]
        kept, _ = rank_for_scoring(jobs, FakeProfile(), 1, order="newest")
        self.assertEqual([j["id"] for j in kept], ["dated"])

    def test_the_budget_is_filled_even_when_few_titles_qualify(self) -> None:
        """A narrow query, or a morning before the boards have posted. Leaving the
        budget unspent would shrink the result rather than making it fresher."""
        jobs = [_job("relevant", "Data Analyst", 1.0)]
        jobs += [_job(f"other{i}", "Facilities Coordinator", 2.0) for i in range(9)]
        kept, aside = rank_for_scoring(jobs, FakeProfile(), 5, order="newest")
        self.assertEqual(len(kept), 5)
        self.assertEqual(aside, 5)
        self.assertEqual(kept[0]["id"], "relevant")

    def test_no_duplicates_when_the_backfill_runs(self) -> None:
        """The backfill draws from the same list the first pass did."""
        jobs = [_job("relevant", "Data Analyst", 1.0)]
        jobs += [_job(f"other{i}", "Facilities Coordinator", 2.0) for i in range(9)]
        kept, _ = rank_for_scoring(jobs, FakeProfile(), 6, order="newest")
        self.assertEqual(len(kept), len({j["id"] for j in kept}))

    def test_the_kept_set_is_newest_first_when_the_budget_bites(self) -> None:
        """Only meaningful under truncation: a pool that fits the budget is
        returned whole and untouched, because selection is this function's job
        and ordering is the caller's."""
        jobs = [
            _job("c", "Data Analyst", 9.0),
            _job("a", "Data Analyst", 0.5),
            _job("dropped", "Data Analyst", 25.0),
            _job("b", "Data Analyst", 4.0),
        ]
        kept, aside = rank_for_scoring(jobs, FakeProfile(), 3, order="newest")
        self.assertEqual([j["id"] for j in kept], ["a", "b", "c"])
        self.assertEqual(aside, 1)

    def test_a_pool_inside_the_budget_is_returned_untouched(self) -> None:
        jobs = [_job("c", "Data Analyst", 9.0), _job("a", "Data Analyst", 0.5)]
        kept, aside = rank_for_scoring(jobs, FakeProfile(), 5, order="newest")
        self.assertEqual(kept, jobs)
        self.assertEqual(aside, 0)

    def test_an_unrecognised_order_falls_back_to_fit(self) -> None:
        jobs = [
            _job("strong", "Data Analyst", 40.0),
            _job("fresh", "Warehouse Associate", 0.1),
        ]
        kept, _ = rank_for_scoring(jobs, FakeProfile(), 1, order="sideways")
        self.assertEqual([j["id"] for j in kept], ["strong"])


class SharedDefinitionTests(unittest.TestCase):
    def test_age_is_read_the_same_way_the_ranking_reads_it(self) -> None:
        """Two definitions of "how old is this posting" would drift. The budget
        and the sort both go through priority.age_days."""
        self.assertIsNone(age_days(None))
        self.assertIsNone(age_days("not-a-date"))
        self.assertAlmostEqual(age_days(_ago(2.0)) or 0.0, 2.0, places=2)
        # Both the offset and the Z form appear in the live feed.
        self.assertIsNotNone(age_days("2026-08-20T16:41:32-04:00"))
        self.assertIsNotNone(age_days("2026-08-20T17:09:03Z"))

    def test_the_relevance_floor_is_the_prescreen_score_itself(self) -> None:
        """Extracted rather than reimplemented, so deleting the filter breaks
        this instead of leaving a test that asserts its own copy of the rule."""
        terms = build_terms(FakeProfile())
        self.assertGreater(prescreen_score(_job("x", "Data Analyst", 1.0), terms), 0)
        self.assertLessEqual(
            prescreen_score(_job("y", "Sales Account Executive", 1.0), terms), 0
        )


class WiringTests(unittest.TestCase):
    def test_search_asks_for_the_order_it_is_about_to_sort_by(self) -> None:
        """The fix is worthless if the endpoint never passes the argument — which
        is exactly how the first version of this bug survived."""
        import inspect

        from app import main

        body = inspect.getsource(main.search)
        self.assertIn('order="newest" if req.sort == "newest" else "fit"', body)


if __name__ == "__main__":
    unittest.main(verbosity=1)
