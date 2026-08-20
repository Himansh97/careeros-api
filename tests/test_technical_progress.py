from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store as app_store
from app.technical_learning.progress import hint_access, progress_overview, submit_guided_attempt


class TechnicalProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(app_store, "DB_PATH", Path(self.tmp.name) / "careeros.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hints_unlock_only_after_failed_checks(self) -> None:
        initial = hint_access("stats-ab-test")
        self.assertFalse(initial["conceptual"])
        self.assertFalse(initial["pattern"])
        submit_guided_attempt("stats-ab-test", "I would watch it.")
        after_one = hint_access("stats-ab-test")
        self.assertTrue(after_one["conceptual"])
        self.assertFalse(after_one["pattern"])
        submit_guided_attempt("stats-ab-test", "Run for longer.")
        after_two = hint_access("stats-ab-test")
        self.assertTrue(after_two["pattern"])
        self.assertTrue(after_two["solutionRevealAvailable"])

    def test_solution_reveal_prevents_clearance(self) -> None:
        result = submit_guided_attempt(
            "stats-ab-test",
            "Randomize each user; measure checkout conversion; guard latency; watch novelty and peeking.",
            solution_revealed=True,
        )
        self.assertTrue(result["grade"]["passed"])
        self.assertFalse(result["cleared"])

    def test_mastery_requires_unaided_practice_and_transfer(self) -> None:
        practice = submit_guided_attempt(
            "stats-ab-test",
            "Randomize each user; measure checkout conversion; guard latency; watch novelty and peeking.",
        )
        self.assertTrue(practice["cleared"])
        before = progress_overview()
        stats = next(item for item in before["skills"] if item["skill"] == "statistics")
        self.assertFalse(stats["mastered"])

        transfer = submit_guided_attempt(
            "stats-lending-policy",
            "Use a staged shadow threshold design; track approval and delinquency, and check subgroup fairness.",
        )
        self.assertTrue(transfer["cleared"])
        after = progress_overview()
        stats = next(item for item in after["skills"] if item["skill"] == "statistics")
        self.assertTrue(stats["mastered"])

    def test_personal_best_tracks_score_not_time_spent(self) -> None:
        submit_guided_attempt("metrics-marketplace-tree", "completed services")
        submit_guided_attempt(
            "metrics-marketplace-tree",
            "Completed successful services; inputs are supply, demand, conversion, repeat; guard cancellation and margin.",
        )
        metric = next(item for item in progress_overview()["skills"] if item["skill"] == "metrics")
        self.assertGreaterEqual(metric["personalBest"], 0.7)
        self.assertNotIn("points", progress_overview())
        self.assertNotIn("streak", progress_overview())


if __name__ == "__main__":
    unittest.main()
