"""Canonical application states are policy, not free-text labels."""
from __future__ import annotations

import unittest

from app.application_states import (
    ApplicationState,
    InvalidApplicationState,
    InvalidApplicationTransition,
    normalize_legacy_state,
    validate_transition,
)


class ApplicationStateTests(unittest.TestCase):
    def test_every_documented_forward_edge_is_allowed(self) -> None:
        edges = (
            ("discovered", "qualified"),
            ("qualified", "tailoring"),
            ("tailoring", "draft"),
            ("draft", "ready"),
            ("ready", "submitted"),
            ("submitted", "recruiter_contacted"),
            ("recruiter_contacted", "screening"),
            ("screening", "interview"),
            ("interview", "offer"),
        )

        for current, target in edges:
            with self.subTest(current=current, target=target):
                self.assertEqual(validate_transition(current, target).value, target)

    def test_outcomes_are_allowed_only_from_post_submission_stages(self) -> None:
        for current in ("submitted", "recruiter_contacted", "screening", "interview"):
            for target in ("rejected", "withdrawn"):
                with self.subTest(current=current, target=target):
                    self.assertEqual(validate_transition(current, target).value, target)

        for current in ("discovered", "qualified", "tailoring", "draft", "ready"):
            with self.subTest(current=current):
                with self.assertRaises(InvalidApplicationTransition):
                    validate_transition(current, "rejected")

    def test_idempotent_transition_is_allowed(self) -> None:
        self.assertEqual(
            validate_transition("screening", "screening"),
            ApplicationState.SCREENING,
        )

    def test_legacy_values_normalize_only_for_reads(self) -> None:
        self.assertEqual(normalize_legacy_state("applied"), ApplicationState.SUBMITTED)
        self.assertEqual(normalize_legacy_state("interviewing"), ApplicationState.INTERVIEW)

        for legacy in ("applied", "interviewing"):
            with self.subTest(legacy=legacy):
                with self.assertRaisesRegex(InvalidApplicationState, "legacy_state_write"):
                    validate_transition("submitted", legacy)

    def test_unknown_state_has_a_stable_error_code(self) -> None:
        with self.assertRaises(InvalidApplicationState) as raised:
            validate_transition("ready", "made_up")
        self.assertEqual(raised.exception.code, "invalid_application_state")

    def test_backward_transition_requires_repair_and_reason(self) -> None:
        with self.assertRaises(InvalidApplicationTransition) as raised:
            validate_transition("interview", "screening")
        self.assertEqual(raised.exception.code, "invalid_application_transition")

        with self.assertRaisesRegex(InvalidApplicationTransition, "repair_reason_required"):
            validate_transition("interview", "screening", repair=True)

        self.assertEqual(
            validate_transition(
                "interview",
                "screening",
                repair=True,
                reason="Employer corrected the stage",
            ),
            ApplicationState.SCREENING,
        )

    def test_terminal_states_cannot_be_left_by_normal_workflow(self) -> None:
        for current in ("offer", "rejected", "withdrawn"):
            with self.subTest(current=current):
                with self.assertRaises(InvalidApplicationTransition):
                    validate_transition(current, "submitted")


if __name__ == "__main__":
    unittest.main()
