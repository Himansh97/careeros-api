"""A liveness summary has to account for every posting it checked.

The old line counted only `unverified` as unchecked and never counted `unknown`
at all, so a run where Firecrawl rate-limited a dozen postings printed

    55 checked — 10 closed, 42 live, 0 unverifiable

where 10 + 42 = 52. Three postings vanished from their own report, and the line
asserted that nothing was left unverifiable. Reading it, you would conclude
every posting had been determined; in fact three had not been reached at all.

The danger is specific: undetermined silently reads as fine. A posting that
closed while Firecrawl was rate-limited looks exactly like one confirmed open,
and the candidate spends an evening on a form for a job that no longer exists.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.liveness import summarize  # noqa: E402


def _r(verdict: str, why: str = "because"):
    return {"verdict": verdict, "why": why, "company": "X", "title": "Y"}


class LivenessSummaryTests(unittest.TestCase):

    def test_every_result_is_accounted_for(self) -> None:
        """The arithmetic that was wrong: live + closed + undetermined == total."""
        results = ([_r("live")] * 42 + [_r("closed")] * 10
                   + [_r("unknown", "firecrawl HTTP 429")] * 3)
        s = summarize(results)
        self.assertEqual(s["total"], 55)
        self.assertEqual(
            s["counts"].get("live", 0) + s["counts"].get("closed", 0) + s["undetermined"],
            s["total"],
        )

    def test_unknown_counts_as_undetermined_not_as_live(self) -> None:
        s = summarize([_r("live"), _r("unknown", "firecrawl HTTP 429")])
        self.assertEqual(s["counts"].get("live"), 1)
        self.assertEqual(s["undetermined"], 1)

    def test_unverified_also_counts_as_undetermined(self) -> None:
        """Two different words for 'we do not know'. Both must land in the same
        bucket, which is exactly the distinction the old count got wrong."""
        s = summarize([_r("unverified", "no firecrawl key"),
                       _r("unknown", "firecrawl HTTP 429")])
        self.assertEqual(s["undetermined"], 2)

    def test_the_reason_survives_into_the_summary(self) -> None:
        """A count of undetermined postings without the reason is not actionable
        — rate limiting resolves by waiting, a missing key does not."""
        s = summarize([_r("unknown", "firecrawl HTTP 429"),
                       _r("unknown", "firecrawl HTTP 429"),
                       _r("unknown", "no apply URL")])
        self.assertEqual(s["reasons"]["firecrawl HTTP 429"], 2)
        self.assertEqual(s["reasons"]["no apply URL"], 1)

    def test_rate_limiting_is_called_out_separately(self) -> None:
        s = summarize([_r("unknown", "firecrawl HTTP 429"),
                       _r("unknown", "no apply URL")])
        self.assertEqual(s["rate_limited"], 1)

    def test_a_clean_run_reports_nothing_undetermined(self) -> None:
        """The honest version must not cry wolf on a run that really did
        determine everything."""
        s = summarize([_r("live")] * 5 + [_r("closed")] * 2)
        self.assertEqual(s["undetermined"], 0)
        self.assertEqual(s["reasons"], {})
        self.assertEqual(s["rate_limited"], 0)

    def test_an_empty_run_is_not_a_crash(self) -> None:
        s = summarize([])
        self.assertEqual((s["total"], s["undetermined"]), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=1)
