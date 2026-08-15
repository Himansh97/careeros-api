"""Closure flags must be retractable.

The bug this guards was invisible from every screen: applications carried
"Posting closed — no longer accepting" while their postings sat in the live
pool, the alert list showed them as dead, and the commit criteria held them at
NO-GO. Nothing errored. An audit found 16 of 22 API-sourced applications in
that state, and clearing them moved seven approvals from held to go.

The cause was that the verdict was applied in one direction only — `closed`
wrote the flag, `live` did nothing — so a single bad fetch was permanent.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import liveness_sync  # noqa: E402
from app.liveness_sync import CLOSED_NOTE, apply_verdicts  # noqa: E402


class Sync(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.dir.name) / "t.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE applications (
              id TEXT PRIMARY KEY, job_id TEXT, next_action TEXT, updated_at TEXT
            );
            CREATE TABLE timeline (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              application_id TEXT, label TEXT, at TEXT
            );
            """
        )
        conn.commit()
        conn.close()

        def connect():
            c = sqlite3.connect(self.db)
            c.row_factory = sqlite3.Row
            return c

        def add_timeline(conn, app_id, label):
            conn.execute(
                "INSERT INTO timeline (application_id, label, at) VALUES (?,?,?)",
                (app_id, label, "now"),
            )

        self.patches = [
            mock.patch.object(liveness_sync, "connect", connect),
            mock.patch.object(liveness_sync, "add_timeline", add_timeline),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def _seed(self, job_id: str, note: str) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO applications (id, job_id, next_action, updated_at) VALUES (?,?,?,?)",
            (f"app_{job_id}", job_id, note, "then"),
        )
        conn.commit()
        conn.close()

    def _note(self, job_id: str) -> str:
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT next_action FROM applications WHERE id=?", (f"app_{job_id}",)
        ).fetchone()
        conn.close()
        return row[0]

    def test_live_posting_clears_a_stale_closure(self) -> None:
        """The whole point: a posting that came back stops being marked dead."""
        self._seed("gh_x_1", CLOSED_NOTE)
        out = apply_verdicts(
            [{"jobId": "gh_x_1", "verdict": "live", "why": "present on its board"}],
            [{"jobId": "gh_x_1", "nextAction": CLOSED_NOTE}],
        )
        self.assertEqual(out["cleared"], 1)
        self.assertEqual(self._note("gh_x_1"), "")

    def test_closed_posting_is_marked(self) -> None:
        self._seed("gh_x_2", "")
        out = apply_verdicts(
            [{"jobId": "gh_x_2", "verdict": "closed", "why": "gone from its board"}],
            [{"jobId": "gh_x_2", "nextAction": ""}],
        )
        self.assertEqual(out["marked"], 1)
        self.assertEqual(self._note("gh_x_2"), CLOSED_NOTE)

    def test_already_closed_is_not_rewritten(self) -> None:
        """Re-marking would push a duplicate timeline entry every single run."""
        self._seed("gh_x_3", CLOSED_NOTE)
        out = apply_verdicts(
            [{"jobId": "gh_x_3", "verdict": "closed", "why": "still gone"}],
            [{"jobId": "gh_x_3", "nextAction": CLOSED_NOTE}],
        )
        self.assertEqual(out["marked"], 0)
        conn = sqlite3.connect(self.db)
        n = conn.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_other_notes_survive_a_live_verdict(self) -> None:
        """Clearing is narrow. A blocker someone else recorded is not ours to
        delete just because the posting is reachable."""
        self._seed("gh_x_4", "Security code required — resubmit the application")
        out = apply_verdicts(
            [{"jobId": "gh_x_4", "verdict": "live", "why": "present"}],
            [{"jobId": "gh_x_4", "nextAction": "Security code required — resubmit the application"}],
        )
        self.assertEqual(out["cleared"], 0)
        self.assertIn("Security code", self._note("gh_x_4"))

    def test_unverified_changes_nothing(self) -> None:
        """No evidence is not evidence of either state."""
        self._seed("indeed_5", CLOSED_NOTE)
        out = apply_verdicts(
            [{"jobId": "indeed_5", "verdict": "unverified", "why": "no key"}],
            [{"jobId": "indeed_5", "nextAction": CLOSED_NOTE}],
        )
        self.assertEqual((out["marked"], out["cleared"]), (0, 0))
        self.assertEqual(self._note("indeed_5"), CLOSED_NOTE)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=0).result
    print(f"\n{len(result.failures) + len(result.errors)} failure(s)")
    sys.exit(1 if (result.failures or result.errors) else 0)
