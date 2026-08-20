"""One connection policy and migration boundary for the CareerOS database."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from .config import DB_PATH
from .migrations.registry import Migration, load_migrations

_TRANSACTION_MODES = {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}
_BASELINE_TABLES = {
    "applications",
    "approvals",
    "automation_rules",
    "automation_runs",
    "bullet_overrides",
    "compose_openings",
    "contacts",
    "document_edits",
    "imported_jobs",
    "interview_intel",
    "job_flags",
    "llm_usage",
    "outreach",
    "practice_attempts",
    "question_research",
    "recruiter_messages",
    "recruiter_reply_drafts",
    "saved_searches",
    "technical_attempts",
    "technical_session_answers",
    "technical_sessions",
    "timeline",
}


class MigrationError(RuntimeError):
    """Base class for an invalid or unverifiable migration history."""


class MigrationOrderError(MigrationError):
    """The registry is not strictly ordered by version."""


class MigrationChecksumMismatch(MigrationError):
    """An already-applied migration was edited."""


@dataclass(frozen=True)
class MigrationStatus:
    current_version: int
    applied_versions: tuple[int, ...] = ()
    baselined_versions: tuple[int, ...] = ()


def _open(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode=WAL")
    return connection


@contextmanager
def connect(
    *, read_only: bool = False, path: Path | None = None
) -> Iterator[sqlite3.Connection]:
    """Open, configure, commit or roll back, and always close one connection."""
    connection = _open(Path(path or DB_PATH), read_only=read_only)
    try:
        yield connection
        if not read_only:
            connection.commit()
    except BaseException:
        if not read_only:
            connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def transaction(
    mode: str = "DEFERRED", *, path: Path | None = None
) -> Iterator[sqlite3.Connection]:
    """Run one explicit SQLite transaction using a validated lock mode."""
    normalized = mode.upper()
    if normalized not in _TRANSACTION_MODES:
        raise ValueError(f"Unsupported transaction mode: {mode}")
    connection = _open(Path(path or DB_PATH), read_only=False)
    try:
        connection.execute(f"BEGIN {normalized}")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def integrity_report(*, path: Path | None = None) -> dict[str, object]:
    """Return integrity and migration facts without mutating the database."""
    database = Path(path or DB_PATH)
    with connect(read_only=True, path=database) as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity = (
            "ok"
            if len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"
            else "failed"
        )
        violations = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        ledger = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        version = (
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            if ledger
            else 0
        )
    return {
        "databaseExists": database.exists(),
        "integrity": integrity,
        "foreignKeyViolations": violations,
        "migrationVersion": int(version),
    }


def _validate_registry(migrations: tuple[Migration, ...]) -> None:
    versions = tuple(migration.version for migration in migrations)
    if versions != tuple(sorted(set(versions))):
        raise MigrationOrderError("Migration versions must be unique and ordered")


def _statements(sql: str) -> Iterator[str]:
    statement = ""
    for character in sql:
        statement += character
        if sqlite3.complete_statement(statement):
            if statement.strip():
                yield statement
            statement = ""
    if statement.strip():
        raise sqlite3.OperationalError("Incomplete migration statement")


def _apply_sql(connection: sqlite3.Connection, sql: str) -> None:
    for statement in _statements(sql):
        connection.execute(statement)


def _legacy_schema_present(connection: sqlite3.Connection) -> bool:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_migrations'"
        )
    }
    if not tables:
        return False
    missing = sorted(_BASELINE_TABLES - tables)
    if missing:
        raise MigrationError(
            "Existing database is missing baseline tables: " + ", ".join(missing)
        )
    return True


def initialize(*, path: Path | None = None) -> MigrationStatus:
    """Verify migration history and atomically apply every pending migration."""
    migrations = tuple(load_migrations())
    _validate_registry(migrations)
    database = Path(path or DB_PATH)
    connection = _open(database, read_only=False)
    applied_now: list[int] = []
    baselined: list[int] = []
    try:
        connection.execute("BEGIN EXCLUSIVE")
        ledger_exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        legacy = not ledger_exists and _legacy_schema_present(connection)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL, "
            "checksum TEXT NOT NULL)"
        )
        applied = {
            int(row["version"]): row
            for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            )
        }
        by_version = {migration.version: migration for migration in migrations}
        unknown = sorted(set(applied) - set(by_version))
        if unknown:
            raise MigrationError(
                f"Database contains unknown migration versions: {unknown}"
            )
        for version, row in applied.items():
            migration = by_version[version]
            if row["name"] != migration.name or row["checksum"] != migration.checksum:
                raise MigrationChecksumMismatch(
                    f"Migration {version:04d} no longer matches its applied checksum"
                )

        for migration in migrations:
            if migration.version in applied:
                continue
            if legacy and migration.version == 1:
                baselined.append(migration.version)
            else:
                _apply_sql(connection, migration.sql)
                applied_now.append(migration.version)
            connection.execute(
                "INSERT INTO schema_migrations "
                "(version, name, applied_at, checksum) VALUES (?,?,?,?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                    migration.checksum,
                ),
            )

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"Foreign-key check failed with {len(violations)} violation(s)"
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            raise MigrationError("SQLite integrity check failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    current = migrations[-1].version if migrations else 0
    return MigrationStatus(
        current_version=current,
        applied_versions=tuple(applied_now),
        baselined_versions=tuple(baselined),
    )
