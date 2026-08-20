from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.technical_learning import datasets
from app.technical_learning.query_supervisor import run_sql
from app.technical_learning.sql_policy import QueryRefused, guard_sql


class TechnicalSqlPolicyTests(unittest.TestCase):
    def test_guard_accepts_one_read_statement(self) -> None:
        self.assertEqual(guard_sql(" -- explain\n SELECT 1; "), "SELECT 1")
        self.assertTrue(guard_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH"))

    def test_guard_rejects_empty_stacked_and_non_read_queries(self) -> None:
        rejected = [
            "", "-- only a comment", "SELECT 1; SELECT 2", "DELETE FROM orders",
            "WITH x AS (SELECT 1) DELETE FROM orders", "PRAGMA table_info(orders)",
            "ATTACH DATABASE '/tmp/a' AS stolen", "SELECT 1 /* ; */; DROP TABLE orders",
        ]
        for statement in rejected:
            with self.subTest(statement=statement), self.assertRaises(QueryRefused):
                guard_sql(statement)


class TechnicalSqlWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(datasets, "DATASET_ROOT", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_supervisor_executes_in_worker_and_caps_rows(self) -> None:
        result = run_sql("commerce", "1", "SELECT order_id FROM orders ORDER BY order_id", row_limit=5)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.row_count, 5)
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows[0], [1])

    def test_sql_errors_are_sanitized_learning_results(self) -> None:
        result = run_sql("commerce", "1", "SELECT missing FROM orders")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sql_error")
        self.assertNotIn(str(Path(self.tmp.name)), result.message or "")

    def test_authorizer_denies_reading_sqlite_metadata(self) -> None:
        result = run_sql("commerce", "1", "SELECT name FROM sqlite_master")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_allowed")

    def test_unknown_dataset_never_becomes_a_path(self) -> None:
        with self.assertRaisesRegex(KeyError, "dataset"):
            run_sql("../../careeros", "1", "SELECT 1")

    def test_wall_timeout_kills_runaway_worker(self) -> None:
        query = (
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) "
            "SELECT SUM(n) FROM x"
        )
        result = run_sql("commerce", "1", query, timeout_s=0.01)
        self.assertFalse(result.ok)
        self.assertIn(result.error_code, {"timeout", "instruction_limit"})


if __name__ == "__main__":
    unittest.main()
