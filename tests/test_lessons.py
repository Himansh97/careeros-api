"""Lessons teach, the path is ordered, and the tutor is fenced in.

Four properties.

**Every worked example runs.** A lesson that claims a query returns 72 rows, on a
page that does not show it returning 72 rows, is asking to be believed about the
one thing it could simply demonstrate. Authoring one that the sandbox refuses —
`EXPLAIN QUERY PLAN` was the first attempt — must fail here rather than render an
empty table later. So must one that returns nothing: an anti-join example
matching zero rows teaches nothing, and the first draft of `sql-joins` did
exactly that.

**Prerequisites gate.** The drill engine declares `prerequisites` and enforces
them nowhere — the server checks only that the ids exist and the frontend lock is
dead code, because `clearedDrillIds` is never passed. A path that orders nothing
is decoration.

**The spine is the whole boundary.** What goes into the prompt is what the tutor
may assert, so anything absent from `key_points` must be absent from the prompt.

**`key_points` never reaches the client.** `_PRIVATE_FIELDS` in the drill
serialiser is a denylist, so new fields ship by default — `debrief` already
leaks that way. Shipping key points would also defeat the point: a bullet list to
skim instead of an explanation to read.

    ./.venv/bin/python tests/test_lessons.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.lessons import _unlocked, status  # noqa: E402
from app.technical_learning.curriculum import (  # noqa: E402
    load_curriculum,
    lessons_for,
)
from app.technical_learning.query_supervisor import run_sql  # noqa: E402
from app.tutor import MODES, _spine  # noqa: E402


class FakeLesson:
    def __init__(self, lesson_id, prerequisites, drill=None):
        self.id = lesson_id
        self.prerequisites = prerequisites
        self.practice_drill_id = drill


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    lessons = load_curriculum().lessons
    check("the curriculum carries lessons", len(lessons) > 0, True)

    # --- every worked example runs, and returns something ---
    for lesson in lessons:
        example = lesson.example
        if not (example and example.sql):
            continue
        result = run_sql(example.dataset_id, "1", example.sql)
        check(f"{lesson.id} example executes", result.ok, True)
        if result.ok:
            # Zero rows is a valid query and a useless demonstration.
            check(f"{lesson.id} example returns rows", len(result.rows) > 0, True)

    # --- every lesson is illustrated ---
    # Seventeen of forty-one shipped without one, so the Learn section showed a
    # diagram on four tracks and nothing on five, which reads as broken rather
    # than as sparse. A lesson with no picture is allowed to be a decision; it
    # is not allowed to be an oversight.
    undrawn = [l.id for l in lessons if not l.visual]
    check("every lesson has a diagram", undrawn, [])

    for lesson in lessons:
        if not lesson.visual:
            continue
        v = lesson.visual
        if v.kind == "bars":
            # Length is the encoding, so a bar with nothing to encode is a box.
            check(f"{lesson.id} bars all carry a value",
                  all(n.value is not None for n in v.nodes), True)
        if v.kind == "heatmap":
            check(f"{lesson.id} heatmap grid is complete",
                  len(v.cells), len(v.rows) * len(v.cols))
        if v.kind == "curve":
            inside = all(
                0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
                for s in v.series for x, y in s.points
            )
            check(f"{lesson.id} curve points are unit space", inside, True)

    # --- requirements read off a job page reach a lesson ---
    # This is the "learn while applying" path. It matched on track name and
    # title substring at first, which resolved "SQL" and almost nothing else —
    # a posting says "ETL", "Airflow" or "A/B testing", none of which is any
    # lesson's title. `teaches` is the authored answer, so it has to stay
    # unambiguous: two lessons claiming the same phrasing means the router
    # silently picks by track order rather than by meaning.
    from app.explain import _norm, explain  # noqa: PLC0415

    claimed: dict[str, str] = {}
    collisions: list[str] = []
    for lesson in lessons:
        for phrase in lesson.teaches:
            key = _norm(phrase)
            if key in claimed and claimed[key] != lesson.id:
                collisions.append(f"{phrase!r}: {claimed[key]} and {lesson.id}")
            claimed[key] = lesson.id
    check("no two lessons claim the same phrasing", collisions, [])

    class FakeProfile:
        # No claims, so the card half of the lookup finds nothing and the
        # assertions below are about lesson routing alone.
        evidence: list = []

    routed = {
        "ETL": "pipe-shapes",
        "Airflow": "pipe-shapes",
        "A/B testing": "stats-experiments",
        "RAG": "llm-rag",
        "Tableau": "bi-dashboards",
        "XGBoost": "ml-trees",
        "prompt engineering": "llm-prompting",
    }
    for term, expected in routed.items():
        found = explain(term, FakeProfile())["lesson"]
        check(f"{term!r} reaches a lesson", found and found["id"], expected)

    # And a requirement nothing teaches must stay silent. A door onto an
    # apology is worse than plain text.
    for absent in ("Anticipated Weekly Hours", "SOX", "xyzzy"):
        result = explain(absent, FakeProfile())
        check(f"{absent!r} routes nowhere", result["lesson"], None)

    # --- the lesson graph is sane ---
    ids = {lesson.id for lesson in lessons}
    for lesson in lessons:
        check(f"{lesson.id} prerequisites exist",
              set(lesson.prerequisites) <= ids, True)
        check(f"{lesson.id} has facts to teach from",
              len(lesson.key_points) >= 3, True)

    # A track must start somewhere, or nothing is ever unlocked.
    for track in {lesson.track for lesson in lessons}:
        roots = [l for l in lessons_for(track) if not l.prerequisites]
        check(f"track {track!r} has an entry point", len(roots) >= 1, True)

    # --- gating ---
    a = FakeLesson("a", [])
    b = FakeLesson("b", ["a"])
    check("a lesson with no prerequisites is open", _unlocked(a, {}), True)
    check("a lesson whose prerequisite is untouched is shut",
          _unlocked(b, {}), False)
    check("taught unlocks the next one",
          _unlocked(b, {"a": {"state": "taught"}}), True)
    check("explained unlocks it too",
          _unlocked(b, {"a": {"state": "explained"}}), True)

    # --- status, and what mastery requires ---
    linked = FakeLesson("c", [], drill="sql-revenue-by-segment")
    check("untouched reads as not-started", status(linked, {}, set()), "not-started")
    check("a stored pass is 'taught'",
          status(linked, {"c": {"state": "taught"}}, set()), "taught")
    check("explained alone is not mastery",
          status(linked, {"c": {"state": "explained"}}, set()), "explained")
    # Mastery needs the drill cleared as well — reusing the drill engine's own
    # definition rather than declaring a lesson mastered because it was read.
    check("explained plus a cleared drill is mastery",
          status(linked, {"c": {"state": "explained"}}, {"sql-revenue-by-segment"}),
          "mastered")

    # --- the tutor's boundary ---
    grain = next(l for l in lessons if l.id == "sql-grain")
    spine = _spine(grain)
    for point in grain.key_points:
        check("every key point reaches the prompt", point in spine, True)
        break  # one is enough to prove the wiring; the loop documents intent
    check("all key points reach the prompt",
          all(p in spine for p in grain.key_points), True)
    # Nothing invented can be justified by the spine, so the spine must not
    # contain facts the lesson does not claim.
    check("the prompt carries no other lesson's content",
          "window function" not in spine.lower(), True)
    check("modes are a closed set", sorted(MODES),
          ["deeper", "example", "simpler", "stuck", "teach"])

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
