from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = (
    "app/store.py",
    "app/automation.py",
    "app/compose.py",
    "app/contacts.py",
    "app/imported.py",
    "app/interview_intel.py",
    "app/interview_practice.py",
    "app/outreach_store.py",
    "app/overrides.py",
    "app/recruiter_messages.py",
    "app/technical_learning/store.py",
    "app/usage.py",
)


class RepositoryConnectionPolicyTests(unittest.TestCase):
    def test_repositories_do_not_own_schema_or_open_sqlite_directly(self) -> None:
        violations: list[str] = []
        for relative in REPOSITORIES:
            path = ROOT / relative
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    normalized = " ".join(node.value.upper().split())
                    if "CREATE TABLE" in normalized or "ALTER TABLE" in normalized:
                        violations.append(f"{relative}:{node.lineno}: request-time schema DDL")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"
                    and node.func.attr == "connect"
                ):
                    violations.append(f"{relative}:{node.lineno}: direct sqlite3.connect")

        self.assertEqual([], violations, "\n".join(violations))


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_initializes_the_database_once(self) -> None:
        from app import main

        with mock.patch.object(main, "initialize") as initialize:
            async with main.lifespan(main.app):
                initialize.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
