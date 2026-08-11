"""Persistence and state-transition tests for recruiter message drafts."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store
from app.recruiter_messages import (
    approve_draft,
    claim_approved_draft,
    dismiss_draft,
    get_message,
    mark_draft_created,
    mark_draft_failed,
    retry_draft,
    update_draft,
    upsert_message,
)


class RecruiterMessageStoreTests(unittest.TestCase):
    """The store keeps only reviewable outgoing content and queue state."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "careeros.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def payload(self) -> dict:
        return {
            "gmailMessageId": "msg_gitlab_handoff",
            "applicationId": "app_gh_gitlab_8616308002",
            "senderName": "Matthew Macfarlane",
            "senderEmail": "matthew@gitlab.com",
            "subject": "Senior Revenue Analytics Analyst",
            "receivedAt": "2026-08-11T12:00:00+00:00",
            "classification": "actionable_handoff",
            "synopsis": "Matthew handed the process to Izzy and Gabe.",
            "gmailUrl": "https://mail.google.com/mail/u/0/#all/msg_gitlab_handoff",
            "draft": {
                "to": [" Izzy Chu <ICHU@gitlab.com> "],
                "cc": ["Gabe Weaver <gweaver@gitlab.com>"],
                "bcc": [],
                "subject": "Re: Senior Revenue Analytics Analyst",
                "body": "Hi Izzy,\n\nThank you for the handoff. I look forward to speaking.\n",
                "status": "awaiting_approval",
            },
        }

    def test_upsert_creates_event_and_draft_without_incoming_content(self) -> None:
        """Removing either row creation or privacy boundary makes this fail."""
        created = upsert_message(self.payload())
        message = get_message("msg_gitlab_handoff")

        self.assertEqual(created["gmailMessageId"], "msg_gitlab_handoff")
        self.assertEqual(message["applicationId"], "app_gh_gitlab_8616308002")
        self.assertEqual(message["draft"]["to"], ["izzy chu <ichu@gitlab.com>"])
        self.assertEqual(message["draft"]["cc"], ["gabe weaver <gweaver@gitlab.com>"])
        with sqlite3.connect(self.db_path) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(recruiter_messages)")}
        self.assertFalse({"body", "raw_headers", "attachments"} & columns)

    def test_approval_claim_and_completion_follow_queue_states(self) -> None:
        """Incorrect state mutations or a double claim make this fail."""
        mid = upsert_message(self.payload())["gmailMessageId"]

        self.assertEqual(approve_draft(mid)["draft"]["status"], "approved")
        self.assertIsNotNone(approve_draft(mid)["draft"]["contentFingerprint"])
        self.assertEqual(claim_approved_draft()["draft"]["status"], "creating")
        self.assertIsNone(claim_approved_draft())
        self.assertEqual(mark_draft_created(mid, "draft_123")["draft"]["status"], "created")

    def test_approval_requires_recipients_subject_and_body(self) -> None:
        """Accepting incomplete mail would let the worker create a bad Gmail draft."""
        for patch in ({"to": []}, {"subject": ""}, {"body": "  "}):
            with self.subTest(patch=patch):
                mid = upsert_message(
                    {**self.payload(), "gmailMessageId": f"msg_incomplete_{len(patch)}_{next(iter(patch))}"}
                )["gmailMessageId"]
                update_draft(mid, patch)

                with self.assertRaises(ValueError):
                    approve_draft(mid)

    def test_edits_are_rejected_once_draft_is_not_editable(self) -> None:
        """Permitting edits after approval could diverge reviewed and created content."""
        mid = upsert_message(self.payload())["gmailMessageId"]
        approve_draft(mid)
        for status in ("approved", "creating", "created", "dismissed"):
            if status == "creating":
                claim_approved_draft()
            elif status == "created":
                mark_draft_created(mid, "draft_123")
            elif status == "dismissed":
                mid = upsert_message({**self.payload(), "gmailMessageId": "msg_dismissed"})[
                    "gmailMessageId"
                ]
                dismiss_draft(mid)
            with self.assertRaises(ValueError):
                update_draft(mid, {"body": "Changed"})

    def test_retry_only_moves_failed_draft_to_approved(self) -> None:
        """Retrying a nonfailed draft would violate the monotonic queue workflow."""
        mid = upsert_message(self.payload())["gmailMessageId"]
        with self.assertRaises(ValueError):
            retry_draft(mid)

        approve_draft(mid)
        claim_approved_draft()
        mark_draft_failed(mid, "gmail_error", "Gmail request failed")
        self.assertEqual(retry_draft(mid)["draft"]["status"], "approved")

    def test_retry_refreshes_fingerprint_after_candidate_edits(self) -> None:
        """A retry must fingerprint the edited content Gmail will actually create."""
        mid = upsert_message(self.payload())["gmailMessageId"]
        original = approve_draft(mid)["draft"]["contentFingerprint"]
        claim_approved_draft()
        mark_draft_failed(mid, "gmail_error", "Gmail request failed")
        update_draft(mid, {"body": "Candidate revised this response."})

        retried = retry_draft(mid)

        self.assertNotEqual(retried["draft"]["contentFingerprint"], original)

    def test_rejects_invalid_recipient_addresses(self) -> None:
        """Invalid recipient data must not enter the approved Gmail queue."""
        mid = upsert_message(self.payload())["gmailMessageId"]

        with self.assertRaises(ValueError):
            update_draft(mid, {"to": "not-an-address"})
        with self.assertRaises(ValueError):
            update_draft(mid, {"cc": ["not-an-address"]})

    def test_failure_error_is_short_and_sanitized(self) -> None:
        """Raw connector secrets and stack details must not become visible draft state."""
        mid = upsert_message(self.payload())["gmailMessageId"]
        approve_draft(mid)
        claim_approved_draft()

        failed = mark_draft_failed(mid, "oauth token=super-secret", "Bearer super-secret\ntraceback")

        self.assertNotIn("super-secret", failed["draft"]["lastErrorCode"])
        self.assertNotIn("super-secret", failed["draft"]["lastErrorMessage"])
        self.assertLessEqual(len(failed["draft"]["lastErrorMessage"]), 240)

    def test_duplicate_upsert_preserves_candidate_draft_edits(self) -> None:
        """Refreshing message metadata must not overwrite candidate-edited content."""
        mid = upsert_message(self.payload())["gmailMessageId"]
        update_draft(mid, {"to": ["candidate@example.com"], "body": "Candidate edit"})

        refreshed = self.payload()
        refreshed["synopsis"] = "Updated classification details."
        refreshed["draft"]["body"] = "Regenerated suggestion"
        message = upsert_message(refreshed)

        self.assertEqual(message["synopsis"], "Updated classification details.")
        self.assertEqual(message["draft"]["to"], ["candidate@example.com"])
        self.assertEqual(message["draft"]["body"], "Candidate edit")

    def test_duplicate_upsert_only_refreshes_mutable_event_metadata(self) -> None:
        """A later scan cannot rewrite the original message identity or clear its match."""
        upsert_message(self.payload())
        refreshed = self.payload()
        refreshed.update({
            "applicationId": None,
            "senderName": "Different Sender",
            "subject": "Different subject",
            "receivedAt": "2026-08-12T12:00:00+00:00",
            "classification": "different",
            "gmailUrl": "https://example.invalid/changed",
            "synopsis": "Updated synopsis.",
        })

        message = upsert_message(refreshed)

        self.assertEqual(message["applicationId"], "app_gh_gitlab_8616308002")
        self.assertEqual(message["senderName"], "Matthew Macfarlane")
        self.assertEqual(message["subject"], "Senior Revenue Analytics Analyst")
        self.assertEqual(message["synopsis"], "Updated synopsis.")

    def test_new_message_adds_one_reply_detected_timeline_event(self) -> None:
        """Reply detection needs an auditable application timeline entry exactly once."""
        upsert_message(self.payload())
        upsert_message(self.payload())

        with sqlite3.connect(self.db_path) as conn:
            labels = [row[0] for row in conn.execute("SELECT label FROM timeline")]
        self.assertEqual(labels, ["Recruiter reply detected"])


if __name__ == "__main__":
    unittest.main()
