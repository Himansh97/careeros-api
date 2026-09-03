"""The chosen market must survive a restart.

It did not. The region lived in a module-level list, so every restart and
every --reload put discovery back on the United States while the candidate was
working through India postings, with nothing said. These tests are written
against the store rather than the module global, because reading the global
back would pass without proving anything about a new process.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app import discovery


class RegionPersistenceTests(unittest.TestCase):
    def setUp(self):
        from app.store import connect

        with connect() as conn:
            self._prior = conn.execute(
                "SELECT value FROM app_setting WHERE key=?", (discovery._REGION_KEY,)
            ).fetchone()
            conn.execute("DELETE FROM app_setting WHERE key=?", (discovery._REGION_KEY,))

    def tearDown(self):
        from app.store import connect, now

        with connect() as conn:
            conn.execute("DELETE FROM app_setting WHERE key=?", (discovery._REGION_KEY,))
            if self._prior:
                conn.execute(
                    "INSERT INTO app_setting (key, value, updated_at) VALUES (?,?,?)",
                    (discovery._REGION_KEY, self._prior["value"], now()),
                )

    def _stored(self):
        from app.store import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_setting WHERE key=?", (discovery._REGION_KEY,)
            ).fetchone()
        return row["value"] if row else None

    def test_nothing_stored_means_the_default(self):
        self.assertEqual(discovery.active_region(), "us")

    def test_a_choice_is_written_to_the_store(self):
        """The assertion that matters: it reaches the database, not a global."""
        discovery.set_region("in")
        self.assertEqual(self._stored(), "in")

    def test_a_choice_is_read_back(self):
        discovery.set_region("in")
        self.assertEqual(discovery.active_region(), "in")

    def test_choosing_again_overwrites_rather_than_duplicating(self):
        from app.store import connect

        discovery.set_region("in")
        discovery.set_region("us")
        with connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) c FROM app_setting WHERE key=?", (discovery._REGION_KEY,)
            ).fetchone()["c"]
        self.assertEqual(n, 1)
        self.assertEqual(discovery.active_region(), "us")

    def test_an_unknown_region_is_not_stored(self):
        discovery.set_region("atlantis")
        self.assertEqual(discovery.active_region(), "us")

    def test_a_stored_region_that_no_longer_exists_falls_back(self):
        """A market could be removed from REGIONS while a row still names it."""
        from app.store import connect, now

        with connect() as conn:
            conn.execute(
                "INSERT INTO app_setting (key, value, updated_at) VALUES (?,?,?)",
                (discovery._REGION_KEY, "atlantis", now()),
            )
        self.assertEqual(discovery.active_region(), "us")

    def test_a_store_failure_does_not_stop_discovery(self):
        """The daily fetch runs on a schedule. A settings read failing is not a
        reason for it to stop; falling back to the default market is."""
        with mock.patch("app.store.connect", side_effect=RuntimeError("locked")):
            self.assertEqual(discovery.active_region(), "us")

    def test_switching_market_drops_the_cached_pool(self):
        """The cache holds one pool. Serving US postings under an India heading
        is the quiet wrong answer this codebase refuses."""
        discovery._cache["all"] = (0.0, [{"title": "stale"}])
        discovery.set_region("in")
        self.assertNotIn("all", discovery._cache)


if __name__ == "__main__":
    unittest.main()
