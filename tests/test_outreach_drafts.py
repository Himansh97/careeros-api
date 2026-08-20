"""Outreach could compose an email and then had nowhere to put it.

`app/outreach.py` wrote a subject and a body, `outreach_store` saved them, and
that was the end of the road: no Gmail draft id, no approval state, no queue.
Twenty-one outreach emails were text in a database table while the recruiter
reply pipeline next door had all three. This adds the missing half.

Three properties matter, and two of them are lessons already paid for:

* A body that promises a resume cannot be approved without one. Three recruiter
  replies went out saying "resume attached" with nothing attached, and outreach
  had exactly the same gap for exactly the same reason.
* Claiming is exclusive, so two agent sessions cannot both create a Gmail draft
  for the same outreach. That rests on `BEGIN IMMEDIATE` rather than on anything
  these tests can demonstrate — see the note on the concurrency test.
* Attachments are read when the draft is claimed, not when it is composed, so a
  resume retailored in between goes out in its current form.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import outreach_store, store  # noqa: E402
from app.db import initialize  # noqa: E402
from app.outreach_store import (  # noqa: E402
    approve_outreach,
    claim_approved_outreach,
    dismiss_outreach,
    get_outreach,
    mark_outreach_draft_created,
    mark_outreach_failed,
    set_attachments,
    upsert_outreach,
)

# outreach_store derives the row id from the job id; a payload cannot set it.
OID = "o_gh_acme_1"


class OutreachDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patcher = patch.object(
            store, "DB_PATH", pathlib.Path(self.temp.name) / "careeros.db"
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        initialize(path=store.DB_PATH)

    def _outreach(self, body: str = "Hello — I'd welcome a short call.", **over):
        payload = {
            "jobId": "gh_acme_1",
            "contactId": None,
            "company": "Acme Logistics",
            "jobTitle": "Data Analyst",
            "channel": "email",
            "status": "drafted",
            "emailSubject": "Data Analyst — quick note",
            "emailDraft": body,
            "linkedinDraft": "",
        }
        payload.update(over)
        return upsert_outreach(payload)

    def _resume(self, name: str = "resume.pdf") -> str:
        path = pathlib.Path(self.temp.name) / name
        path.write_bytes(b"%PDF-1.4\nnot a real resume, but a real file\n")
        return str(path)

    # ------------------------------------------------- the attachment rule

    def test_a_body_promising_a_resume_cannot_be_approved_without_one(self) -> None:
        self._outreach(body="Hello — resume attached. Happy to talk.")
        with self.assertRaises(ValueError) as caught:
            approve_outreach(OID)
        self.assertIn("attach", str(caught.exception).lower())

    def test_the_same_body_approves_once_the_resume_is_attached(self) -> None:
        self._outreach(body="Hello — resume attached. Happy to talk.")
        set_attachments(OID, [self._resume()])
        record = approve_outreach(OID)
        self.assertEqual(record["draftStatus"], "approved")

    def test_a_body_promising_nothing_approves_with_no_attachment(self) -> None:
        """Most outreach should not carry a resume. The guard refuses a specific
        inconsistency, it does not require an attachment."""
        self._outreach()
        self.assertEqual(approve_outreach(OID)["draftStatus"], "approved")

    def test_an_attachment_that_no_longer_exists_blocks_approval(self) -> None:
        self._outreach()
        set_attachments(OID, [str(pathlib.Path(self.temp.name) / "gone.pdf")])
        with self.assertRaises(ValueError) as caught:
            approve_outreach(OID)
        self.assertIn("missing", str(caught.exception).lower())

    def test_an_empty_body_cannot_be_approved(self) -> None:
        self._outreach(body="   ")
        with self.assertRaises(ValueError):
            approve_outreach(OID)

    # ------------------------------------------------ the duplicate guard

    def _contact(self, email: str = "hank@acme.test") -> None:
        with store.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO contacts "
                "(id, company, name, email, provider, created_at)"
                " VALUES ('c_1','Acme Logistics','Hank',?,'manual',?)",
                (email, store.now()),
            )
            conn.execute("UPDATE outreach SET contact_id='c_1' WHERE id=?", (OID,))
            conn.commit()

    def test_someone_already_written_to_is_not_approved_again(self) -> None:
        """Three people received the same outreach twice in one day because a
        second batch was drafted while the first had already gone out."""
        self._outreach()
        self._contact()
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO outreach (id, job_id, company, job_title, channel,"
                " status, contact_id, sent_at, created_at)"
                " VALUES ('o_earlier','j2','Acme Logistics','Data Analyst','email',"
                " 'sent','c_1','2026-08-20T05:10:00+00:00','2026-08-01T00:00:00+00:00')"
            )
            conn.commit()

        with self.assertRaises(outreach_store.AlreadyContacted) as caught:
            approve_outreach(OID)
        self.assertIn("already written to", str(caught.exception))

    def test_a_deliberate_follow_up_can_still_be_approved(self) -> None:
        """A second approach on purpose is not a duplicate, and the guard must
        not make a genuine follow-up impossible."""
        self._outreach()
        self._contact()
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO outreach (id, job_id, company, job_title, channel,"
                " status, contact_id, sent_at, created_at)"
                " VALUES ('o_earlier','j2','Acme Logistics','Data Analyst','email',"
                " 'sent','c_1','2026-08-20T05:10:00+00:00','2026-08-01T00:00:00+00:00')"
            )
            conn.commit()
        self.assertEqual(
            approve_outreach(OID, force=True)["draftStatus"], "approved"
        )

    def test_a_first_approach_is_not_blocked(self) -> None:
        self._outreach()
        self._contact()
        self.assertEqual(approve_outreach(OID)["draftStatus"], "approved")

    def test_the_result_says_whether_the_check_could_conclude_anything(self) -> None:
        """"No prior contact" read off a stale mailbox snapshot is not a finding.
        The caller has to be able to tell that apart from a real all-clear."""
        self._outreach()
        self._contact()
        check = approve_outreach(OID)["duplicateCheck"]
        self.assertTrue(check["checked"])
        self.assertEqual(check["priorSends"], 0)
        self.assertIn("conclusive", check)

    def test_outreach_with_no_contact_is_not_silently_cleared(self) -> None:
        """Nothing to check against is not the same as checked and clean."""
        self._outreach()
        check = approve_outreach(OID)["duplicateCheck"]
        self.assertFalse(check["checked"])
        self.assertFalse(check["conclusive"])

    # --------------------------------------------------------- the handoff

    def test_claiming_returns_the_attachment_ready_to_send(self) -> None:
        """Base64 the agent can hand straight to Gmail. Anything less means
        somebody encodes a PDF by hand, which is how this breaks."""
        import base64

        self._outreach(body="Resume attached.")
        resume = self._resume()
        set_attachments(OID, [resume])
        approve_outreach(OID)

        claimed = claim_approved_outreach()
        self.assertEqual(claimed["id"], OID)
        payload = claimed["attachmentPayloads"][0]
        self.assertEqual(payload["filename"], "resume.pdf")
        self.assertEqual(payload["mimeType"], "application/pdf")
        self.assertEqual(
            base64.b64decode(payload["content"]), pathlib.Path(resume).read_bytes()
        )

    def test_the_file_is_read_when_claimed_not_when_composed(self) -> None:
        import base64

        self._outreach(body="Resume attached.")
        resume = self._resume()
        set_attachments(OID, [resume])
        approve_outreach(OID)

        pathlib.Path(resume).write_bytes(b"%PDF-1.4\nthe retailored version\n")
        claimed = claim_approved_outreach()
        self.assertIn(
            b"retailored",
            base64.b64decode(claimed["attachmentPayloads"][0]["content"]),
        )

    def test_a_claim_is_exclusive(self) -> None:
        """Two agent sessions must not both draft the same outreach."""
        self._outreach()
        approve_outreach(OID)
        first = claim_approved_outreach()
        second = claim_approved_outreach()
        self.assertIsNotNone(first)
        self.assertIsNone(second, "the same outreach was claimed twice")

    def test_two_sessions_claiming_at_once_do_not_both_win(self) -> None:
        """Two threads claiming at once must not both come back with a record.

        **This test does not prove race-safety, and should not be read as
        doing so.** Removing the guard on the UPDATE passes it, and so does
        removing `BEGIN IMMEDIATE` — the transactions are small enough that the
        interleaving never happens here however tightly the threads are
        started. It catches a gross regression and nothing subtler.

        The exclusivity actually rests on `BEGIN IMMEDIATE`, which takes the
        write lock for the whole transaction so a second claimer blocks until
        the first has moved the row to `creating`. The condition on the UPDATE
        is belt-and-braces on top of that. Both are kept; neither is verified
        here, and the honest place to record that is next to the test that
        looks like it verifies them.
        """
        import threading

        self._outreach()
        approve_outreach(OID)

        results: list[object] = []
        barrier = threading.Barrier(2)

        def claim() -> None:
            barrier.wait()          # start both as close together as possible
            try:
                results.append(claim_approved_outreach())
            except Exception as exc:   # a lock error is still not a double-claim
                results.append(exc)

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        won = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(won), 1, f"both sessions claimed the same outreach: {results}")

    def test_only_approved_outreach_is_claimable(self) -> None:
        self._outreach()
        self.assertIsNone(claim_approved_outreach())

    def test_a_vanished_attachment_fails_the_claim_rather_than_dropping_it(self) -> None:
        """Silently sending the message without its resume is the worst outcome
        available, so the claim fails instead."""
        self._outreach(body="Resume attached.")
        resume = self._resume()
        set_attachments(OID, [resume])
        approve_outreach(OID)
        pathlib.Path(resume).unlink()

        self.assertIsNone(claim_approved_outreach())
        record = get_outreach(OID)
        self.assertEqual(record["draftStatus"], "failed")
        self.assertIn("missing", (record["lastError"] or "").lower())

    # ----------------------------------------------------------- recording

    def test_a_created_draft_records_its_gmail_id(self) -> None:
        self._outreach()
        approve_outreach(OID)
        claim_approved_outreach()
        record = mark_outreach_draft_created(OID, "r-12345")
        self.assertEqual(record["draftStatus"], "created")
        self.assertEqual(record["gmailDraftId"], "r-12345")

    def test_a_created_draft_requires_an_id(self) -> None:
        self._outreach()
        with self.assertRaises(ValueError):
            mark_outreach_draft_created(OID, "")

    def test_a_failure_is_recorded_rather_than_looking_untried(self) -> None:
        self._outreach()
        approve_outreach(OID)
        record = mark_outreach_failed(OID, "Gmail rejected the request")
        self.assertEqual(record["draftStatus"], "failed")
        self.assertIn("Gmail", record["lastError"])

    def test_dismissing_is_distinct_from_failing(self) -> None:
        self._outreach()
        self.assertEqual(dismiss_outreach(OID)["draftStatus"], "dismissed")

    def test_outreach_written_before_this_existed_reads_as_never_prepared(self) -> None:
        """Twenty-one rows predate these columns. They must not read as failed."""
        self._outreach()
        self.assertIsNone(get_outreach(OID)["draftStatus"])
        self.assertEqual(get_outreach(OID)["attachments"], [])

    def test_sending_stays_on_the_outreach_status_not_the_draft_state(self) -> None:
        """`sent` is a fact about the outreach; the draft states are about
        preparing it. Collapsing them would make "drafted in Gmail" and "sent"
        the same value, which is the confusion this whole pipeline avoids."""
        self._outreach()
        approve_outreach(OID)
        claim_approved_outreach()
        mark_outreach_draft_created(OID, "r-1")
        record = get_outreach(OID)
        self.assertEqual(record["status"], "drafted")
        self.assertEqual(record["draftStatus"], "created")


if __name__ == "__main__":
    unittest.main(verbosity=1)
