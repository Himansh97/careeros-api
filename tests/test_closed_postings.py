"""A closed posting must stop asking to be applied to.

The liveness check has known for days which postings are gone — it writes
`next_action = "Posting closed — no longer accepting"`. Nothing downstream read
it. Eight of the twenty-six "ready" applications were against postings that no
longer existed, sorted in among the live ones by fit score, and the aging alert
was telling the candidate to go and submit them.

`aging_applications` even carried a comment saying closed postings were skipped
as noise. It sat above no filter, and the query did not select `next_action` at
all — so the intent was written down and never implemented. That is the same
shape as every other bug in this pipeline: the backend knows, and the surface
the candidate actually looks at throws it away.

Three properties are pinned here:

* a closed posting produces no "go send it" alert
* an open one still does — the filter must not swallow real work
* the closure is a field on the application, not a string every caller re-parses
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import alerts, store  # noqa: E402
from app.db import initialize  # noqa: E402
from app.liveness_sync import CLOSED_NOTE, is_closure_note  # noqa: E402


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class ClosedPostingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = pathlib.Path(self.temp.name) / "careeros.db"
        self.patcher = patch.object(store, "DB_PATH", path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        initialize(path=path)

    def _app(self, job_id: str, company: str, next_action: str | None, days: float = 10):
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO applications (id, job_id, title, company, location, source,"
                " status, apply_url, next_action, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"app_{job_id}", job_id, "Data Analyst", company, "Remote", "greenhouse",
                 "ready", "https://example.test/job", next_action, _ago(days), _ago(days)),
            )
            conn.commit()

    # ------------------------------------------------------------------ the bug

    def test_a_closed_posting_does_not_raise_an_aging_alert(self) -> None:
        self._app("gh_dead_1", "Reddit", CLOSED_NOTE)
        aging = [a for a in alerts.aging_applications() if a.ref == "gh_dead_1"]
        self.assertEqual(
            aging, [],
            "a closed posting told the candidate to go and submit it",
        )

    def test_an_open_posting_still_raises_one(self) -> None:
        """The filter must not become a way for real work to disappear."""
        self._app("gh_live_1", "Datadog", None)
        aging = [a for a in alerts.aging_applications() if a.ref == "gh_live_1"]
        self.assertEqual(len(aging), 1, "an open, stale application stopped alerting")

    def test_an_unrelated_next_action_still_raises_one(self) -> None:
        """Only closure silences the alert. Any other note is a different
        problem and must not suppress this one."""
        self._app("gh_live_2", "Stripe", "Waiting on portfolio link")
        aging = [a for a in alerts.aging_applications() if a.ref == "gh_live_2"]
        self.assertEqual(len(aging), 1)

    def test_the_closed_posting_is_still_reported_somewhere(self) -> None:
        """Silencing one alert must not make the fact vanish — otherwise the
        fix is just a quieter version of the same problem."""
        self._app("gh_dead_2", "OpenAI", CLOSED_NOTE)
        closed = [a for a in alerts.blocked_applications()
                  if a.ref == "gh_dead_2" and a.kind == "application_closed"]
        self.assertEqual(len(closed), 1, "the closure stopped being reported at all")

    # ------------------------------------------------- the field, not a string

    def test_applications_carry_the_closure_as_a_field(self) -> None:
        self._app("gh_dead_3", "SoFi", CLOSED_NOTE)
        self._app("gh_live_3", "Ramp", None)
        by_job = {a["jobId"]: a for a in store.list_applications()}
        self.assertTrue(by_job["gh_dead_3"]["postingClosed"])
        self.assertFalse(by_job["gh_live_3"]["postingClosed"])

    def test_one_definition_of_closed_is_shared(self) -> None:
        """blocked_applications used to re-derive this with its own string test.
        Two definitions of the same fact drift."""
        self.assertTrue(is_closure_note(CLOSED_NOTE))
        self.assertTrue(is_closure_note("posting closed"))
        self.assertTrue(is_closure_note("No longer accepting applications"))
        self.assertFalse(is_closure_note(None))
        self.assertFalse(is_closure_note(""))
        self.assertFalse(is_closure_note("Waiting on portfolio link"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
