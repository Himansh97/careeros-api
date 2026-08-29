"""Reading the curriculum: what to teach next, and how far through you are.

Kept apart from `tutor.py` so the thing that decides *what* to teach never
depends on the thing that generates prose. This module makes no model calls and
works with the API key absent — the path through a subject is knowable without
one.

Progress is not a flashcard box. You do not review the relational model every
three days, so Leitner is wrong here. A lesson is **taught** once its pass has
been read, **explained** once it has been said back, and **mastered** only when
the linked drill has also been cleared — reusing the drill engine's existing
definition of cleared (passed, no hints, no solution revealed) rather than
inventing a second one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import connect


def _states() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT lesson_id, state, explained_at FROM lesson_progress"
        ).fetchall()
        taught = {
            r["lesson_id"] for r in conn.execute(
                "SELECT DISTINCT lesson_id FROM lesson_pass WHERE mode='teach'"
            ).fetchall()
        }
    out = {r["lesson_id"]: dict(r) for r in rows}
    for lesson_id in taught:
        out.setdefault(lesson_id, {"lesson_id": lesson_id, "state": "taught",
                                   "explained_at": None})
    return out


def _cleared_drills() -> set[str]:
    """Drills cleared unaided — the drill engine's own definition, not a new one."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT drill_id FROM technical_attempts WHERE cleared=1"
        ).fetchall()
    return {r["drill_id"] for r in rows}


def status(lesson: Any, states: dict[str, dict[str, Any]],
           cleared: set[str]) -> str:
    state = (states.get(lesson.id) or {}).get("state")
    if state == "explained" and lesson.practice_drill_id in cleared:
        return "mastered"
    if state == "explained":
        return "explained"
    if state == "taught":
        return "taught"
    return "not-started"


def _unlocked(lesson: Any, states: dict[str, dict[str, Any]]) -> bool:
    """Prerequisites, actually enforced.

    The drill engine declares `prerequisites` and never checks them — the server
    validates only that the ids exist, and the frontend's lock is dead code
    because `clearedDrillIds` is never passed to it. A path that does not order
    anything is decoration, so this one gates.
    """
    return all(
        (states.get(prereq) or {}).get("state") in ("taught", "explained")
        for prereq in lesson.prerequisites
    )


def overview(track: str | None = None) -> dict[str, Any]:
    from .technical_learning.curriculum import lessons_for

    lessons = lessons_for(track)
    states = _states()
    cleared = _cleared_drills()

    items = []
    for lesson in lessons:
        items.append({
            "id": lesson.id,
            "title": lesson.title,
            "track": lesson.track,
            "level": lesson.level,
            "order": lesson.order,
            "hook": lesson.hook,
            "status": status(lesson, states, cleared),
            "unlocked": _unlocked(lesson, states),
            "prerequisites": lesson.prerequisites,
            "keyPoints": len(lesson.key_points),
            "hasExample": lesson.example is not None,
        })

    tracks = sorted({item["track"] for item in items})
    return {
        "lessons": items,
        "tracks": tracks,
        "taught": sum(1 for i in items if i["status"] != "not-started"),
        "total": len(items),
        # The next unlocked, unstarted lesson per track — the two-tracks-in-
        # parallel shape, without the caller working it out.
        "next": {
            t: next(
                (i["id"] for i in items
                 if i["track"] == t and i["unlocked"] and i["status"] == "not-started"),
                None,
            )
            for t in tracks
        },
    }


def detail(lesson_id: str) -> dict[str, Any]:
    """One lesson, with its worked example already executed."""
    from .technical_learning.curriculum import get_lesson
    from .technical_learning.query_supervisor import run_sql
    from .technical_learning.sql_policy import QueryRefused

    lesson = get_lesson(lesson_id)
    states = _states()

    example: dict[str, Any] | None = None
    if lesson.example:
        example = {
            "caption": lesson.example.caption,
            "body": lesson.example.body,
            "sql": lesson.example.sql,
            "result": None,
        }
        if lesson.example.sql and lesson.example.dataset_id:
            try:
                result = run_sql(lesson.example.dataset_id, "1", lesson.example.sql)
                example["result"] = result.model_dump(mode="json")
            except QueryRefused as exc:
                # The sandbox refusing a lesson's own example is an authoring
                # bug, and saying so beats rendering an empty table.
                example["result"] = {"ok": False, "error": str(exc)}

    return {
        "id": lesson.id,
        "title": lesson.title,
        "track": lesson.track,
        "level": lesson.level,
        "hook": lesson.hook,
        "objectives": lesson.objectives,
        "misconceptions": [m.model_dump(mode="json") for m in lesson.misconceptions],
        "interviewAngle": lesson.interview_angle,
        "sources": lesson.sources,
        "example": example,
        "practiceDrillId": lesson.practice_drill_id,
        "status": status(lesson, states, _cleared_drills()),
        # Deliberately absent: key_points. They are the tutor's boundary, not a
        # summary to read instead of the lesson — shipping them would turn a
        # taught explanation into a bullet list, which is the thing this whole
        # feature exists to replace.
    }


def mark_explained(lesson_id: str) -> dict[str, Any]:
    at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO lesson_progress (lesson_id, state, explained_at, updated_at) "
            "VALUES (?, 'explained', ?, ?) ON CONFLICT(lesson_id) DO UPDATE SET "
            "state='explained', explained_at=excluded.explained_at, "
            "updated_at=excluded.updated_at",
            (lesson_id, at, at),
        )
    return {"lessonId": lesson_id, "state": "explained", "explainedAt": at}
