from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.db import connect, initialize
from app import discovery_store
from app import discovery
from app import sources
import httpx


def job(job_id: str = "gh_acme_1") -> dict:
    return {
        "id": job_id,
        "title": "Data Analyst",
        "company": {"name": "Acme", "id": "acme"},
        "location": "Chicago, IL",
        "workArrangement": "hybrid",
        "source": "Greenhouse",
        "atsPlatform": "Greenhouse",
        "postedAt": "2026-08-20T00:00:00Z",
        "discoveredAt": "volatile",
        "description": "Analyze trusted data.",
        "applyUrl": "https://boards.example/jobs/1",
        "unexpectedPrivateField": "must not persist",
    }


class DiscoveryGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "careeros.db"
        initialize(path=self.path)
        patcher = mock.patch.object(discovery_store, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_payload_hash_is_canonical_and_excludes_volatile_fields(self) -> None:
        left = job()
        right = dict(reversed(list(left.items())))
        right["discoveredAt"] = "later"
        right["unexpectedPrivateField"] = "different"

        self.assertEqual(
            discovery_store.hash_job_payload(left),
            discovery_store.hash_job_payload(right),
        )
        normalized = discovery_store.normalize_job_payload(left)
        self.assertNotIn("discoveredAt", normalized)
        self.assertNotIn("unexpectedPrivateField", normalized)
        self.assertEqual(normalized["company"], {"id": "acme", "name": "Acme"})

    def test_healthy_generation_survives_a_cold_process(self) -> None:
        generation = discovery_store.record_generation(
            discovery_store.SourceResult(
                source_key="greenhouse/acme",
                state=discovery_store.SourceState.HEALTHY,
                jobs=(job(),),
            )
        )

        snapshot = discovery_store.current_snapshot()

        self.assertEqual([item["id"] for item in snapshot.jobs], ["gh_acme_1"])
        self.assertFalse(snapshot.jobs[0]["stale"])
        self.assertEqual(snapshot.jobs[0]["sourceGenerationId"], generation.id)
        self.assertEqual(snapshot.sources[0].state, discovery_store.SourceState.HEALTHY)

    def test_failed_generation_carries_forward_latest_healthy_jobs(self) -> None:
        healthy = discovery_store.record_generation(
            discovery_store.SourceResult(
                "greenhouse/acme", discovery_store.SourceState.HEALTHY, (job(),)
            )
        )
        failed = discovery_store.record_generation(
            discovery_store.SourceResult(
                "greenhouse/acme",
                discovery_store.SourceState.DEGRADED,
                (),
                error_code="timeout",
            )
        )

        snapshot = discovery_store.current_snapshot()

        self.assertEqual(snapshot.sources[0].generation_id, failed.id)
        self.assertEqual(snapshot.sources[0].error_code, "timeout")
        self.assertTrue(snapshot.jobs[0]["stale"])
        self.assertEqual(snapshot.jobs[0]["sourceGenerationId"], healthy.id)
        with connect(read_only=True, path=self.path) as connection:
            row = connection.execute(
                "SELECT state, job_count, error_code, error_summary "
                "FROM source_generation WHERE id=?",
                (failed.id,),
            ).fetchone()
        self.assertEqual(dict(row), {
            "state": "degraded",
            "job_count": 0,
            "error_code": "timeout",
            "error_summary": None,
        })

    def test_pruning_keeps_thirty_generations_and_referenced_history(self) -> None:
        generations = [
            discovery_store.record_generation(
                discovery_store.SourceResult(
                    "greenhouse/acme",
                    discovery_store.SourceState.HEALTHY,
                    (job(f"gh_acme_{index}"),),
                )
            )
            for index in range(32)
        ]
        with connect(path=self.path) as connection:
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, created_at, updated_at) "
                "VALUES ('app_1','gh_acme_1','Analyst','Acme','qualified','t','t')"
            )
            connection.execute(
                "INSERT INTO liveness_event "
                "(application_id, new_verdict, evidence_kind, generation_id, "
                "reason_code, observed_at, created_at) "
                "VALUES ('app_1','live','present',?,'present','t','t')",
                (generations[0].id,),
            )

        deleted = discovery_store.prune_generations(keep=30)

        self.assertEqual(deleted, 1)
        with connect(read_only=True, path=self.path) as connection:
            ids = {
                row[0]
                for row in connection.execute("SELECT id FROM source_generation")
            }
            payload = connection.execute(
                "SELECT payload_json FROM job_observation WHERE generation_id=?",
                (generations[0].id,),
            ).fetchone()[0]
        self.assertIn(generations[0].id, ids)
        self.assertNotIn(generations[1].id, ids)
        self.assertNotIn("unexpectedPrivateField", json.loads(payload))


class SourceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_errors_become_stable_codes_without_exception_text(self) -> None:
        request = httpx.Request("GET", "https://example.test/jobs")
        response = httpx.Response(429, request=request)

        async def failed():
            raise httpx.HTTPStatusError(
                "secret upstream response", request=request, response=response
            )

        result = await sources._safe_result(failed(), "greenhouse/acme")

        self.assertEqual(result.state, discovery_store.SourceState.DEGRADED)
        self.assertEqual(result.error_code, "rate_limited")
        self.assertEqual(result.jobs, ())
        self.assertNotIn("secret", repr(result))

    def test_exception_categories_are_explicit(self) -> None:
        request = httpx.Request("GET", "https://example.test/jobs")
        self.assertEqual(
            sources.classify_source_error(httpx.ReadTimeout("late", request=request)),
            "timeout",
        )
        self.assertEqual(sources.classify_source_error(ValueError("bad json")), "parse")
        self.assertEqual(
            sources.classify_source_error(httpx.ConnectError("down", request=request)),
            "network",
        )


class DiscoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.path = Path(self.temp.name) / "careeros.db"
        initialize(path=self.path)
        self.path_patch = mock.patch.object(discovery_store, "DB_PATH", self.path)
        self.path_patch.start()
        discovery._cache.clear()
        discovery._refresh_task = None

    async def _cleanup(self) -> None:
        self.path_patch.stop()
        discovery._cache.clear()
        discovery._refresh_task = None
        self.temp.cleanup()

    async def test_refresh_persists_and_cold_cache_recovers_without_network(self) -> None:
        result = discovery_store.SourceResult(
            "greenhouse/acme", discovery_store.SourceState.HEALTHY, (job(),)
        )
        with (
            mock.patch.object(sources, "fetch_source_results", new=mock.AsyncMock(return_value=(result,))),
            mock.patch("app.imported.list_imported", return_value=[]),
        ):
            refreshed = await discovery._refresh()
        self.assertEqual([item["id"] for item in refreshed], ["gh_acme_1"])

        discovery._cache.clear()
        with mock.patch.object(sources, "fetch_source_results") as fetch:
            restored = await discovery.fetch_all_jobs()

        self.assertEqual([item["id"] for item in restored], ["gh_acme_1"])
        self.assertEqual(restored[0]["origin"], "fetched")
        fetch.assert_not_called()

    async def test_failed_refresh_uses_durable_carry_forward(self) -> None:
        healthy = discovery_store.SourceResult(
            "greenhouse/acme", discovery_store.SourceState.HEALTHY, (job(),)
        )
        failed = discovery_store.SourceResult(
            "greenhouse/acme",
            discovery_store.SourceState.DEGRADED,
            (),
            "timeout",
        )
        with mock.patch("app.imported.list_imported", return_value=[]):
            with mock.patch.object(
                sources, "fetch_source_results", new=mock.AsyncMock(return_value=(healthy,))
            ):
                await discovery._refresh()
            with mock.patch.object(
                sources, "fetch_source_results", new=mock.AsyncMock(return_value=(failed,))
            ):
                jobs = await discovery._refresh()

        self.assertEqual([item["id"] for item in jobs], ["gh_acme_1"])
        self.assertTrue(jobs[0]["stale"])
        self.assertEqual(discovery.failed_sources(), ["greenhouse/acme: timeout"])


if __name__ == "__main__":
    unittest.main()
