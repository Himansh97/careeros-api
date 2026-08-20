from __future__ import annotations

import unittest

from app.technical_learning.grading import grade_rows, grade_rubric
from app.technical_learning.models import QueryResult, RubricElement


class TechnicalRowGradingTests(unittest.TestCase):
    def result(self, rows, *, truncated=False) -> QueryResult:
        return QueryResult(ok=True, rows=rows, row_count=len(rows), truncated=truncated)

    def test_unordered_results_are_multisets_with_duplicate_counts(self) -> None:
        self.assertTrue(grade_rows([[1], [2], [1]], self.result([[2], [1], [1]]), ordered=False).passed)
        grade = grade_rows([[1], [1]], self.result([[1]]), ordered=False)
        self.assertFalse(grade.passed)
        self.assertIn("row count", grade.summary)

    def test_ordered_results_fail_on_first_different_row(self) -> None:
        grade = grade_rows([[1], [2]], self.result([[2], [1]]), ordered=True)
        self.assertFalse(grade.passed)
        self.assertIn("row 1", grade.differences[0])

    def test_nulls_and_numeric_tolerance_are_supported(self) -> None:
        grade = grade_rows([[None, 1.0]], self.result([[None, 1.0004]]), ordered=True, numeric_tolerance=0.001)
        self.assertTrue(grade.passed)

    def test_truncated_output_cannot_pass(self) -> None:
        grade = grade_rows([[1]], self.result([[1]], truncated=True), ordered=True)
        self.assertFalse(grade.passed)
        self.assertIn("truncated", grade.summary)


class TechnicalRubricGradingTests(unittest.TestCase):
    def test_explicit_rubric_reports_each_element(self) -> None:
        rubric = [
            RubricElement(id="unit", label="Unit", description="assignment", weight=2, accepted=["user"]),
            RubricElement(id="guard", label="Guardrail", description="safety", weight=1, accepted=["latency", "error"]),
        ]
        grade = grade_rubric("Randomize each user and monitor latency.", rubric)
        self.assertTrue(grade.passed)
        self.assertAlmostEqual(grade.score, 1.0)
        self.assertEqual([item["id"] for item in grade.rubric], ["unit", "guard"])

    def test_missing_weighted_elements_produce_actionable_feedback(self) -> None:
        rubric = [RubricElement(id="risk", label="Validity risks", description="name a threat", weight=1, accepted=["novelty", "peeking"])]
        grade = grade_rubric("Run it for two weeks.", rubric)
        self.assertFalse(grade.passed)
        self.assertIn("Validity risks", grade.differences[0])


if __name__ == "__main__":
    unittest.main()
