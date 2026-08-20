"""One-query disposable SQLite worker.

This module intentionally imports no CareerOS storage or profile code. Its complete
authority is the already-resolved synthetic dataset path provided on stdin.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _limits() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        # Limit new address-space growth without making platform support mandatory.
        current = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if current < 220 * 1024:
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    except (ImportError, OSError, ValueError):
        pass


def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
    denied = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_UPDATE,
    }
    if action in denied:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower() in {"sqlite_master", "sqlite_schema"}:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def execute(request: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(request["datasetPath"]))
    row_limit = max(1, min(int(request.get("rowLimit", 200)), 500))
    instruction_limit = max(1_000, min(int(request.get("instructionLimit", 2_000_000)), 10_000_000))
    if not path.is_file():
        return {"ok": False, "errorCode": "dataset_missing", "message": "Dataset is unavailable."}

    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    connection.enable_load_extension(False)
    connection.set_authorizer(_authorizer)
    remaining = instruction_limit

    def progress() -> int:
        nonlocal remaining
        remaining -= 1000
        return 1 if remaining <= 0 else 0

    connection.set_progress_handler(progress, 1000)
    try:
        cursor = connection.execute(str(request["sql"]))
        rows = cursor.fetchmany(row_limit + 1)
        columns = [description[0] for description in (cursor.description or [])]
        return {
            "ok": True,
            "columns": columns,
            "rows": [list(row) for row in rows[:row_limit]],
            "rowCount": min(len(rows), row_limit),
            "truncated": len(rows) > row_limit,
        }
    except sqlite3.DatabaseError as exc:
        message = str(exc).lower()
        if "interrupted" in message:
            return {"ok": False, "errorCode": "instruction_limit", "message": "Query exceeded the execution limit."}
        if "not authorized" in message or "access to" in message:
            return {"ok": False, "errorCode": "not_allowed", "message": "That database operation is not allowed."}
        return {"ok": False, "errorCode": "sql_error", "message": str(exc)[:240]}
    finally:
        connection.close()


def main() -> int:
    _limits()
    try:
        raw = sys.stdin.buffer.readline(256 * 1024)
        request = json.loads(raw)
        response = execute(request)
    except Exception:
        response = {"ok": False, "errorCode": "worker_error", "message": "The SQL worker could not process the request."}
    encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > 1024 * 1024:
        encoded = b'{"ok":false,"errorCode":"output_limit","message":"Query output exceeded the limit."}'
    os.write(sys.stdout.fileno(), encoded + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
