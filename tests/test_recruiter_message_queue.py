"""Protocol tests for the heartbeat recruiter-draft queue bridge."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store
from app.recruiter_messages import approve_draft
from scripts.recruiter_message_queue import handle


REPO_ROOT = Path(__file__).resolve().parent.parent


class RecruiterMessageQueueTests(unittest.TestCase):
    """Queue commands retain only reviewable draft state."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "careeros.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def payload(self) -> dict:
        return {
            "gmailMessageId": "msg_queue_handoff",
            "applicationId": "app_queue_handoff",
            "senderName": "Recruiter",
            "senderEmail": "recruiter@example.com",
            "subject": "Interview update",
            "receivedAt": "2026-08-11T12:00:00+00:00",
            "classification": "actionable_handoff",
            "synopsis": "A recruiter handed the process to a colleague.",
            "gmailUrl": "https://mail.google.com/mail/u/0/#all/msg_queue_handoff",
            "draft": {
                "to": ["colleague@example.com"],
                "cc": [],
                "bcc": [],
                "subject": "Re: Interview update",
                "body": "Thank you for the update.",
            },
        }

    def test_upsert_returns_the_stored_message(self) -> None:
        """Dropping the upsert dispatch would prevent heartbeat events being reviewable."""
        response = handle({"action": "upsert", "payload": self.payload()})

        self.assertTrue(response["ok"])
        self.assertIsNone(response["error"])
        self.assertEqual(response["result"]["gmailMessageId"], "msg_queue_handoff")
        self.assertEqual(response["result"]["draft"]["status"], "awaiting_approval")

    def test_script_entrypoint_starts_from_repo_root_and_sanitizes_invalid_json(self) -> None:
        """Running the automation command must not fail before reading stdin."""
        completed = subprocess.run(
            [sys.executable, "scripts/recruiter_message_queue.py"],
            cwd=REPO_ROOT,
            input="not valid json\n",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout),
            {"ok": False, "result": None, "error": "Request could not be processed."},
        )

    def test_claim_returns_one_approved_record_then_none(self) -> None:
        """A non-atomic or repeated claim could create duplicate Gmail drafts."""
        handle({"action": "upsert", "payload": self.payload()})
        approve_draft("msg_queue_handoff")

        first = handle({"action": "claim"})
        second = handle({"action": "claim"})

        self.assertTrue(first["ok"])
        self.assertEqual(first["result"]["draft"]["status"], "creating")
        self.assertTrue(second["ok"])
        self.assertIsNone(second["result"])

    def test_created_requires_a_nonempty_gmail_draft_id(self) -> None:
        """Completing without Gmail's ID would falsely record a created draft."""
        response = handle({"action": "created", "gmailMessageId": "msg_queue_handoff", "gmailDraftId": ""})

        self.assertFalse(response["ok"])
        self.assertIsNone(response["result"])
        self.assertEqual(response["error"], "Request could not be processed.")

    def test_created_rejects_a_whitespace_only_gmail_draft_id(self) -> None:
        """Whitespace must not be recorded as a usable Gmail draft identifier."""
        handle({"action": "upsert", "payload": self.payload()})
        approve_draft("msg_queue_handoff")
        handle({"action": "claim"})

        response = handle({
            "action": "created",
            "gmailMessageId": "msg_queue_handoff",
            "gmailDraftId": "  \t ",
        })

        self.assertFalse(response["ok"])
        self.assertIsNone(response["result"])
        self.assertEqual(response["error"], "Request could not be processed.")

    def test_created_stores_a_stripped_gmail_draft_id(self) -> None:
        """Whitespace surrounding Gmail's ID must not become part of persisted state."""
        handle({"action": "upsert", "payload": self.payload()})
        approve_draft("msg_queue_handoff")
        handle({"action": "claim"})

        response = handle({
            "action": "created",
            "gmailMessageId": "msg_queue_handoff",
            "gmailDraftId": "  draft_123  ",
        })

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["draft"]["gmailDraftId"], "draft_123")

    def test_failed_keeps_only_safe_code_and_a_bounded_message(self) -> None:
        """Raw connector errors must not persist secrets or unbounded diagnostic content."""
        handle({"action": "upsert", "payload": self.payload()})
        approve_draft("msg_queue_handoff")
        handle({"action": "claim"})

        response = handle({
            "action": "failed",
            "gmailMessageId": "msg_queue_handoff",
            "code": "gmail_timeout",
            "message": "secret-token " * 100,
        })

        self.assertTrue(response["ok"])
        draft = response["result"]["draft"]
        self.assertEqual(draft["lastErrorCode"], "gmail_timeout")
        self.assertLessEqual(len(draft["lastErrorMessage"]), 300)
        self.assertNotIn("secret-token", draft["lastErrorMessage"])

    def test_requeue_stale_returns_creating_record_without_reapproving_it(self) -> None:
        """Reapproval of an interrupted claim would permit duplicate draft creation."""
        handle({"action": "upsert", "payload": self.payload()})
        approve_draft("msg_queue_handoff")
        handle({"action": "claim"})
        with store.connect() as conn:
            conn.execute(
                "UPDATE recruiter_reply_drafts SET updated_at=? WHERE gmail_message_id=?",
                ("2020-01-01T00:00:00+00:00", "msg_queue_handoff"),
            )

        response = handle({"action": "requeue_stale"})

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"][0]["gmailMessageId"], "msg_queue_handoff")
        self.assertEqual(response["result"][0]["draft"]["status"], "creating")


if __name__ == "__main__":
    unittest.main()
