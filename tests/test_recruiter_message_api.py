"""HTTP contract tests for the recruiter message review queue."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pathlib
import sys

# The other eight test files do this; these three did not, so running them the
# way AGENTS.md documents — `./.venv/bin/python tests/<file>.py` — died on
# `No module named 'app'` and looked like a broken suite rather than a broken
# path. They pass either way now.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import store
from app.main import app
from app.recruiter_messages import (
    approve_draft,
    claim_approved_draft,
    get_message,
    mark_draft_failed,
    upsert_message,
)


class RecruiterMessageApiTests(unittest.TestCase):
    """The review API exposes drafts without claiming outgoing mail was sent."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "careeros.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.message_id = self.seed_message()

    def seed_message(
        self,
        *,
        message_id: str = "msg_gitlab_handoff",
        application_id: str = "app_gh_gitlab_8616308002",
        received_at: str = "2026-08-11T12:00:00+00:00",
        to_addresses: list[str] | None = None,
    ) -> str:
        upsert_message(
            {
                "gmailMessageId": message_id,
                "applicationId": application_id,
                "senderName": "Matthew Macfarlane",
                "senderEmail": "matthew@gitlab.com",
                "subject": "Senior Revenue Analytics Analyst",
                "receivedAt": received_at,
                "classification": "actionable_handoff",
                "synopsis": "Matthew handed the process to Izzy and Gabe.",
                "gmailUrl": f"https://mail.google.com/mail/u/0/#all/{message_id}",
                "draft": {
                    "to": to_addresses or ["izzy@gitlab.com"],
                    "cc": ["gabe@gitlab.com"],
                    "bcc": [],
                    "subject": "Re: Senior Revenue Analytics Analyst",
                    "body": "Hi Izzy,\n\nThank you for the handoff.",
                },
            }
        )
        return message_id

    def test_list_returns_messages_newest_first_and_filters_by_application(self) -> None:
        """A list implementation that ignores ordering or application filtering fails here."""
        newer_id = self.seed_message(
            message_id="msg_newer",
            received_at="2026-08-12T12:00:00+00:00",
        )
        self.seed_message(message_id="msg_other_app", application_id="app_other")

        listed = self.client.get("/api/recruiter-messages")
        filtered = self.client.get(
            "/api/recruiter-messages",
            params={"applicationId": "app_gh_gitlab_8616308002"},
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["messages"][0]["gmailMessageId"], newer_id)
        self.assertEqual(
            [message["gmailMessageId"] for message in filtered.json()["messages"]],
            [newer_id, self.message_id],
        )

    def test_detail_returns_404_for_unknown_message(self) -> None:
        """Removing the missing-message guard would expose a successful empty response."""
        response = self.client.get("/api/recruiter-messages/missing-message")

        self.assertEqual(response.status_code, 404)

    def test_draft_update_accepts_only_reviewable_draft_fields(self) -> None:
        """Allowing unknown fields could let the HTTP API mutate queue state directly."""
        updated = self.client.put(
            f"/api/recruiter-messages/{self.message_id}/draft",
            json={
                "to": ["candidate@example.com"],
                "cc": [],
                "bcc": ["archive@example.com"],
                "subject": "Re: Updated subject",
                "body": "Updated candidate reply.",
            },
        )
        rejected = self.client.put(
            f"/api/recruiter-messages/{self.message_id}/draft",
            json={"status": "created"},
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["to"], ["candidate@example.com"])
        self.assertEqual(updated.json()["bcc"], ["archive@example.com"])
        self.assertEqual(updated.json()["status"], "awaiting_approval")
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(get_message(self.message_id)["draft"]["status"], "awaiting_approval")

    def test_approve_returns_approved_draft_without_outgoing_mail_claims(self) -> None:
        """Returning send state from approval would falsely imply Gmail has sent mail."""
        response = self.client.post(f"/api/recruiter-messages/{self.message_id}/approve")

        self.assertEqual(response.status_code, 200)
        draft = response.json()
        self.assertEqual(draft["status"], "approved")
        self.assertFalse({"sent", "sentAt", "gmailMessageId"} & set(draft))
        self.assertNotIn("gmailDraftId", draft)

    def test_dismiss_and_retry_enforce_source_states(self) -> None:
        """Permitting either action from an arbitrary state breaks the review workflow."""
        retry_before_failure = self.client.post(
            f"/api/recruiter-messages/{self.message_id}/retry"
        )
        dismissed = self.client.post(f"/api/recruiter-messages/{self.message_id}/dismiss")
        approve_draft(self.seed_message(message_id="msg_failed"))
        claim_approved_draft()
        mark_draft_failed("msg_failed", "gmail_error", "Gmail request failed")
        retried = self.client.post("/api/recruiter-messages/msg_failed/retry")

        self.assertEqual(retry_before_failure.status_code, 409)
        self.assertEqual(dismissed.status_code, 200)
        self.assertEqual(dismissed.json()["status"], "dismissed")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["status"], "approved")

    def test_malformed_recipient_returns_422_without_changing_draft(self) -> None:
        """Weak address validation would persist malformed mail recipients."""
        original_to = get_message(self.message_id)["draft"]["to"]

        response = self.client.put(
            f"/api/recruiter-messages/{self.message_id}/draft",
            json={"to": ["candidate @example.com"]},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(get_message(self.message_id)["draft"]["to"], original_to)

    def test_update_round_trips_display_name_recipient_as_bare_address(self) -> None:
        """Rejecting a display-name recipient returned by GET blocks normal draft edits."""
        message_id = self.seed_message(
            message_id="msg_display_name",
            to_addresses=["Izzy Chu <ICHU@gitlab.com>"],
        )
        detail = self.client.get(f"/api/recruiter-messages/{message_id}").json()

        response = self.client.put(
            f"/api/recruiter-messages/{message_id}/draft",
            json={
                "to": detail["draft"]["to"],
                "subject": "Re: Updated handoff",
                "body": "Thank you, Izzy.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["to"], ["ichu@gitlab.com"])
        self.assertEqual(get_message(message_id)["draft"]["to"], ["ichu@gitlab.com"])


if __name__ == "__main__":
    unittest.main()
