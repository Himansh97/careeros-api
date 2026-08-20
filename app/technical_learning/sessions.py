from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .curriculum import CURRICULUM_VERSION, load_curriculum
from .progress import grade_answer
from .store import connection


_DURATIONS = {30: 3, 45: 5, 60: 7}
_BASE_IDS = [
    "sql-revenue-by-segment",
    "python-paid-revenue",
    "stats-ab-test",
    "model-orders-star",
    "etl-duplicate-orders",
    "dashboard-exec-review",
    "metrics-marketplace-tree",
]


def _time(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _public_question(drill: dict[str, Any]) -> dict[str, Any]:
    hidden = {"expected_sql", "expected_output", "rubric", "solution"}
    return {key: value for key, value in drill.items() if key not in hidden}


def _manifest(duration: int, role: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    curriculum = load_curriculum()
    by_id = {drill.id: drill for drill in curriculum.drills}
    selected = list(_BASE_IDS[: _DURATIONS[duration]])
    if role:
        mission = next((mission for mission in curriculum.missions if mission.role == role), None)
        if mission is None:
            raise ValueError(f"unknown role: {role}")
        role_drill = mission.drill_ids[-1]
        if role_drill not in selected:
            selected[-1] = role_drill
    private = [by_id[drill_id].model_dump(mode="json") for drill_id in selected]
    public = [_public_question(drill) for drill in private]
    return public, private


def _row(session_id: str) -> dict[str, Any]:
    with connection() as db:
        row = db.execute("SELECT * FROM technical_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        answers = {
            answer["question_id"]: json.loads(answer["answer_json"])
            for answer in db.execute(
                "SELECT question_id, answer_json FROM technical_session_answers WHERE session_id=?",
                (session_id,),
            )
        }
    return {
        "id": row["id"],
        "curriculumVersion": row["curriculum_version"],
        "durationMinutes": row["duration_minutes"],
        "role": row["role"],
        "state": row["state"],
        "questions": json.loads(row["public_manifest_json"]),
        "answers": answers,
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "expiresAt": row["expires_at"],
        "completedAt": row["completed_at"],
        "completionReason": row["completion_reason"],
        "serverNow": datetime.now(UTC).isoformat(),
        **({"scorecard": json.loads(row["scorecard_json"])} if row["scorecard_json"] else {}),
    }


def create_session(
    duration_minutes: int,
    *,
    role: str | None = None,
    now: datetime | None = None,
    curriculum_version: str = CURRICULUM_VERSION,
) -> dict[str, Any]:
    if duration_minutes not in _DURATIONS:
        raise ValueError("duration must be 30, 45, or 60 minutes")
    if curriculum_version != CURRICULUM_VERSION:
        load_curriculum(curriculum_version)
    public, private = _manifest(duration_minutes, role)
    created = _time(now)
    session_id = f"ts_{uuid.uuid4().hex}"
    with connection() as db:
        db.execute(
            """INSERT INTO technical_sessions
               (id, curriculum_version, duration_minutes, role, state,
                public_manifest_json, grading_manifest_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                session_id,
                curriculum_version,
                duration_minutes,
                role,
                "created",
                json.dumps(public, separators=(",", ":")),
                json.dumps(private, separators=(",", ":")),
                _iso(created),
            ),
        )
    return _row(session_id)


def start_session(session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    started = _time(now)
    with connection() as db:
        row = db.execute("SELECT state, duration_minutes FROM technical_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        if row["state"] == "created":
            db.execute(
                "UPDATE technical_sessions SET state='running', started_at=?, expires_at=? WHERE id=?",
                (_iso(started), _iso(started + timedelta(minutes=row["duration_minutes"])), session_id),
            )
        elif row["state"] != "running":
            raise ValueError("session is closed")
    return _row(session_id)


def save_answer(session_id: str, question_id: str, answer: Any, *, now: datetime | None = None) -> dict[str, Any]:
    snapshot = get_session(session_id, now=now)
    if snapshot["state"] != "running":
        raise ValueError("session is closed")
    if question_id not in {question["id"] for question in snapshot["questions"]}:
        raise KeyError(f"unknown session question: {question_id}")
    saved = _iso(_time(now))
    with connection() as db:
        db.execute(
            """INSERT INTO technical_session_answers(session_id, question_id, answer_json, saved_at)
               VALUES (?,?,?,?) ON CONFLICT(session_id, question_id) DO UPDATE SET
               answer_json=excluded.answer_json, saved_at=excluded.saved_at""",
            (session_id, question_id, json.dumps(answer, separators=(",", ":"), default=str), saved),
        )
    return {"sessionId": session_id, "questionId": question_id, "savedAt": saved}


def _finalize(session_id: str, *, now: datetime, reason: str) -> dict[str, Any]:
    with connection() as db:
        row = db.execute(
            "SELECT grading_manifest_json, state FROM technical_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        if row["state"] == "graded":
            return _row(session_id)
        private = json.loads(row["grading_manifest_json"])
        answers = {
            answer["question_id"]: json.loads(answer["answer_json"])
            for answer in db.execute(
                "SELECT question_id, answer_json FROM technical_session_answers WHERE session_id=?",
                (session_id,),
            )
        }

    from .models import Drill

    results = []
    for raw in private:
        drill = Drill.model_validate(raw)
        answer = answers.get(drill.id, "")
        grade, _metadata = grade_answer(drill, answer)
        results.append(
            {
                "questionId": drill.id,
                "title": drill.title,
                "skill": drill.skill,
                "passed": grade.passed,
                "score": grade.score,
                "summary": grade.summary,
                "differences": grade.differences,
                "rubric": grade.rubric,
                "solution": drill.solution,
                "debrief": drill.debrief,
            }
        )
    overall = sum(result["score"] for result in results) / len(results) if results else 0.0
    scorecard = {
        "score": round(overall, 3),
        "passed": overall >= 0.7,
        "questions": results,
        "reviewQueue": [result["questionId"] for result in results if not result["passed"]],
    }
    with connection() as db:
        db.execute(
            """UPDATE technical_sessions SET state='graded', completed_at=?,
               completion_reason=?, scorecard_json=? WHERE id=?""",
            (_iso(now), reason, json.dumps(scorecard, separators=(",", ":")), session_id),
        )
    return _row(session_id)


def submit_session(session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    moment = _time(now)
    snapshot = _row(session_id)
    if snapshot["state"] == "graded":
        return snapshot
    if snapshot["state"] != "running":
        raise ValueError("session has not started")
    expires = datetime.fromisoformat(snapshot["expiresAt"])
    return _finalize(session_id, now=moment, reason="expired" if moment >= expires else "submitted")


def get_session(session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    snapshot = _row(session_id)
    moment = _time(now)
    if snapshot["state"] == "running" and snapshot["expiresAt"]:
        if moment >= datetime.fromisoformat(snapshot["expiresAt"]):
            return _finalize(session_id, now=moment, reason="expired")
    return snapshot
