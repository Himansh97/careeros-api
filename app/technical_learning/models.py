from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    fixture: dict[str, Any] = Field(default_factory=dict)
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
        if self.kind == "python" and not self.fixture:
            raise ValueError("Python drills require a synthetic fixture")
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


class LessonExample(BaseModel):
    """A worked example. Runnable when it names a dataset."""

    caption: str
    body: str = ""
    # When set, the example is executed against that dataset through the same
    # supervisor the drills use, so a SQL lesson shows real rows rather than a
    # claim about what the query would return.
    dataset_id: str | None = None
    sql: str | None = None


class VisualNode(BaseModel):
    label: str
    note: str = ""
    # "good" | "bad" | "neutral" — semantic emphasis, not decoration. A cell in
    # a confusion matrix means something; colouring it arbitrarily would teach
    # the wrong thing.
    tone: Literal["good", "bad", "neutral"] = "neutral"
    # Drives the visual channel where there is one: opacity in a heatmap,
    # length in a bar. Absent means the shape carries no magnitude.
    value: float | None = None

    @field_validator("tone", mode="before")
    @classmethod
    def blank_tone_is_neutral(cls, value: Any) -> Any:
        """An empty tone means none was specified, not an invalid one.

        Authoring these by hand, "" is what you type when a node needs no
        emphasis, and rejecting the whole curriculum for it took the entire
        lesson file down twice. Coercing is right here because the meaning is
        unambiguous — unlike a wrong tone, which should still fail.
        """
        return "neutral" if value in ("", None) else value


class VisualSeries(BaseModel):
    label: str
    # Points in unit space, 0..1 on both axes. Unit space rather than data
    # values because these curves are shapes to recognise — the bias-variance
    # tradeoff has no units — and a renderer that never has to scale cannot
    # mis-scale.
    points: list[tuple[float, float]] = Field(min_length=2)
    tone: Literal["good", "bad", "neutral"] = "neutral"


class LessonVisual(BaseModel):
    """A diagram, from a closed vocabulary the renderer knows how to draw.

    Six shapes, added only when a lesson genuinely could not be drawn with the
    existing ones. `matrix` exists because a confusion matrix is the clearest
    thing in classification and cannot be a list of boxes; `curve` exists
    because bias-variance and complexity growth are shapes, and describing a
    shape in prose is the exact failure a diagram fixes.

    Structured rather than an image so it renders in the app's own type and
    colours, stays readable in both themes, and can be corrected with an edit
    rather than redrawn.
    """

    kind: Literal["flow", "layers", "compare", "cycle",
                  "fanout", "heatmap", "curve", "bars"]
    caption: str = ""
    # flow | layers | compare | cycle
    nodes: list[VisualNode] = Field(default_factory=list)
    # matrix
    rows: list[str] = Field(default_factory=list)
    cols: list[str] = Field(default_factory=list)
    cells: list[VisualNode] = Field(default_factory=list)
    # curve. Aliased to the camelCase the renderer expects, so the JSON crosses
    # the wire in the shape the component already reads rather than needing a
    # translation layer that could drift.
    x_label: str = Field(default="", serialization_alias="xLabel")
    y_label: str = Field(default="", serialization_alias="yLabel")
    shade_gap: bool = Field(default=False, serialization_alias="shadeGap")
    series: list[VisualSeries] = Field(default_factory=list)

    @model_validator(mode="after")
    def drawable(self) -> "LessonVisual":
        if self.kind in ("flow", "layers", "compare", "cycle", "fanout", "bars"):
            if not 2 <= len(self.nodes) <= 6:
                # One box is not a diagram; seven is one nobody reads.
                raise ValueError(f"{self.kind} needs between 2 and 6 nodes")
            if self.kind == "bars" and any(n.value is None for n in self.nodes):
                # Length is the encoding; a bar with no value is just a box.
                raise ValueError("every bar needs a value")
        elif self.kind == "heatmap":
            if not (self.rows and self.cols):
                raise ValueError("a heatmap needs row and column labels")
            if len(self.cells) != len(self.rows) * len(self.cols):
                raise ValueError(
                    f"a {len(self.rows)}x{len(self.cols)} heatmap needs "
                    f"{len(self.rows) * len(self.cols)} cells, got {len(self.cells)}"
                )
        elif self.kind == "curve":
            if not self.series:
                raise ValueError("a curve needs at least one series")
            for line in self.series:
                for x, y in line.points:
                    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                        raise ValueError(
                            f"curve points are unit space; {line.label} has ({x}, {y})"
                        )
        return self


class Misconception(BaseModel):
    claim: str        # what people believe
    correction: str   # and why it is wrong


class Lesson(BaseModel):
    """A unit that teaches, as opposed to a Drill, which tests.

    Deliberately not a `kind` on `Drill`. That model's `grading_contract`
    validator requires every unit to be answerable — `solution` and `hints` have
    no defaults, and `grade_answer` falls through to SQL execution for any kind
    it does not recognise. A lesson has no answer, so bolting it on would mean
    special-casing it in every validator, dispatch branch and progress
    calculation. It shares `skill` and `concept` slugs instead, which is enough
    to hand off to a drill as retrieval practice afterwards.

    `key_points` is the load-bearing field: it is the set of facts the tutor is
    permitted to assert. The same device `compose.select_evidence` uses for
    outreach and `resume_coach` uses for rewrites — hand the model the facts,
    let it rephrase and illustrate, refuse anything added. A tutor that can
    invent a fact about window functions eventually will, and the person being
    taught is the least able to catch it.
    """

    id: str
    title: str
    track: str
    skill: str
    concept: str
    level: Literal["foundation", "working", "interview", "advanced"]
    order: int = 0
    prerequisites: list[str] = Field(default_factory=list)

    hook: str                     # why this matters, in one or two sentences
    objectives: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(min_length=1)
    example: LessonExample | None = None
    visual: LessonVisual | None = None
    misconceptions: list[Misconception] = Field(default_factory=list)
    interview_angle: str = ""
    sources: list[str] = Field(default_factory=list)
    # The drill to hand off to once this has been taught, if there is one.
    practice_drill_id: str | None = None

    @model_validator(mode="after")
    def teachable(self) -> "Lesson":
        if not self.key_points:
            # Without key points the tutor has no boundary, and an unbounded
            # tutor is just a model talking about a title.
            raise ValueError(f"lesson {self.id} has no key_points to teach from")
        if self.example and self.example.sql and not self.example.dataset_id:
            raise ValueError(f"lesson {self.id} has runnable SQL but names no dataset")
        return self


class Curriculum(BaseModel):
    version: str
    title: str
    drills: list[Drill]
    missions: list[Mission]
    lessons: list[Lesson] = Field(default_factory=list)

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

        lesson_ids = [lesson.id for lesson in self.lessons]
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("duplicate lesson id")
        lesson_known = set(lesson_ids)
        for lesson in self.lessons:
            # A lesson's prerequisites are other lessons, not drills — the path
            # through a subject is a path through explanations.
            missing = set(lesson.prerequisites) - lesson_known
            if missing:
                raise ValueError(f"unknown prerequisite for {lesson.id}: {sorted(missing)}")
            if lesson.practice_drill_id and lesson.practice_drill_id not in known:
                raise ValueError(
                    f"lesson {lesson.id} hands off to unknown drill "
                    f"{lesson.practice_drill_id!r}"
                )
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
