from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import store as app_store
from app.main import app
from app.technical_learning import datasets


class TechnicalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        db_patch = patch.object(app_store, "DB_PATH", root / "careeros.db")
        dataset_patch = patch.object(datasets, "DATASET_ROOT", root / "datasets")
        db_patch.start()
        dataset_patch.start()
        self.addCleanup(db_patch.stop)
        self.addCleanup(dataset_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_overview_and_curriculum_are_public_and_answer_free(self) -> None:
        overview = self.client.get("/api/prep/technical")
        curriculum = self.client.get("/api/prep/technical/curriculum")
        self.assertEqual(overview.status_code, 200)
        self.assertIn("skills", overview.json())
        self.assertEqual(curriculum.status_code, 200)
        keys = repr(curriculum.json()).lower()
        self.assertNotIn("expected_sql", keys)
        self.assertNotIn("'rubric':", keys)

    def test_drill_includes_schema_but_not_reference_answer(self) -> None:
        response = self.client.get("/api/prep/technical/drills/sql-revenue-by-segment")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(len(body["schema"]), 2)
        self.assertNotIn("expected_sql", body)
        self.assertNotIn("solution", body)
        missing = self.client.get("/api/prep/technical/drills/missing")
        self.assertEqual(missing.status_code, 404)

    def test_run_returns_bounded_results_and_sanitized_errors(self) -> None:
        good = self.client.post(
            "/api/prep/technical/run",
            json={"drillId": "sql-revenue-by-segment", "sql": "SELECT 1 AS value"},
        )
        bad = self.client.post(
            "/api/prep/technical/run",
            json={"drillId": "sql-revenue-by-segment", "sql": "SELECT missing FROM orders"},
        )
        refused = self.client.post(
            "/api/prep/technical/run",
            json={"drillId": "sql-revenue-by-segment", "sql": "ATTACH DATABASE '/tmp/x' AS x"},
        )
        self.assertEqual(good.status_code, 200)
        self.assertTrue(good.json()["ok"])
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(bad.json()["errorCode"], "sql_error")
        self.assertEqual(refused.status_code, 422)
        self.assertNotIn("/tmp/x", refused.text)

    def test_guided_attempt_returns_grade_hints_and_debrief(self) -> None:
        response = self.client.post(
            "/api/prep/technical/attempts",
            json={"drillId": "stats-ab-test", "answer": "watch it", "hintsUnlocked": 0, "solutionRevealed": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["grade"]["passed"])
        self.assertTrue(response.json()["hints"]["conceptual"])
        self.assertIn("debrief", response.json())
        self.assertNotIn("solution", response.json())

        revealed = self.client.post(
            "/api/prep/technical/attempts",
            json={"drillId": "stats-ab-test", "answer": "watch it", "solutionRevealed": True},
        )
        self.assertIn("solution", revealed.json())

    def test_session_routes_preserve_delayed_grading(self) -> None:
        invalid = self.client.post("/api/prep/technical/sessions", json={"durationMinutes": 15})
        self.assertEqual(invalid.status_code, 422)

        created = self.client.post(
            "/api/prep/technical/sessions",
            json={"durationMinutes": 30, "role": "product-analyst"},
        )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["id"]
        started = self.client.post(f"/api/prep/technical/sessions/{session_id}/start")
        self.assertEqual(started.json()["state"], "running")
        self.assertNotIn("scorecard", started.json())

        question_id = started.json()["questions"][0]["id"]
        saved = self.client.patch(
            f"/api/prep/technical/sessions/{session_id}/answers/{question_id}",
            json={"answer": "SELECT 1"},
        )
        self.assertEqual(saved.status_code, 200)
        running = self.client.get(f"/api/prep/technical/sessions/{session_id}")
        self.assertNotIn("scorecard", running.json())

        submitted = self.client.post(f"/api/prep/technical/sessions/{session_id}/submit")
        self.assertEqual(submitted.json()["state"], "graded")
        self.assertIn("scorecard", submitted.json())

    def test_unknown_session_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/prep/technical/sessions/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
