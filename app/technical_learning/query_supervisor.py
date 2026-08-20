from __future__ import annotations

import json
import subprocess
import sys

from .datasets import ensure_dataset
from .models import QueryResult
from .sql_policy import guard_sql


def _parse_response(raw: bytes) -> QueryResult:
    if len(raw) > 1024 * 1024:
        return QueryResult(ok=False, error_code="output_limit", message="Query output exceeded the limit.")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return QueryResult(ok=False, error_code="worker_error", message="The SQL worker returned an invalid response.")
    return QueryResult(
        ok=bool(value.get("ok")),
        columns=value.get("columns") or [],
        rows=value.get("rows") or [],
        row_count=int(value.get("rowCount") or 0),
        truncated=bool(value.get("truncated")),
        error_code=value.get("errorCode"),
        message=value.get("message"),
    )


def run_sql(
    dataset_id: str,
    dataset_version: str,
    sql: str,
    *,
    timeout_s: float = 3.0,
    row_limit: int = 200,
    instruction_limit: int = 2_000_000,
) -> QueryResult:
    statement = guard_sql(sql)
    path = ensure_dataset(dataset_id, dataset_version)
    request = json.dumps(
        {
            "datasetPath": str(path.resolve()),
            "sql": statement,
            "rowLimit": row_limit,
            "instructionLimit": instruction_limit,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "app.technical_learning.sql_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=pathlib_root(),
    )
    try:
        stdout, _ = process.communicate(request + b"\n", timeout=max(0.001, timeout_s))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return QueryResult(ok=False, error_code="timeout", message="Query exceeded the wall-clock limit.")
    if process.returncode != 0:
        return QueryResult(ok=False, error_code="worker_error", message="The SQL worker stopped unexpectedly.")
    return _parse_response(stdout)


def pathlib_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2])
