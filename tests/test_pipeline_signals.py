"""An application should advance from things that already happened.

Every stage of the pipeline used to be a button. Two of them never needed to be:
opening the application on the employer's site is unambiguous, and a
confirmation email is the employer stating it arrived. The classifier has
produced "application confirmation" since the beginning and nothing read the
label, so Adobe and CVS both confirmed receipt while their applications waited
for a manual click.

Automating that is only safe with two guarantees, and both exist because of
failures already recorded in this repository:

* **Never backwards.** `store.py` records what a rewind cost once: six
  applications the candidate had actually sent were walked back and offered to
  them to send again. Automatic signals arrive out of order, so this matters far
  more now than when a human drove every transition.
* **Never invent a send.** Many ATSs send no confirmation at all. Silence must
  raise a question, never produce a `submitted` — a false send date on a real
  application is the specific harm this system is built to avoid.

A third guarantee is about the matching itself: a message that cannot be
confidently tied to one application must be reported, not guessed at. A wrong
auto-advance moves the wrong application and the candidate has no reason to look.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import store  # noqa: E402
from app.pipeline_signals import (  # noqa: E402
    CONFIDENCE_FLOOR,
    apply_signal,
    mark_applying,
    match_application,
    score_match,
    stuck_applying,
)
from app.store import StatusRegression, advance  # noqa: E402


class PipelineSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patcher = patch.object(
            store, "DB_PATH", pathlib.Path(self.temp.name) / "careeros.db"
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        with store.connect() as conn:
            conn.executescript(store.SCHEMA)

    def _app(self, app_id: str, company: str, title: str, status: str = "ready",
             when: str = "2026-08-10T00:00:00+00:00") -> str:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO applications (id, job_id, title, company, location,"
                " source, status, apply_url, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (app_id, app_id.replace("app_", ""), title, company, "Remote",
                 "greenhouse", status, "https://example.test", when, when),
            )
            conn.commit()
        return app_id

    def _message(self, **over):
        base = {
            "gmailMessageId": "m1",
            "applicationId": None,
            "senderName": "Adobe Careers",
            "senderEmail": "adobe@myworkday.com",
            "subject": "Thanks for Applying to Adobe",
            "synopsis": "Adobe confirmed receipt of the application.",
            "classification": "application confirmation",
            "receivedAt": "2026-08-15T23:31:00+00:00",
        }
        base.update(over)
        return base

    # ------------------------------------------------------ never backwards

    def test_advance_refuses_to_walk_an_application_back(self) -> None:
        """The rewind that cost six sent applications, made impossible."""
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="submitted")
        with self.assertRaises(StatusRegression):
            advance(app_id, "ready", "re-tailored")
        self.assertEqual(store.get_application(app_id)["status"], "submitted")

    def test_a_person_may_still_correct_a_stage_by_hand(self) -> None:
        """The guard is aimed at automatic signals, not at the candidate."""
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="submitted")
        advance(app_id, "ready", "set back deliberately", allow_regression=True)
        self.assertEqual(store.get_application(app_id)["status"], "ready")

    def test_a_late_confirmation_cannot_undo_a_later_stage(self) -> None:
        """Mail arrives out of order. A confirmation reaching an application that
        already has a recruiter talking to it must change nothing."""
        app_id = self._app("app_a", "Adobe", "Data Engineer",
                           status="recruiter_contacted")
        result = apply_signal(self._message(applicationId=app_id))
        self.assertFalse(result["advanced"])
        self.assertEqual(
            store.get_application(app_id)["status"], "recruiter_contacted"
        )

    def test_reopening_a_submitted_application_is_not_an_error(self) -> None:
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="submitted")
        self.assertFalse(mark_applying(app_id))
        self.assertEqual(store.get_application(app_id)["status"], "submitted")

    # ----------------------------------------------------- the transitions

    def test_opening_an_application_moves_it_to_applying(self) -> None:
        app_id = self._app("app_a", "Adobe", "Data Engineer")
        self.assertTrue(mark_applying(app_id))
        self.assertEqual(store.get_application(app_id)["status"], "applying")

    def test_a_confirmation_email_submits_the_application(self) -> None:
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="applying")
        result = apply_signal(self._message())
        self.assertTrue(result["advanced"], result)
        self.assertEqual(result["applicationId"], app_id)
        self.assertEqual(store.get_application(app_id)["status"], "submitted")

    def test_the_submitted_date_is_the_employers_not_todays(self) -> None:
        """A confirmation can arrive days after the application went out.
        Stamping the moment the mail was read puts a false date on real work."""
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="applying")
        apply_signal(self._message(receivedAt="2026-08-15T23:31:00+00:00"))
        submitted = store.get_application(app_id)["submittedAt"]
        self.assertTrue(str(submitted).startswith("2026-08-15"), submitted)

    def test_a_second_confirmation_does_not_restamp_the_date(self) -> None:
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="applying")
        apply_signal(self._message(receivedAt="2026-08-15T23:31:00+00:00"))
        apply_signal(self._message(receivedAt="2026-08-19T10:00:00+00:00"))
        self.assertTrue(
            str(store.get_application(app_id)["submittedAt"]).startswith("2026-08-15")
        )

    def test_a_progression_email_moves_past_submitted(self) -> None:
        app_id = self._app("app_a", "Dallas College", "Business Systems Analyst",
                           status="submitted")
        result = apply_signal(self._message(
            senderEmail="dallascollege@myworkday.com",
            senderName="Dallas College",
            subject="Hiring Supervisor Review",
            synopsis="Dallas College forwarded the Business Systems Analyst "
                     "application to the hiring supervisor.",
            classification="application_progressed",
        ))
        self.assertTrue(result["advanced"], result)
        self.assertEqual(
            store.get_application(app_id)["status"], "recruiter_contacted"
        )

    def test_an_unrelated_classification_advances_nothing(self) -> None:
        self._app("app_a", "Adobe", "Data Engineer", status="applying")
        result = apply_signal(self._message(classification="recruiter outreach"))
        self.assertFalse(result["advanced"])
        self.assertEqual(store.get_application("app_a")["status"], "applying")

    # -------------------------------------------------------- the matching

    def test_an_ambiguous_match_is_reported_rather_than_guessed(self) -> None:
        """Two applications to the same employer produce near-identical
        confirmations. Advancing whichever sorted first is a coin toss the
        candidate would never see."""
        self._app("app_a", "Adobe", "Data Engineer", status="applying")
        self._app("app_b", "Adobe", "Data Analyst", status="applying")
        result = apply_signal(self._message(subject="Thanks for Applying to Adobe",
                                            synopsis="Adobe confirmed receipt."))
        self.assertFalse(result["advanced"])
        self.assertTrue(result["needsReview"])
        self.assertEqual(store.get_application("app_a")["status"], "applying")
        self.assertEqual(store.get_application("app_b")["status"], "applying")

    def test_the_role_title_breaks_a_tie_between_two_applications(self) -> None:
        self._app("app_a", "Adobe", "Senior Data Engineer", status="applying")
        self._app("app_b", "Adobe", "Marketing Manager", status="applying")
        result = apply_signal(self._message(
            subject="Thanks for Applying to Adobe — Senior Data Engineer",
            synopsis="Adobe confirmed receipt of the Senior Data Engineer application.",
        ))
        self.assertTrue(result["advanced"], result)
        self.assertEqual(result["applicationId"], "app_a")

    def test_a_message_matching_no_application_advances_nothing(self) -> None:
        """HCSC, SS&C and TriMark all wrote about applications made outside this
        system. Matching them to the nearest tracked company would be worse than
        matching them to nothing."""
        self._app("app_a", "Adobe", "Data Engineer", status="applying")
        result = apply_signal(self._message(
            senderEmail="updates@eprivatemail.com",
            senderName="HCSC",
            subject="Thank You for Your Interest in HCSC",
            synopsis="HCSC chose other candidates.",
            classification="application confirmation",
        ))
        self.assertFalse(result["advanced"])
        self.assertEqual(store.get_application("app_a")["status"], "applying")

    def test_the_ats_domain_does_not_match_every_application(self) -> None:
        """adobe@myworkday.com is Workday's host carrying Adobe's name. Matching
        on the host alone finds 'myworkday' for every Workday employer."""
        self._app("app_a", "Target", "Analyst", status="applying")
        score = score_match(self._message(), store.list_applications()[0])
        self.assertLess(score, CONFIDENCE_FLOOR)

    def test_an_already_linked_message_needs_no_matching(self) -> None:
        app_id = self._app("app_a", "Adobe", "Data Engineer", status="applying")
        match = match_application(self._message(applicationId=app_id))
        self.assertTrue(match["confident"])
        self.assertEqual(match["score"], 1.0)

    # ------------------------------------------------ never invent a send

    def test_silence_never_produces_a_submitted(self) -> None:
        """The whole reason the stuck case is an alert and not a transition."""
        self._app("app_a", "Adobe", "Data Engineer", status="applying",
                  when="2026-01-01T00:00:00+00:00")
        rows = stuck_applying(days=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "app_a")
        self.assertEqual(store.get_application("app_a")["status"], "applying")

    def test_a_recently_opened_application_is_not_yet_chased(self) -> None:
        from datetime import datetime, timezone

        self._app("app_a", "Adobe", "Data Engineer", status="applying",
                  when=datetime.now(timezone.utc).isoformat())
        self.assertEqual(stuck_applying(days=3), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
