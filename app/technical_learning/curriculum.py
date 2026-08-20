from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import Curriculum, Drill


CURRICULUM_VERSION = "2026.08.1"
_FILES = {CURRICULUM_VERSION: Path(__file__).with_name("curriculum_v1.json")}
_PRIVATE_FIELDS = {"expected_sql", "expected_output", "rubric", "solution"}


@lru_cache(maxsize=4)
def load_curriculum(version: str = CURRICULUM_VERSION) -> Curriculum:
    try:
        path = _FILES[version]
    except KeyError as exc:
        raise KeyError(f"unknown curriculum version: {version}") from exc
    return Curriculum.model_validate_json(path.read_text(encoding="utf-8"))


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
