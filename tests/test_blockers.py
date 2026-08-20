"""Structured blockers replace decisions inferred from display copy."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import blockers
from app.db import connect, initialize


class BlockerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "careeros.db"
        initialize(path=self.path)
        self._path_patch = mock.patch.object(blockers, "DB_PATH", self.path)
        self._path_patch.start()
        with connect(path=self.path) as connection:
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, next_action, created_at, updated_at) "
                "VALUES ('app_1', 'job_1', 'Analyst', 'Example', 'ready', "
                "'Posting closed — no longer accepting', 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, next_action, created_at, updated_at) "
                "VALUES ('app_2', 'job_2', 'Engineer', 'Example', 'ready', "
                "'Waiting for portfolio review by Pat', 'now', 'now')"
            )

    def tearDown(self) -> None:
        self._path_patch.stop()
        self._tmp.cleanup()

    def test_open_and_resolve_blocker_preserves_audit_history(self) -> None:
        created = blockers.open_blocker(
            "app_1",
            kind="candidate_approval",
            owner="candidate",
            severity="blocking",
            source="approval_queue",
            evidence={"approvalId": "approval_1"},
            summary="Review and approve",
        )
        resolved = blockers.resolve_blocker(created["id"])

        self.assertEqual(created["state"], "open")
        self.assertEqual(resolved["state"], "resolved")
        self.assertIsNotNone(resolved["resolvedAt"])
        self.assertEqual(
            [row["state"] for row in blockers.list_blockers("app_1")],
            ["resolved"],
        )

    def test_repeating_the_same_open_blocker_is_idempotent(self) -> None:
        payload = dict(
            application_id="app_1",
            kind="posting_closed",
            owner="external",
            severity="blocking",
            source="liveness",
            evidence={"generationId": "generation_1"},
            summary="Posting closed",
        )

        first = blockers.open_blocker(**payload)
        second = blockers.open_blocker(**payload)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(blockers.list_blockers("app_1")), 1)

    def test_legacy_next_actions_become_typed_state_or_information(self) -> None:
        report = blockers.migrate_legacy_next_actions()

        closed = blockers.list_blockers("app_1")
        unknown = blockers.list_blockers("app_2")
        self.assertEqual(report, {"posting_closed": 1, "legacy_note": 1})
        self.assertEqual(
            (closed[0]["kind"], closed[0]["severity"], closed[0]["state"]),
            ("posting_closed", "blocking", "open"),
        )
        self.assertEqual(
            (unknown[0]["kind"], unknown[0]["severity"], unknown[0]["state"]),
            ("legacy_note", "info", "open"),
        )

    def test_legacy_migration_is_idempotent(self) -> None:
        blockers.migrate_legacy_next_actions()
        blockers.migrate_legacy_next_actions()

        self.assertEqual(len(blockers.list_blockers("app_1")), 1)
        self.assertEqual(len(blockers.list_blockers("app_2")), 1)


if __name__ == "__main__":
    unittest.main()
