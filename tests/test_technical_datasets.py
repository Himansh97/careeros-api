from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.technical_learning import datasets


class TechnicalDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.object(datasets, "DATASET_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_generation_is_deterministic_for_each_version(self) -> None:
        first = datasets.ensure_dataset("commerce", "1")
        digest_one = hashlib.sha256(first.read_bytes()).hexdigest()
        first.unlink()
        second = datasets.ensure_dataset("commerce", "1")
        digest_two = hashlib.sha256(second.read_bytes()).hexdigest()
        self.assertEqual(digest_one, digest_two)

    def test_only_allowlisted_dataset_ids_and_versions_resolve(self) -> None:
        with self.assertRaisesRegex(KeyError, "dataset"):
            datasets.ensure_dataset("../../careeros", "1")
        with self.assertRaisesRegex(KeyError, "version"):
            datasets.ensure_dataset("commerce", "99")

    def test_public_schema_is_useful_and_contains_no_pii_columns(self) -> None:
        schema = datasets.dataset_schema("lending", "1")
        self.assertGreaterEqual(len(schema), 2)
        names = {column["name"].lower() for table in schema for column in table["columns"]}
        for forbidden in {"name", "email", "phone", "address", "body", "draft"}:
            self.assertNotIn(forbidden, names)

    def test_synthetic_datasets_are_readable_and_have_stable_rows(self) -> None:
        path = datasets.ensure_dataset("commerce", "1")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 12)
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 20)
        finally:
            connection.close()

    def test_private_snapshot_is_aggregate_only_and_omits_pii(self) -> None:
        source = self.root / "source.sqlite3"
        target = self.root / "private.sqlite3"
        with sqlite3.connect(source) as connection:
            connection.execute(
                "CREATE TABLE applications "
                "(id TEXT, company TEXT, title TEXT, status TEXT, created_at TEXT, "
                "contact_name TEXT, contact_email TEXT, notes TEXT)"
            )
            connection.executemany(
                "INSERT INTO applications VALUES (?,?,?,?,?,?,?,?)",
                [
                    ("1", "Acme", "Analyst", "applied", "2026-08-01", "Recruiter", "r@example.com", "private"),
                    ("2", "Beta", "Engineer", "applied", "2026-08-05", "Hiring", "h@example.com", "secret"),
                ],
            )

        built = datasets.build_private_snapshot(source, target)
        with sqlite3.connect(built) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(applications_summary)")}
            rows = connection.execute("SELECT status, created_month, application_count FROM applications_summary").fetchall()

        self.assertEqual(columns, {"status", "created_month", "application_count"})
        self.assertEqual(rows, [("applied", "2026-08", 2)])
        self.assertNotIn("example.com", built.read_bytes().decode("utf-8", errors="ignore"))


if __name__ == "__main__":
    unittest.main()
