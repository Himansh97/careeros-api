from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import Curriculum, Drill, Lesson


CURRICULUM_VERSION = "2026.08.1"
_FILES = {CURRICULUM_VERSION: Path(__file__).with_name("curriculum_v1.json")}
# Lessons are authored in their own file. They are a different kind of writing —
# prose that teaches rather than tasks that grade — and keeping them apart means
# adding a lesson cannot corrupt a drill, and the drill file stays reviewable.
_LESSON_FILES = {CURRICULUM_VERSION: Path(__file__).with_name("lessons_v1.json")}
_PRIVATE_FIELDS = {"expected_sql", "expected_output", "rubric", "solution"}


@lru_cache(maxsize=4)
def load_curriculum(version: str = CURRICULUM_VERSION) -> Curriculum:
    try:
        path = _FILES[version]
    except KeyError as exc:
        raise KeyError(f"unknown curriculum version: {version}") from exc

    data = json.loads(path.read_text(encoding="utf-8"))
    lessons = _LESSON_FILES.get(version)
    if lessons and lessons.exists():
        data["lessons"] = json.loads(lessons.read_text(encoding="utf-8"))["lessons"]
    return Curriculum.model_validate(data)


def get_lesson(lesson_id: str, version: str = CURRICULUM_VERSION) -> Lesson:
    for lesson in load_curriculum(version).lessons:
        if lesson.id == lesson_id:
            return lesson
    raise KeyError(f"unknown lesson: {lesson_id}")


def lessons_for(track: str | None = None,
                version: str = CURRICULUM_VERSION) -> list[Lesson]:
    """Lessons in teaching order, optionally for one track."""
    found = [
        lesson for lesson in load_curriculum(version).lessons
        if track is None or lesson.track == track
    ]
    return sorted(found, key=lambda lesson: (lesson.track, lesson.order))


def get_drill(drill_id: str, version: str = CURRICULUM_VERSION) -> Drill:
    for drill in load_curriculum(version).drills:
        if drill.id == drill_id:
            return drill
    raise KeyError(f"unknown drill: {drill_id}")


def _public_drill(drill: Drill) -> dict[str, Any]:
    data = drill.model_dump(mode="json")
    for field in _PRIVATE_FIELDS:
        data.pop(field, None)
    return data


def public_curriculum(version: str = CURRICULUM_VERSION) -> dict[str, Any]:
    curriculum = load_curriculum(version)
    return {
        "version": curriculum.version,
        "title": curriculum.title,
        "drills": [_public_drill(drill) for drill in curriculum.drills],
        "missions": [mission.model_dump(mode="json") for mission in curriculum.missions],
    }


def public_drill(drill_id: str, version: str = CURRICULUM_VERSION) -> dict[str, Any]:
    return _public_drill(get_drill(drill_id, version))
