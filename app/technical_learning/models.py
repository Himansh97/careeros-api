from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class HintSet(BaseModel):
    conceptual: str
    pattern: str


class RubricElement(BaseModel):
    id: str
    label: str
    description: str
    weight: float = Field(gt=0)
    accepted: list[str] = Field(default_factory=list)


class Drill(BaseModel):
    id: str
    title: str
    track: Literal["analytics-core", "data-stack", "role-mission"]
    skill: str
    concept: str
    kind: Literal["sql", "python", "case"]
    stage: Literal["practice", "transfer"]
    difficulty: Literal["foundation", "intermediate", "advanced"]
    prerequisites: list[str] = Field(default_factory=list)
    prompt: str
    brief: str
    example: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    starter_answer: str = ""
    expected_sql: str | None = None
    expected_output: Any | None = None
    rubric: list[RubricElement] = Field(default_factory=list)
    ordered: bool = False
    numeric_tolerance: float = 0.0
    hints: HintSet
    solution: str
    debrief: str
    transfer_group: str
    interview_eligible: bool = True

    @model_validator(mode="after")
    def grading_contract(self) -> "Drill":
        if self.kind == "sql" and not self.expected_sql:
            raise ValueError("SQL drills require expected_sql")
        if self.kind == "python" and self.expected_output is None:
            raise ValueError("Python drills require expected_output")
        if self.kind == "case" and not self.rubric:
            raise ValueError("Case drills require rubric")
        if self.kind in {"sql", "python"} and not self.dataset_id:
            raise ValueError("Executable drills require a dataset")
        return self


class Mission(BaseModel):
    id: str
    role: str
    title: str
    description: str
    drill_ids: list[str]


class Curriculum(BaseModel):
    version: str
    title: str
    drills: list[Drill]
    missions: list[Mission]

    @model_validator(mode="after")
    def valid_graph(self) -> "Curriculum":
        ids = [drill.id for drill in self.drills]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate drill id")
        known = set(ids)
        for drill in self.drills:
            missing = set(drill.prerequisites) - known
            if missing:
                raise ValueError(f"unknown prerequisite for {drill.id}: {sorted(missing)}")
        for mission in self.missions:
            missing = set(mission.drill_ids) - known
            if missing:
                raise ValueError(f"unknown mission drill for {mission.id}: {sorted(missing)}")
        return self


class QueryResult(BaseModel):
    ok: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error_code: str | None = None
    message: str | None = None


class Grade(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str
    differences: list[str] = Field(default_factory=list)
    rubric: list[dict[str, Any]] = Field(default_factory=list)
