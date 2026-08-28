from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import store
from app.application_states import ApplicationState
from app.db import connect, initialize
from app.main import app


class ApplicationStateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "careeros.db"
        self.path_patch = patch.object(store, "DB_PATH", self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        initialize(path=self.path)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _seed(self, suffix: str, status: str) -> str:
        app_id = f"app_{suffix}"
        with connect(path=self.path) as connection:
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, created_at, updated_at) "
                "VALUES (?,?, 'Analyst','Acme',?,'t','t')",
                (app_id, suffix, status),
            )
        return app_id

    def _advance(self, app_id: str, target: str):
        return self.client.post(
            f"/api/applications/{app_id}/advance",
            json={"status": target, "note": f"to {target}"},
        )

    def test_every_canonical_state_is_accepted_on_a_valid_edge(self) -> None:
        edges = (
            ("discovered", "discovered"),
            ("discovered", "qualified"),
            ("qualified", "tailoring"),
            ("tailoring", "draft"),
            ("draft", "ready"),
            ("ready", "submitted"),
            ("submitted", "recruiter_contacted"),
            ("recruiter_contacted", "screening"),
            ("screening", "interview"),
            ("interview", "offer"),
            ("submitted", "rejected"),
            ("submitted", "withdrawn"),
        )
        self.assertEqual({target for _, target in edges}, {state.value for state in ApplicationState})
        for index, (current, target) in enumerate(edges):
            with self.subTest(current=current, target=target):
                app_id = self._seed(str(index), current)
                response = self._advance(app_id, target)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], target)

    def test_unknown_and_legacy_write_text_have_stable_422_codes(self) -> None:
        for target, code in (
            ("made_up", "invalid_application_state"),
            ("applied", "legacy_state_write"),
            ("interviewing", "legacy_state_write"),
        ):
            with self.subTest(target=target):
                response = self._advance(self._seed(target, "ready"), target)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], code)

    def test_backward_edge_and_terminal_escape_have_stable_409_codes(self) -> None:
        for current, target in (("submitted", "ready"), ("rejected", "qualified")):
            with self.subTest(current=current, target=target):
                response = self._advance(self._seed(f"{current}_{target}", current), target)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "invalid_application_transition",
                )

    def test_idempotent_transition_does_not_duplicate_timeline(self) -> None:
        app_id = self._seed("same", "ready")
        self.assertEqual(self._advance(app_id, "submitted").status_code, 200)
        self.assertEqual(self._advance(app_id, "submitted").status_code, 200)
        with connect(read_only=True, path=self.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM timeline WHERE application_id=?", (app_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_backward_repairs_require_a_reason_and_are_repository_only(self) -> None:
        app_id = self._seed("repair", "submitted")
        with self.assertRaisesRegex(ValueError, "repair_reason_required"):
            store.repair_application_state(app_id, "ready", "")
        store.repair_application_state(app_id, "ready", "Candidate corrected the record")
        self.assertEqual(store.get_application(app_id)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
