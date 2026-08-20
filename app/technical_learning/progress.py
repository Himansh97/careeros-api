from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .curriculum import CURRICULUM_VERSION, get_drill, load_curriculum
from .grading import grade_rows, grade_rubric
from .models import Grade, QueryResult
from .query_supervisor import run_sql
from .sql_policy import QueryRefused
from .store import connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _python_grade(expected: Any, actual: Any) -> Grade:
    wanted = json.dumps(expected, sort_keys=True, separators=(",", ":"), default=str)
    got = json.dumps(actual, sort_keys=True, separators=(",", ":"), default=str)
    if wanted == got:
        return Grade(passed=True, score=1, summary="Normalized output matches.")
    return Grade(
        passed=False,
        score=0,
        summary="Normalized output differs.",
        differences=["Return the requested records and columns in the requested order."],
    )


def grade_answer(drill: Any, answer: Any) -> tuple[Grade, dict[str, Any]]:
    if drill.kind == "case":
        return grade_rubric(answer, drill.rubric), {"runtimeMs": None, "truncated": False}
    if drill.kind == "python":
        return _python_grade(drill.expected_output, answer), {"runtimeMs": None, "truncated": False}

    started = time.perf_counter()
    expected = run_sql(drill.dataset_id, drill.dataset_version, drill.expected_sql)
    try:
        actual = run_sql(drill.dataset_id, drill.dataset_version, str(answer))
    except QueryRefused as exc:
        actual = QueryResult(ok=False, error_code="query_refused", message=str(exc))
    runtime_ms = round((time.perf_counter() - started) * 1000)
    if not expected.ok:
        return Grade(passed=False, score=0, summary="Reference dataset is unavailable."), {
            "runtimeMs": runtime_ms,
            "truncated": False,
        }
    grade = grade_rows(
        expected.rows,
        actual,
        ordered=drill.ordered,
        numeric_tolerance=drill.numeric_tolerance,
    )
    return grade, {"runtimeMs": runtime_ms, "truncated": actual.truncated}


def hint_access(drill_id: str) -> dict[str, bool]:
    get_drill(drill_id)
    with connection() as db:
        failures = db.execute(
            "SELECT COUNT(*) FROM technical_attempts WHERE drill_id=? AND passed=0",
            (drill_id,),
        ).fetchone()[0]
    return {
        "conceptual": failures >= 1,
        "pattern": failures >= 2,
        "solutionRevealAvailable": failures >= 2,
    }


def submit_guided_attempt(
    drill_id: str,
    answer: Any,
    *,
    hints_unlocked: int = 0,
    solution_revealed: bool = False,
    curriculum_version: str = CURRICULUM_VERSION,
) -> dict[str, Any]:
    drill = get_drill(drill_id, curriculum_version)
    grade, metadata = grade_answer(drill, answer)
    cleared = grade.passed and hints_unlocked == 0 and not solution_revealed
    attempt_id = f"ta_{uuid.uuid4().hex}"
    created_at = _now()
    with connection() as db:
        db.execute(
            """INSERT INTO technical_attempts
               (id, curriculum_version, drill_id, dataset_version, answer_json,
                passed, score, summary, differences_json, hints_unlocked,
                solution_revealed, cleared, runtime_ms, truncated, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt_id,
                curriculum_version,
                drill_id,
                drill.dataset_version,
                json.dumps(answer, separators=(",", ":"), default=str),
                int(grade.passed),
                grade.score,
                grade.summary,
                json.dumps(grade.differences),
                max(0, hints_unlocked),
                int(solution_revealed),
                int(cleared),
                metadata["runtimeMs"],
                int(metadata["truncated"]),
                created_at,
            ),
        )
    return {
        "id": attempt_id,
        "drillId": drill_id,
        "grade": grade.model_dump(mode="json"),
        "cleared": cleared,
        "hints": hint_access(drill_id),
        "debrief": drill.debrief,
        "solutionAvailable": solution_revealed,
        "metadata": metadata,
        "createdAt": created_at,
    }


def progress_overview() -> dict[str, Any]:
    curriculum = load_curriculum()
    with connection() as db:
        rows = db.execute(
            "SELECT drill_id, MAX(score) best, MAX(cleared) cleared, COUNT(*) attempts "
            "FROM technical_attempts GROUP BY drill_id"
        ).fetchall()
    attempts = {row["drill_id"]: dict(row) for row in rows}
    skills: list[dict[str, Any]] = []
    for skill in sorted({drill.skill for drill in curriculum.drills}):
        drills = [drill for drill in curriculum.drills if drill.skill == skill]
        practice = [drill for drill in drills if drill.stage == "practice"]
        transfer = [drill for drill in drills if drill.stage == "transfer"]
        cleared_practice = any(attempts.get(drill.id, {}).get("cleared") for drill in practice)
        cleared_transfer = any(attempts.get(drill.id, {}).get("cleared") for drill in transfer)
        best = max((float(attempts.get(drill.id, {}).get("best") or 0) for drill in drills), default=0)
        skills.append(
            {
                "skill": skill,
                "cleared": sum(bool(attempts.get(drill.id, {}).get("cleared")) for drill in drills),
                "total": len(drills),
                "mastered": bool(cleared_practice and cleared_transfer),
                "personalBest": round(best, 3),
            }
        )
    recommendations = [
        {"drillId": drill.id, "title": drill.title, "reason": "Build the next transferable skill."}
        for drill in curriculum.drills
        if not attempts.get(drill.id, {}).get("cleared")
    ][:3]
    return {
        "curriculumVersion": curriculum.version,
        "skills": skills,
        "attempts": sum(int(row["attempts"]) for row in rows),
        "recommendations": recommendations,
    }
