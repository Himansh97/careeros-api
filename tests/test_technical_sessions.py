from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pathlib
import sys

# Every other test file in this repo carries this. Without it, running the way
# AGENTS.md documents — `./.venv/bin/python tests/<file>.py` — dies on
# "No module named 'app'", so eight passing suites read as a broken suite.
# `tests/test_recruiter_messages.py` records this exact bug happening before.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import store as app_store
from app.technical_learning.sessions import (
    create_session,
    get_session,
    save_answer,
    start_session,
    submit_session,
)


class TechnicalSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(app_store, "DB_PATH", Path(self.tmp.name) / "careeros.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    def test_only_approved_durations_are_allowed(self) -> None:
        for minutes in (30, 45, 60):
            self.assertEqual(create_session(minutes, now=self.now)["durationMinutes"], minutes)
        with self.assertRaisesRegex(ValueError, "duration"):
            create_session(15, now=self.now)

    def test_manifest_is_frozen_and_contains_mixed_question_types(self) -> None:
        session = create_session(45, role="product-analyst", now=self.now)
        self.assertEqual(session["state"], "created")
        kinds = {question["kind"] for question in session["questions"]}
        self.assertTrue({"sql", "python", "case"} <= kinds)
        encoded = repr(session).lower()
        self.assertNotIn("expected_sql", encoded)
        self.assertNotIn("rubric", encoded)

    def test_start_and_autosave_are_idempotent(self) -> None:
        created = create_session(30, now=self.now)
        running = start_session(created["id"], now=self.now)
        again = start_session(created["id"], now=self.now + timedelta(seconds=2))
        self.assertEqual(running["startedAt"], again["startedAt"])
        question_id = running["questions"][0]["id"]
        first = save_answer(created["id"], question_id, "draft one", now=self.now)
        second = save_answer(created["id"], question_id, "draft two", now=self.now)
        self.assertEqual(first["questionId"], second["questionId"])
        self.assertEqual(get_session(created["id"], now=self.now)["answers"][question_id], "draft two")

    def test_running_session_never_leaks_correctness(self) -> None:
        created = create_session(30, now=self.now)
        running = start_session(created["id"], now=self.now)
        snapshot = get_session(running["id"], now=self.now + timedelta(minutes=1))
        self.assertNotIn("scorecard", snapshot)
        self.assertNotIn("passed", repr(snapshot).lower())

    def test_submit_grades_all_answers_only_after_round(self) -> None:
        created = create_session(30, now=self.now)
        running = start_session(created["id"], now=self.now)
        for question in running["questions"]:
            answer = "Randomize user conversion with latency guardrails, validate grain, and monitor errors."
            if question["kind"] == "python":
                answer = [{"customer_id": 1, "revenue": 300.0}, {"customer_id": 2, "revenue": 125.0}]
            if question["kind"] == "sql":
                answer = "SELECT c.segment, ROUND(SUM(o.revenue), 2) FROM customers c JOIN orders o ON o.customer_id=c.customer_id WHERE o.status='paid' GROUP BY c.segment ORDER BY SUM(o.revenue) DESC"
            save_answer(created["id"], question["id"], answer, now=self.now)
        result = submit_session(created["id"], now=self.now + timedelta(minutes=5))
        self.assertEqual(result["state"], "graded")
        self.assertIn("scorecard", result)
        self.assertEqual(len(result["scorecard"]["questions"]), len(running["questions"]))

    def test_server_time_expires_and_grades_the_session(self) -> None:
        created = create_session(30, now=self.now)
        start_session(created["id"], now=self.now)
        expired = get_session(created["id"], now=self.now + timedelta(minutes=31))
        self.assertEqual(expired["state"], "graded")
        self.assertEqual(expired["completionReason"], "expired")
        with self.assertRaisesRegex(ValueError, "closed"):
            save_answer(created["id"], expired["questions"][0]["id"], "late", now=self.now + timedelta(minutes=31))


if __name__ == "__main__":
    unittest.main()
