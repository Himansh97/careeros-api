"""Durable, source-aware job discovery snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import uuid
from typing import Any

from .config import DB_PATH
from .db import connect, transaction


class SourceState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SourceResult:
    source_key: str
    state: SourceState
    jobs: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class SourceGeneration:
    id: str
    source_key: str
    state: SourceState
    job_count: int
    error_code: str | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class SourceHealth:
    source_key: str
    state: SourceState
    generation_id: str
    job_count: int
    error_code: str | None
    stale: bool


@dataclass(frozen=True)
class DiscoverySnapshot:
    jobs: tuple[dict[str, Any], ...]
    sources: tuple[SourceHealth, ...]
    generated_at: str | None


_PUBLIC_JOB_FIELDS = (
    "id",
    "title",
    "location",
    "workArrangement",
    "source",
    "atsPlatform",
    "postedAt",
    "description",
    "applyUrl",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Return the fixed public posting shape used for persistence and hashing."""
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    normalized = {field: job.get(field) for field in _PUBLIC_JOB_FIELDS}
    normalized["company"] = {
        "id": company.get("id"),
        "name": company.get("name"),
    }
    return normalized


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_job_payload(job: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(normalize_job_payload(job)).encode("utf-8")
    ).hexdigest()


def record_generation(result: SourceResult) -> SourceGeneration:
    source_key = result.source_key.strip().lower()
    if not source_key:
        raise ValueError("source_key is required")
    state = SourceState(result.state)
    error_code = (result.error_code or "").strip().lower() or None
    if state is SourceState.HEALTHY and error_code is not None:
        raise ValueError("healthy source generations cannot carry an error code")
    if state is not SourceState.HEALTHY and error_code is None:
        raise ValueError("non-healthy source generations require a stable error code")
    if state is not SourceState.HEALTHY and result.jobs:
        raise ValueError("failed source generations cannot contain fresh observations")

    started_at = _now()
    finished_at = _now()
    generation_id = f"gen_{uuid.uuid4().hex}"
    normalized_jobs = tuple(normalize_job_payload(item) for item in result.jobs)
    with transaction("IMMEDIATE", path=Path(DB_PATH)) as connection:
        connection.execute(
            "INSERT INTO source_generation "
            "(id, source_key, started_at, finished_at, state, job_count, error_code, error_summary) "
            "VALUES (?,?,?,?,?,?,?,NULL)",
            (
                generation_id,
                source_key,
                started_at,
                finished_at,
                state.value,
                len(normalized_jobs),
                error_code,
            ),
        )
        for payload in normalized_jobs:
            payload_json = _canonical_json(payload)
            connection.execute(
                "INSERT INTO job_observation "
                "(generation_id, source_key, job_id, observed_at, payload_json, payload_hash) "
                "VALUES (?,?,?,?,?,?)",
                (
                    generation_id,
                    source_key,
                    payload["id"],
                    finished_at,
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                ),
            )
    return SourceGeneration(
        generation_id,
        source_key,
        state,
        len(normalized_jobs),
        error_code,
        started_at,
        finished_at,
    )


def _observations(
    connection, generation_id: str, source_key: str, *, stale: bool
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT payload_json FROM job_observation "
        "WHERE generation_id=? ORDER BY job_id",
        (generation_id,),
    ).fetchall()
    return [
        {
            **json.loads(row["payload_json"]),
            "stale": stale,
            "sourceKey": source_key,
            "sourceGenerationId": generation_id,
        }
        for row in rows
    ]


def current_snapshot() -> DiscoverySnapshot:
    with connect(read_only=True, path=Path(DB_PATH)) as connection:
        latest_rows = connection.execute(
            "SELECT * FROM source_generation AS generation "
            "WHERE rowid = ("
            "SELECT candidate.rowid FROM source_generation AS candidate "
            "WHERE candidate.source_key=generation.source_key "
            "ORDER BY candidate.finished_at DESC, candidate.rowid DESC LIMIT 1"
            ") ORDER BY source_key"
        ).fetchall()
        jobs: list[dict[str, Any]] = []
        sources: list[SourceHealth] = []
        generated_at: str | None = None
        for latest in latest_rows:
            generated_at = max(generated_at or latest["finished_at"], latest["finished_at"])
            state = SourceState(latest["state"])
            selected = latest
            stale = state is not SourceState.HEALTHY
            if stale:
                selected = connection.execute(
                    "SELECT * FROM source_generation "
                    "WHERE source_key=? AND state='healthy' "
                    "ORDER BY finished_at DESC, rowid DESC LIMIT 1",
                    (latest["source_key"],),
                ).fetchone()
            if selected is not None:
                jobs.extend(
                    _observations(
                        connection,
                        selected["id"],
                        latest["source_key"],
                        stale=stale,
                    )
                )
            sources.append(
                SourceHealth(
                    source_key=latest["source_key"],
                    state=state,
                    generation_id=latest["id"],
                    job_count=int(latest["job_count"]),
                    error_code=latest["error_code"],
                    stale=stale,
                )
            )
    return DiscoverySnapshot(tuple(jobs), tuple(sources), generated_at)


def source_owner(job_id: str) -> str | None:
    """Return the adapter that has most recently observed a posting."""
    with connect(read_only=True, path=Path(DB_PATH)) as connection:
        row = connection.execute(
            "SELECT source_key FROM job_observation WHERE job_id=? "
            "ORDER BY observed_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return row["source_key"] if row else None


def prune_generations(*, keep: int = 30) -> int:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    deleted = 0
    with transaction("IMMEDIATE", path=Path(DB_PATH)) as connection:
        protected = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT generation_id FROM liveness_event "
                "WHERE generation_id IS NOT NULL"
            )
        }
        source_keys = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_key FROM source_generation"
            )
        ]
        for source_key in source_keys:
            rows = connection.execute(
                "SELECT id FROM source_generation WHERE source_key=? "
                "ORDER BY finished_at DESC, rowid DESC",
                (source_key,),
            ).fetchall()
            for row in rows[keep:]:
                generation_id = row["id"]
                if generation_id in protected:
                    continue
                connection.execute(
                    "DELETE FROM job_observation WHERE generation_id=?",
                    (generation_id,),
                )
                connection.execute(
                    "DELETE FROM source_generation WHERE id=?",
                    (generation_id,),
                )
                deleted += 1
    return deleted
