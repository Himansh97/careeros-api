"""The schema ledger must make database history explicit and immutable."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db
from app.db import (
    MigrationChecksumMismatch,
    MigrationOrderError,
    connect,
    initialize,
)
from app.migrations.registry import Migration
from app.migrations.registry import load_migrations


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "careeros.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fresh_database_is_created_by_the_baseline_migration(self) -> None:
        status = initialize(path=self.path)

        with connect(read_only=True, path=self.path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            ledger = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            ).fetchall()

        self.assertEqual(status.current_version, 2)
        self.assertTrue(
            {"applications", "timeline", "approvals", "job_flags"}.issubset(tables)
        )
        self.assertEqual(
            [(row["version"], row["name"]) for row in ledger],
            [(1, "baseline"), (2, "trust_foundation")],
        )
        self.assertEqual(len(ledger[0]["checksum"]), 64)

    def test_initialize_is_idempotent(self) -> None:
        first = initialize(path=self.path)
        second = initialize(path=self.path)

        with connect(read_only=True, path=self.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]

        self.assertEqual(first.current_version, 2)
        self.assertEqual(second.current_version, 2)
        self.assertEqual(second.applied_versions, ())
        self.assertEqual(count, 2)

    def test_matching_legacy_database_is_baselined_without_replacing_rows(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.executescript(load_migrations()[0].sql)
            connection.execute(
                "INSERT INTO applications "
                "(id, job_id, title, company, status, created_at, updated_at) "
                "VALUES ('app_1', 'job_1', 'Analyst', 'Example', 'ready', 'a', 'a')"
            )

        status = initialize(path=self.path)

        with connect(read_only=True, path=self.path) as connection:
            row = connection.execute(
                "SELECT title, company, status FROM applications WHERE id='app_1'"
            ).fetchone()
        self.assertEqual(status.baselined_versions, (1,))
        self.assertEqual(tuple(row), ("Analyst", "Example", "ready"))

    def test_partial_legacy_schema_is_not_falsely_marked_as_baseline(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE applications (id TEXT PRIMARY KEY)"
            )

        with self.assertRaisesRegex(db.MigrationError, "missing baseline tables"):
            initialize(path=self.path)

    def test_checksum_change_is_refused(self) -> None:
        initialize(path=self.path)
        changed = Migration.build(1, "baseline", "SELECT 1;")
        trust = load_migrations()[1]

        with mock.patch.object(db, "load_migrations", return_value=(changed, trust)):
            with self.assertRaises(MigrationChecksumMismatch):
                initialize(path=self.path)

    def test_out_of_order_registry_is_refused_before_writing(self) -> None:
        second = Migration.build(2, "second", "CREATE TABLE second (id INTEGER);")
        first = Migration.build(1, "first", "CREATE TABLE first (id INTEGER);")

        with mock.patch.object(db, "load_migrations", return_value=(second, first)):
            with self.assertRaises(MigrationOrderError):
                initialize(path=self.path)
        self.assertFalse(self.path.exists())

    def test_failing_migration_rolls_back_schema_and_ledger(self) -> None:
        first = Migration.build(1, "first", "CREATE TABLE first (id INTEGER);")
        broken = Migration.build(
            2,
            "broken",
            "CREATE TABLE second (id INTEGER); INSERT INTO missing VALUES (1);",
        )

        with mock.patch.object(db, "load_migrations", return_value=(first, broken)):
            with self.assertRaises(sqlite3.OperationalError):
                initialize(path=self.path)

        with sqlite3.connect(self.path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("first", tables)
        self.assertNotIn("second", tables)
        self.assertNotIn("schema_migrations", tables)


if __name__ == "__main__":
    unittest.main()
