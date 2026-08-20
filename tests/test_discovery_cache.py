"""The job cache serves stale and refreshes behind the request.

Written because the failure it guards is invisible: everything still returns
200, the numbers are all correct, and the only symptom is that one request in
a while takes forty seconds. That is exactly the kind of regression that gets
reintroduced by someone tidying up the cache logic.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import discovery  # noqa: E402
from app.config import CACHE_TTL_SECONDS  # noqa: E402


def _job(jid: str) -> dict:
    return {
        "id": jid,
        "title": "Analyst",
        "company": {"id": "c", "name": "C"},
        "source": "Greenhouse",
    }


class StaleWhileRevalidate(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        discovery._cache.clear()
        discovery._refresh_task = None
        restore = mock.patch.object(discovery, "_restore_durable_cache", return_value=None)
        restore.start()
        self.addCleanup(restore.stop)

    async def asyncTearDown(self) -> None:
        task = discovery._refresh_task
        if task is not None and not task.done():
            task.cancel()
        discovery._cache.clear()
        discovery._refresh_task = None

    async def test_fresh_cache_does_not_refetch(self) -> None:
        discovery._cache["all"] = (time.time(), [_job("a")])
        with mock.patch.object(discovery, "_refresh") as refresh:
            jobs = await discovery.fetch_all_jobs()
        self.assertEqual([j["id"] for j in jobs], ["a"])
        refresh.assert_not_called()

    async def test_expired_cache_returns_immediately(self) -> None:
        """The point of the whole change: the caller does not wait."""
        discovery._cache["all"] = (time.time() - CACHE_TTL_SECONDS - 1, [_job("old")])

        started = asyncio.Event()

        async def slow() -> list[dict]:
            started.set()
            await asyncio.sleep(30)
            return [_job("new")]

        with mock.patch.object(discovery, "_refresh", side_effect=slow):
            jobs = await asyncio.wait_for(discovery.fetch_all_jobs(), timeout=1)
            # Stale content, handed straight back.
            self.assertEqual([j["id"] for j in jobs], ["old"])
            # And the refresh really is running, not skipped.
            await asyncio.wait_for(started.wait(), timeout=1)

    async def test_one_refresh_for_a_burst(self) -> None:
        """Ten callers after expiry must not start ten crawls of every board."""
        discovery._cache["all"] = (time.time() - CACHE_TTL_SECONDS - 1, [_job("old")])
        calls = 0

        async def counted() -> list[dict]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.2)
            return [_job("new")]

        with mock.patch.object(discovery, "_refresh", side_effect=counted):
            await asyncio.gather(*(discovery.fetch_all_jobs() for _ in range(10)))
        self.assertEqual(calls, 1)

    async def test_cold_cache_still_blocks(self) -> None:
        """With nothing cached there is nothing to serve, so the caller waits."""
        async def fetched() -> list[dict]:
            return [_job("fresh")]

        with mock.patch.object(discovery, "_refresh", side_effect=fetched):
            jobs = await discovery.fetch_all_jobs()
        self.assertEqual([j["id"] for j in jobs], ["fresh"])

    async def test_force_blocks_even_with_a_fresh_cache(self) -> None:
        """The explicit refresh button must get current data, not the snapshot."""
        discovery._cache["all"] = (time.time(), [_job("old")])

        async def fetched() -> list[dict]:
            return [_job("forced")]

        with mock.patch.object(discovery, "_refresh", side_effect=fetched):
            jobs = await discovery.fetch_all_jobs(force=True)
        self.assertEqual([j["id"] for j in jobs], ["forced"])

    async def test_background_failure_does_not_reach_the_caller(self) -> None:
        """A board going down mid-refresh must not break the request that
        triggered it — the caller already has its stale answer."""
        discovery._cache["all"] = (time.time() - CACHE_TTL_SECONDS - 1, [_job("old")])

        async def boom() -> list[dict]:
            raise RuntimeError("greenhouse is down")

        with mock.patch.object(discovery, "_refresh", side_effect=boom):
            jobs = await discovery.fetch_all_jobs()
            self.assertEqual([j["id"] for j in jobs], ["old"])
            task = discovery._refresh_task
            assert task is not None
            await task  # must not raise
        self.assertIsNone(task.exception())


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=0).result
    print(f"\n{len(result.failures) + len(result.errors)} failure(s)")
    sys.exit(1 if (result.failures or result.errors) else 0)
