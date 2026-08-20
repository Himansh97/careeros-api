"""Structured application blockers and legacy compatibility migration."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .config import DB_PATH
from .db import connect, transaction

_OWNERS = {"candidate", "system", "external"}
_SEVERITIES = {"info", "warning", "blocking"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "applicationId": row["application_id"],
        "kind": row["kind"],
        "owner": row["owner"],
        "severity": row["severity"],
        "state": row["state"],
        "detectedAt": row["detected_at"],
        "resolvedAt": row["resolved_at"],
        "source": row["source"],
        "evidence": json.loads(row["evidence_json"]),
        "summary": row["summary"],
    }


def list_blockers(
    application_id: str, *, include_resolved: bool = True
) -> list[dict[str, Any]]:
    clause = "" if include_resolved else " AND state='open'"
    with connect(read_only=True, path=DB_PATH) as connection:
        rows = connection.execute(
            "SELECT * FROM application_blocker WHERE application_id=?"
            + clause
            + " ORDER BY detected_at, id",
            (application_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def open_blocker(
    application_id: str,
    *,
    kind: str,
    owner: str,
    severity: str,
    source: str,
    evidence: dict[str, Any] | None,
    summary: str,
) -> dict[str, Any]:
    if owner not in _OWNERS:
        raise ValueError(f"Unknown blocker owner: {owner}")
    if severity not in _SEVERITIES:
        raise ValueError(f"Unknown blocker severity: {severity}")
    canonical_evidence = _evidence(evidence)
    identity = json.dumps(
        {
            "applicationId": application_id,
            "kind": kind,
            "source": source,
            "evidence": canonical_evidence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    blocker_id = "blk_" + sha256(identity.encode("utf-8")).hexdigest()[:24]
    with transaction("IMMEDIATE", path=DB_PATH) as connection:
        existing = connection.execute(
            "SELECT * FROM application_blocker WHERE id=?", (blocker_id,)
        ).fetchone()
        if existing:
            return _row(existing)
        connection.execute(
            "INSERT INTO application_blocker "
            "(id, application_id, kind, owner, severity, state, detected_at, "
            "resolved_at, source, evidence_json, summary) "
            "VALUES (?,?,?,?,?,'open',?,NULL,?,?,?)",
            (
                blocker_id,
                application_id,
                kind,
                owner,
                severity,
                _now(),
                source,
                canonical_evidence,
                summary.strip(),
            ),
        )
        return _row(
            connection.execute(
                "SELECT * FROM application_blocker WHERE id=?", (blocker_id,)
            ).fetchone()
        )


def resolve_blocker(blocker_id: str) -> dict[str, Any]:
    with transaction("IMMEDIATE", path=DB_PATH) as connection:
        existing = connection.execute(
            "SELECT * FROM application_blocker WHERE id=?", (blocker_id,)
        ).fetchone()
        if existing is None:
            raise KeyError(blocker_id)
        if existing["state"] != "resolved":
            connection.execute(
                "UPDATE application_blocker "
                "SET state='resolved', resolved_at=? WHERE id=?",
                (_now(), blocker_id),
            )
        return _row(
            connection.execute(
                "SELECT * FROM application_blocker WHERE id=?", (blocker_id,)
            ).fetchone()
        )


def migrate_legacy_next_actions() -> dict[str, int]:
    """Preserve legacy display copy without inferring unknown text as a gate."""
    counts: dict[str, int] = {}
    with connect(read_only=True, path=DB_PATH) as connection:
        rows = connection.execute(
            "SELECT id, next_action FROM applications "
            "WHERE next_action IS NOT NULL AND TRIM(next_action) != '' "
            "ORDER BY id"
        ).fetchall()
    for row in rows:
        text = row["next_action"].strip()
        if text == "Posting closed — no longer accepting":
            kind, owner, severity = "posting_closed", "external", "blocking"
        elif text == "Review and approve":
            kind, owner, severity = "candidate_approval", "candidate", "blocking"
        else:
            kind, owner, severity = "legacy_note", "system", "info"
        before = len(list_blockers(row["id"]))
        open_blocker(
            row["id"],
            kind=kind,
            owner=owner,
            severity=severity,
            source="legacy_next_action",
            evidence={"nextAction": text},
            summary=text,
        )
        after = len(list_blockers(row["id"]))
        if after > before:
            counts[kind] = counts.get(kind, 0) + 1
    return counts
