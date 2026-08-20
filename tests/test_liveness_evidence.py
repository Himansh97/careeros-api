from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.db import connect, initialize
from app import discovery_store, liveness, liveness_sync


class LivenessEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "careeros.db"
        initialize(path=self.path)
        patches = (
            mock.patch.object(liveness_sync, "DB_PATH", self.path),
            mock.patch.object(discovery_store, "DB_PATH", self.path),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self._seed("app_gh_acme_1", "gh_acme_1")
        self._seed("app_imported_1", "imported_1")

    def _seed(self, app_id: str, job_id: str) -> None:
        with connect(path=self.path) as connection:
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, next_action, created_at, updated_at) "
                "VALUES (?,?, 'Analyst','Acme','ready','Review and approve','t','t')",
                (app_id, job_id),
            )

    def _generation(
        self, state: discovery_store.SourceState = discovery_store.SourceState.HEALTHY,
        error_code: str | None = None,
    ) -> discovery_store.SourceGeneration:
        return discovery_store.record_generation(
            discovery_store.SourceResult(
                "greenhouse/acme", state, (), error_code=error_code
            )
        )

    def _evidence(
        self,
        kind: str,
        generation: discovery_store.SourceGeneration | None = None,
        *,
        job_id: str = "gh_acme_1",
        source_key: str | None = "greenhouse/acme",
        state: discovery_store.SourceState = discovery_store.SourceState.HEALTHY,
        detail_code: str = "observed",
    ) -> liveness_sync.LivenessEvidence:
        return liveness_sync.LivenessEvidence(
            job_id=job_id,
            source_key=source_key,
            observation_kind=kind,
            generation_id=generation.id if generation else None,
            source_state=state,
            observed_at="2026-08-20T12:00:00+00:00",
            detail_code=detail_code,
        )

    def _application(self, app_id: str = "app_gh_acme_1") -> dict:
        with connect(read_only=True, path=self.path) as connection:
            return dict(connection.execute(
                "SELECT * FROM applications WHERE id=?", (app_id,)
            ).fetchone())

    def test_present_and_direct_live_are_live(self) -> None:
        generation = self._generation()
        summary = liveness_sync.apply_evidence((
            self._evidence("present", generation),
            self._evidence("direct_live", None, detail_code="http_200"),
        ))
        self.assertEqual(summary.counts["live"], 2)
        self.assertEqual(self._application()["liveness_verdict"], "live")

    def test_direct_closed_closes_immediately(self) -> None:
        summary = liveness_sync.apply_evidence((
            self._evidence("direct_closed", None, detail_code="http_410"),
        ))
        app = self._application()
        self.assertEqual(summary.marked, 1)
        self.assertEqual(app["liveness_verdict"], "closed")
        self.assertEqual(app["next_action"], liveness_sync.CLOSED_NOTE)

    def test_two_separate_healthy_absences_are_required(self) -> None:
        first = self._generation()
        second = self._generation()

        one = liveness_sync.apply_evidence((self._evidence("absent", first),))
        self.assertEqual(one.counts["unknown"], 1)
        self.assertNotEqual(self._application()["next_action"], liveness_sync.CLOSED_NOTE)

        repeated = liveness_sync.apply_evidence((self._evidence("absent", first),))
        self.assertEqual(repeated.marked, 0)

        two = liveness_sync.apply_evidence((self._evidence("absent", second),))
        self.assertEqual(two.marked, 1)
        self.assertEqual(self._application()["liveness_verdict"], "closed")

    def test_degraded_missing_owner_and_imported_absence_stay_unknown(self) -> None:
        degraded = self._generation(discovery_store.SourceState.DEGRADED, "timeout")
        evidence = (
            self._evidence(
                "absent", degraded,
                state=discovery_store.SourceState.DEGRADED,
                detail_code="timeout",
            ),
            self._evidence("absent", None, source_key=None, detail_code="missing_owner"),
            self._evidence(
                "absent", None, job_id="imported_1", source_key="imported/manual",
                detail_code="pool_absence",
            ),
        )
        summary = liveness_sync.apply_evidence(evidence)
        self.assertEqual(summary.counts["unknown"], 3)
        self.assertEqual(summary.marked, 0)
        self.assertNotEqual(
            self._application("app_imported_1")["next_action"],
            liveness_sync.CLOSED_NOTE,
        )

    def test_later_presence_retracts_closure_and_records_event(self) -> None:
        liveness_sync.apply_evidence((
            self._evidence("direct_closed", detail_code="http_404"),
        ))
        generation = self._generation()

        summary = liveness_sync.apply_evidence((self._evidence("present", generation),))

        app = self._application()
        self.assertEqual(summary.cleared, 1)
        self.assertEqual(app["liveness_verdict"], "live")
        self.assertEqual(app["next_action"], "")
        with connect(read_only=True, path=self.path) as connection:
            latest = connection.execute(
                "SELECT prior_verdict, new_verdict, evidence_kind, generation_id "
                "FROM liveness_event ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(tuple(latest), ("closed", "live", "present", generation.id))

    def test_projection_and_event_roll_back_together(self) -> None:
        with mock.patch.object(liveness_sync, "add_timeline", side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                liveness_sync.apply_evidence((
                    self._evidence("direct_closed", detail_code="http_404"),
                ))
        app = self._application()
        self.assertIsNone(app["liveness_verdict"])
        with connect(read_only=True, path=self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM liveness_event").fetchone()[0]
        self.assertEqual(count, 0)

    def test_snapshot_builder_never_turns_degradation_into_absence(self) -> None:
        posting = {
            "id": "gh_acme_1",
            "title": "Analyst",
            "company": {"id": "acme", "name": "Acme"},
            "source": "Greenhouse",
        }
        healthy = discovery_store.record_generation(
            discovery_store.SourceResult(
                "greenhouse/acme", discovery_store.SourceState.HEALTHY, (posting,)
            )
        )
        present = liveness.evidence_from_snapshot(
            [{"jobId": "gh_acme_1"}], discovery_store.current_snapshot()
        )[0]
        self.assertEqual((present.observation_kind, present.generation_id), ("present", healthy.id))

        degraded = discovery_store.record_generation(
            discovery_store.SourceResult(
                "greenhouse/acme",
                discovery_store.SourceState.DEGRADED,
                (),
                "timeout",
            )
        )
        unknown = liveness.evidence_from_snapshot(
            [{"jobId": "gh_acme_1"}], discovery_store.current_snapshot()
        )[0]
        self.assertEqual(unknown.observation_kind, "unknown")
        self.assertEqual(unknown.generation_id, degraded.id)
        self.assertEqual(unknown.detail_code, "timeout")


if __name__ == "__main__":
    unittest.main()
