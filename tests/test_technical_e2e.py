from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pathlib
import sys

# Every other test file in this repo carries this. Without it, running the way
# AGENTS.md documents — `./.venv/bin/python tests/<file>.py` — dies on
# "No module named 'app'", so eight passing suites read as a broken suite.
# `tests/test_recruiter_messages.py` records this exact bug happening before.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import store as app_store
from app.main import app
from app.technical_learning import datasets


class TechnicalLearningEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(app_store, "DB_PATH", root / "careeros.db"),
            patch.object(datasets, "DATASET_ROOT", root / "datasets"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _attempt(self, drill_id: str, answer: object) -> dict[str, object]:
        response = self.client.post(
            "/api/prep/technical/attempts",
            json={"drillId": drill_id, "answer": answer},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_complete_guided_and_timed_synthetic_journey(self) -> None:
        sql = """
            SELECT c.segment, ROUND(SUM(o.revenue), 2) AS revenue
            FROM customers c JOIN orders o ON o.customer_id = c.customer_id
            WHERE o.status = 'paid'
            GROUP BY c.segment
            ORDER BY revenue DESC
        """
        self.assertTrue(self._attempt("sql-revenue-by-segment", sql)["grade"]["passed"])
        self.assertTrue(self._attempt("sql-lending-delinquency", """
            SELECT b.fico_band, COUNT(*) AS loan_count,
                   SUM(CASE WHEN l.status = 'delinquent' THEN 1 ELSE 0 END) AS delinquent_loans
            FROM borrowers b JOIN loans l ON l.borrower_id = b.borrower_id
            GROUP BY b.fico_band
            ORDER BY b.fico_band
        """)["grade"]["passed"])
        self.assertTrue(self._attempt("python-paid-revenue", [
            {"customer_id": 1, "revenue": 300.0},
            {"customer_id": 2, "revenue": 125.0},
        ])["grade"]["passed"])
        case = self._attempt(
            "stats-ab-test",
            "Randomize by user, define conversion and latency guardrails, calculate power, then monitor sample ratio mismatch.",
        )
        self.assertGreater(case["grade"]["score"], 0)

        created = self.client.post(
            "/api/prep/technical/sessions",
            json={"durationMinutes": 30, "role": "product-analyst"},
        ).json()
        running = self.client.post(f"/api/prep/technical/sessions/{created['id']}/start").json()
        self.assertNotIn("scorecard", running)
        for question in running["questions"]:
            answer: object = "Define the grain, decision, primary metric, guardrail, and validation plan."
            if question["kind"] == "sql":
                answer = sql
            elif question["kind"] == "python":
                answer = [
                    {"customer_id": 1, "revenue": 300.0},
                    {"customer_id": 2, "revenue": 125.0},
                ]
            saved = self.client.patch(
                f"/api/prep/technical/sessions/{created['id']}/answers/{question['id']}",
                json={"answer": answer},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        graded = self.client.post(f"/api/prep/technical/sessions/{created['id']}/submit").json()
        self.assertEqual(graded["state"], "graded")
        self.assertEqual(len(graded["scorecard"]["questions"]), len(running["questions"]))
        self.assertTrue(graded["scorecard"]["passed"])


if __name__ == "__main__":
    unittest.main()
