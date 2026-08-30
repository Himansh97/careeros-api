"""The check must catch what the old grader passes, and invent nothing.

Two properties, and the first one is the reason this module exists at all.

**A confident negation is not a pass.** `technical_learning.grading.grade_rubric`
scores `term.casefold() in text.casefold()` per rubric element and passes at
70%, so an answer naming every expected term while denying all of them scores
1.0. On a drill that is a bad grade. On an explanation it is worse than no check:
it tells someone they understand an idea they just stated backwards, which is
precisely the error the Feynman technique exists to surface. The negation below
is pinned here so that reusing the substring grader for this can never be a
quiet decision.

**Invented gaps have nowhere to live.** The model returns indices into the key
points, and `_parse` drops anything outside that range. A checker that invents a
gap is worse than a tutor that invents a fact — the learner already suspects
they misunderstood, so they will believe it. Prompting for this is a request;
dropping it is a boundary.

The parse tests are offline and deterministic. The grading test costs one call
and is skipped without a key, because a test that silently passes when the thing
it checks never ran is not a test.

    ./.venv/bin/python tests/test_feynman.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.feynman import _parse, check  # noqa: E402
from app.technical_learning.curriculum import get_lesson  # noqa: E402

# The exact shape that beats `grade_rubric`: every expected term present, every
# idea denied.
NEGATION = (
    "Grain does not change when you join. Joining loans to payments keeps one "
    "row per loan, so the fan-out never affects the total, and summing principal "
    "after a join gives you the same number as before. Counting with DISTINCT is "
    "unnecessary because duplicate rows are not produced."
)

# A correct explanation in entirely non-technical words. This must read as
# carried — someone doing the exercise properly does not use the lesson's
# vocabulary, and marking them down for that would teach them to parrot.
PLAIN = (
    "Before you join, one line in the table is one loan. Once you attach the "
    "payments, each loan turns into as many lines as it has payments — three "
    "payments means three lines for that one loan. Nothing breaks and nothing "
    "warns you. But if you now add up the principal, that loan's principal gets "
    "counted once for every payment line, so your total comes out several times "
    "too big. The line no longer means what it used to mean, and that is the "
    "part you have to notice yourself."
)


def main() -> int:
    failures: list[str] = []

    def check_(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # --- the boundary, offline ---
    # An index outside the key points is an invented gap and must be dropped.
    parsed = _parse('{"carried": [1], "missed": [99, 2], "backwards": [-4]}', 3, "x")
    assert parsed is not None
    check_("an out-of-range gap is dropped",
           [p for p in parsed["missed"] if p > 3], [])
    check_("every real point is classified",
           sorted(parsed["carried"] + parsed["missed"] + parsed["backwards"]),
           [1, 2, 3])

    # A point the model forgot to mention must not become a pass by default.
    parsed = _parse('{"carried": [1]}', 3, "x")
    assert parsed is not None
    check_("an unclassified point counts as missed", parsed["missed"], [2, 3])

    # A "quotation" the learner never said would be read as their words.
    parsed = _parse(
        '{"carried": [], "missed": [], "backwards": [1, 2],'
        ' "quote": {"1": "grain never changes", "2": "words they did not write"}}',
        2,
        "I said grain never changes, which I now doubt.",
    )
    assert parsed is not None
    check_("a real quotation survives", parsed["quotes"].get(1), "grain never changes")
    check_("a fabricated quotation is dropped", parsed["quotes"].get(2), None)

    # `next` must be something worth re-teaching, never a point already carried.
    parsed = _parse('{"carried": [1], "missed": [2], "backwards": [3], "next": 1}',
                    3, "x")
    assert parsed is not None
    check_("next never points at a carried idea", parsed["next"], 3)

    check_("junk is refused rather than guessed", _parse("no json here", 3, "x"), None)

    # --- the grading itself ---
    from app.llm import available  # noqa: PLC0415

    ready, why = available()
    if not ready:
        print(f"\nSKIPPED the live grading checks — {why}")
        print("  (set ANTHROPIC_API_KEY to run them; they cost about $0.01)")
    else:
        lesson = get_lesson("sql-grain")
        points = list(lesson.key_points)

        negated = check(points=points, title=lesson.title, explanation=NEGATION)
        check_("the negation is graded at all", negated["ok"], True)
        if negated["ok"]:
            # The whole point. `grade_rubric` scores this 1.0 and passes it.
            check_("a confident negation is not settled", negated["settled"], False)
            check_("a confident negation reads as backwards",
                   len(negated["backwards"]) > 0, True)
            print(f"     backwards on {len(negated['backwards'])} of "
                  f"{len(points)} points, ${negated['costUsd']}")

        plain = check(points=points, title=lesson.title, explanation=PLAIN)
        check_("a plain-English explanation is graded", plain["ok"], True)
        if plain["ok"]:
            # Judging the idea, not the vocabulary.
            check_("plain words are not counted as backwards",
                   plain["backwards"], [])
            check_("plain words carry the core",
                   len(plain["carried"]) >= 2, True)
            print(f"     carried {len(plain['carried'])}/{len(points)}, "
                  f"missed {len(plain['missed'])}, ${plain['costUsd']}")

    # --- refusals cost nothing ---
    short = check(points=["a"], title="t", explanation="dunno")
    check_("too short to check is refused", short["ok"], False)
    check_("a topic with no key points is refused",
           check(points=[], title="t", explanation="x" * 80)["ok"], False)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
