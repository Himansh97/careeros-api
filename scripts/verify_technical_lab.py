#!/usr/bin/env python3
"""Verify the Technical Interview Lab against isolated synthetic state."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import store as app_store  # noqa: E402
from app.main import app  # noqa: E402
from app.technical_learning import datasets  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        with (
            patch.object(app_store, "DB_PATH", root / "careeros.db"),
            patch.object(datasets, "DATASET_ROOT", root / "datasets"),
            TestClient(app) as client,
        ):
            curriculum = client.get("/api/prep/technical/curriculum")
            require(curriculum.status_code == 200, "curriculum route failed")
            require(len(curriculum.json()["drills"]) >= 20, "curriculum is incomplete")
            require("expected_sql" not in curriculum.text.lower(), "public curriculum leaked an answer")

            sql = (
                "SELECT c.segment, ROUND(SUM(o.revenue), 2) "
                "FROM customers c JOIN orders o ON o.customer_id=c.customer_id "
                "WHERE o.status='paid' GROUP BY c.segment ORDER BY SUM(o.revenue) DESC"
            )
            run = client.post("/api/prep/technical/run", json={"drillId": "sql-revenue-by-segment", "sql": sql})
            require(run.status_code == 200 and run.json()["ok"], "isolated SQL execution failed")
            attempt = client.post(
                "/api/prep/technical/attempts",
                json={"drillId": "sql-revenue-by-segment", "answer": sql},
            )
            require(attempt.json()["grade"]["passed"], "deterministic SQL grading failed")

            attack = client.post(
                "/api/prep/technical/run",
                json={"drillId": "sql-revenue-by-segment", "sql": "ATTACH DATABASE '/tmp/secret' AS stolen"},
            )
            require(attack.status_code == 422, "unsafe SQL was not refused")
            require("/tmp/secret" not in attack.text, "SQL error leaked a filesystem path")

            created = client.post(
                "/api/prep/technical/sessions",
                json={"durationMinutes": 30, "role": "product-analyst"},
            ).json()
            running = client.post(f"/api/prep/technical/sessions/{created['id']}/start").json()
            require("scorecard" not in running, "running session leaked correctness")
            for question in running["questions"]:
                answer: object = "Define a decision, grain, metric, guardrail, and validation plan."
                if question["kind"] == "sql":
                    answer = sql
                elif question["kind"] == "python":
                    answer = [
                        {"customer_id": 1, "revenue": 300.0},
                        {"customer_id": 2, "revenue": 125.0},
                    ]
                saved = client.patch(
                    f"/api/prep/technical/sessions/{created['id']}/answers/{question['id']}",
                    json={"answer": answer},
                )
                require(saved.status_code == 200, "interview autosave failed")
            graded = client.post(f"/api/prep/technical/sessions/{created['id']}/submit").json()
            require(graded["state"] == "graded" and "scorecard" in graded, "delayed scorecard failed")

            print(json.dumps({
                "ok": True,
                "curriculumDrills": len(curriculum.json()["drills"]),
                "sqlRows": run.json()["rowCount"],
                "interviewQuestions": len(running["questions"]),
                "securityBoundary": "refused",
            }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
