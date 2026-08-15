"""The spend limit has to be a control, not a receipt.

A dashboard that reports overspend after the fact is bookkeeping. What matters
is that `check_budget()` refuses *before* the request, and that a refusal is
handled the same way a missing API key is — fall back to the deterministic
resume, never fail to produce one.

Also pins the arithmetic against the published per-million-token rates, because
a pricing table that silently drifts turns the whole ledger into fiction.

    ./.venv/bin/python tests/test_usage.py
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


class Ledger(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.dir.name) / "t.db"

        import sqlite3

        def connect():
            c = sqlite3.connect(self.db)
            c.row_factory = sqlite3.Row
            return c

        from app import usage

        self.usage = importlib.reload(usage)
        self.p = mock.patch.object(self.usage, "connect", connect)
        self.p.start()

    def tearDown(self) -> None:
        self.p.stop()
        self.dir.cleanup()

    # ── pricing ──────────────────────────────────────────────────────────
    def test_sonnet_matches_published_rates(self) -> None:
        """$2/MTok in, $10/MTok out."""
        got = self.usage.cost_usd(
            "claude-sonnet-5", input_tokens=50_000, output_tokens=15_000
        )
        self.assertAlmostEqual(got, 50_000 * 2 / 1e6 + 15_000 * 10 / 1e6, places=10)

    def test_cache_reads_are_a_tenth_of_input(self) -> None:
        base = self.usage.cost_usd("claude-sonnet-5", input_tokens=100_000)
        cached = self.usage.cost_usd("claude-sonnet-5", cache_read_tokens=100_000)
        self.assertAlmostEqual(cached, base / 10, places=10)

    def test_an_unknown_model_is_never_free(self) -> None:
        """The dangerous failure: a new model recorded at $0 breaks the budget
        while looking like frugality."""
        got = self.usage.cost_usd("claude-not-yet-priced", input_tokens=1_000_000)
        self.assertGreater(got, 0)

    # ── the control ──────────────────────────────────────────────────────
    def test_under_budget_allows_a_call(self) -> None:
        self.assertIsNone(self.usage.check_budget())

    def test_exceeding_the_daily_cap_blocks(self) -> None:
        self.usage.DAILY_BUDGET_USD = 0.001
        self.usage.record("claude-sonnet-5", "t", input_tokens=1_000_000)  # $2.00
        reason = self.usage.check_budget()
        self.assertIsNotNone(reason)
        self.assertIn("daily", reason)

    def test_exceeding_the_monthly_cap_blocks(self) -> None:
        self.usage.DAILY_BUDGET_USD = 0        # disabled
        self.usage.MONTHLY_BUDGET_USD = 0.001
        self.usage.record("claude-sonnet-5", "t", input_tokens=1_000_000)
        reason = self.usage.check_budget()
        self.assertIsNotNone(reason)
        self.assertIn("monthly", reason)

    def test_a_zero_cap_means_no_limit_not_no_spending(self) -> None:
        """0 disables the cap. Reading it as "budget of zero, block everything"
        would be a silent kill switch on a config default."""
        self.usage.DAILY_BUDGET_USD = 0
        self.usage.MONTHLY_BUDGET_USD = 0
        self.usage.record("claude-sonnet-5", "t", input_tokens=10_000_000)
        self.assertIsNone(self.usage.check_budget())

    def test_the_block_message_is_readable_below_a_cent(self) -> None:
        """'$0.00 of $0.00 spent today' told the user nothing."""
        self.usage.DAILY_BUDGET_USD = 0.00005
        self.usage.record("claude-sonnet-5", "t", input_tokens=16, output_tokens=4)
        reason = self.usage.check_budget()
        self.assertNotIn("$0.00 of", reason)

    # ── the ledger ───────────────────────────────────────────────────────
    def test_failed_calls_are_recorded(self) -> None:
        """A ledger of successes only drifts from the real bill."""
        self.usage.record("claude-sonnet-5", "t", input_tokens=100, ok=False)
        s = self.usage.summary(30)
        self.assertEqual(s["totals"]["calls"], 1)
        self.assertEqual(s["totals"]["failed"], 1)

    def test_budget_compares_unrounded(self) -> None:
        """Display rounds to 4dp; the comparison must not, or sub-$0.0001 calls
        would accumulate invisibly against the cap."""
        self.usage.DAILY_BUDGET_USD = 0.00006
        self.usage.record("claude-sonnet-5", "t", input_tokens=16, output_tokens=4)
        state = self.usage.budget_state()
        self.assertEqual(state["today"], 0.0001)   # rounded for display
        self.assertTrue(state["blocked"])          # but blocked on the real value


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=0).result
    print(f"\n{len(result.failures) + len(result.errors)} failure(s)")
    sys.exit(1 if (result.failures or result.errors) else 0)
