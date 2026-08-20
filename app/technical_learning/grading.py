from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from .models import Grade, QueryResult, RubricElement


def _equal(expected: Any, actual: Any, tolerance: float) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=tolerance)
    return str(expected) == str(actual)


def _row_equal(expected: list[Any], actual: list[Any], tolerance: float) -> bool:
    return len(expected) == len(actual) and all(
        _equal(wanted, got, tolerance) for wanted, got in zip(expected, actual)
    )


def _canonical(row: list[Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def grade_rows(
    expected: list[list[Any]],
    actual: QueryResult,
    *,
    ordered: bool,
    numeric_tolerance: float = 0.0,
) -> Grade:
    if not actual.ok:
        return Grade(passed=False, score=0, summary=actual.message or "Query did not run.")
    if actual.truncated:
        return Grade(passed=False, score=0, summary="Result was truncated and cannot be graded as complete.")
    if len(expected) != len(actual.rows):
        return Grade(
            passed=False,
            score=0,
            summary=f"Expected row count {len(expected)}, got {len(actual.rows)}.",
        )
    if ordered:
        pairs = list(zip(expected, actual.rows))
    elif numeric_tolerance == 0:
        wanted = Counter(_canonical(row) for row in expected)
        got = Counter(_canonical(row) for row in actual.rows)
        if wanted == got:
            return Grade(passed=True, score=1, summary=f"Exact match: {len(expected)} rows.")
        return Grade(passed=False, score=0, summary="Rows differ.", differences=["The returned multiset does not match the expected rows."])
    else:
        remaining = list(actual.rows)
        for wanted in expected:
            match = next((index for index, row in enumerate(remaining) if _row_equal(wanted, row, numeric_tolerance)), None)
            if match is None:
                return Grade(passed=False, score=0, summary="Rows differ.", differences=[f"Missing expected row: {wanted}"])
            remaining.pop(match)
        return Grade(passed=True, score=1, summary=f"Exact match: {len(expected)} rows.")

    for index, (wanted, got) in enumerate(pairs, start=1):
        if not _row_equal(wanted, got, numeric_tolerance):
            return Grade(
                passed=False,
                score=0,
                summary="Rows differ.",
                differences=[f"row {index} differs: expected {wanted}, got {got}"],
            )
    return Grade(passed=True, score=1, summary=f"Exact match: {len(expected)} rows.")


def grade_rubric(answer: str | dict[str, Any], rubric: list[RubricElement]) -> Grade:
    text = answer if isinstance(answer, str) else " ".join(str(value) for value in answer.values())
    normalized = text.casefold()
    total = sum(element.weight for element in rubric) or 1.0
    earned = 0.0
    details: list[dict[str, Any]] = []
    differences: list[str] = []
    for element in rubric:
        matched = any(term.casefold() in normalized for term in element.accepted)
        if matched:
            earned += element.weight
        else:
            differences.append(f"Missing {element.label}: {element.description}")
        details.append(
            {
                "id": element.id,
                "label": element.label,
                "met": matched,
                "feedback": "Demonstrated." if matched else element.description,
            }
        )
    score = earned / total
    return Grade(
        passed=score >= 0.7,
        score=score,
        summary="Rubric threshold met." if score >= 0.7 else "Address the missing rubric elements.",
        differences=differences,
        rubric=details,
    )
