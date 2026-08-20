"""Behavioral tests for the one CareerOS SQLite runtime."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import connect, integrity_report, transaction


class DatabaseRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "careeros.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_read_only_connection_refuses_a_missing_database(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            with connect(read_only=True, path=self.path):
                self.fail("a missing read-only database was opened")
        self.assertFalse(self.path.exists())

    def test_writable_connection_applies_runtime_safety_pragmas(self) -> None:
        with connect(path=self.path) as connection:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 5000)
        self.assertEqual(journal_mode.lower(), "wal")

    def test_connection_is_closed_when_the_context_exits(self) -> None:
        with connect(path=self.path) as connection:
            connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_transaction_rolls_back_the_entire_write_on_error(self) -> None:
        with connect(path=self.path) as connection:
            connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")

        with self.assertRaisesRegex(RuntimeError, "stop"):
            with transaction("IMMEDIATE", path=self.path) as connection:
                connection.execute("INSERT INTO proof (id) VALUES (1)")
                raise RuntimeError("stop")

        with connect(read_only=True, path=self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM proof").fetchone()[0]
        self.assertEqual(count, 0)

    def test_integrity_report_is_machine_readable_and_non_mutating(self) -> None:
        with connect(path=self.path) as connection:
            connection.execute(
                "CREATE TABLE parent (id INTEGER PRIMARY KEY)"
            )
            connection.execute(
                "CREATE TABLE child ("
                "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL "
                "REFERENCES parent(id))"
            )

        report = integrity_report(path=self.path)

        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreignKeyViolations"], [])
        self.assertEqual(report["migrationVersion"], 0)
        self.assertEqual(report["databaseExists"], True)


if __name__ == "__main__":
    unittest.main()
