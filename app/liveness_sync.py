"""Apply source-aware liveness evidence and its application projection atomically."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH
from .db import connect, transaction
from .discovery_store import SourceState
from .store import add_timeline

CLOSED_NOTE = "Posting closed — no longer accepting"
_OURS = ("posting closed", "no longer accepting")
_LOCAL_SOURCE_PREFIXES = ("imported/", "manual/")


@dataclass(frozen=True)
class LivenessEvidence:
    job_id: str
    source_key: str | None
    observation_kind: str
    generation_id: str | None
    source_state: SourceState
    observed_at: str
    detail_code: str


@dataclass(frozen=True)
class LivenessSummary:
    counts: dict[str, int]
    marked: int = 0
    cleared: int = 0
    unchanged: int = 0


def is_closure_note(note: str | None) -> bool:
    low = (note or "").lower()
    return any(marker in low for marker in _OURS)


_is_closure_note = is_closure_note


def _already_recorded(connection, application_id: str, evidence: LivenessEvidence) -> bool:
    if evidence.generation_id is None:
        return False
    return connection.execute(
        "SELECT 1 FROM liveness_event "
        "WHERE application_id=? AND generation_id=? AND evidence_kind=? "
        "AND reason_code=? LIMIT 1",
        (
            application_id,
            evidence.generation_id,
            evidence.observation_kind,
            evidence.detail_code,
        ),
    ).fetchone() is not None


def _decision(connection, application_id: str, evidence: LivenessEvidence) -> tuple[str, str]:
    kind = evidence.observation_kind
    state = SourceState(evidence.source_state)
    if kind == "direct_closed":
        return "closed", evidence.detail_code
    if kind == "direct_live":
        return "live", evidence.detail_code
    if kind == "present":
        if state is SourceState.HEALTHY:
            return "live", "present_in_healthy_generation"
        return "unknown", f"source_{state.value}"
    if kind != "absent":
        return "unknown", evidence.detail_code or "inconclusive"

    source_key = (evidence.source_key or "").strip().lower()
    if not source_key:
        return "unknown", "missing_source_owner"
    if source_key.startswith(_LOCAL_SOURCE_PREFIXES):
        return "unknown", "local_source_pool_absence_ignored"
    if state is not SourceState.HEALTHY:
        return "unknown", evidence.detail_code or f"source_{state.value}"
    if not evidence.generation_id:
        return "unknown", "missing_generation"

    previous = connection.execute(
        "SELECT evidence_kind, generation_id, reason_code FROM liveness_event "
        "WHERE application_id=? AND source_key=? "
        "ORDER BY id DESC LIMIT 1",
        (application_id, source_key),
    ).fetchone()
    if (
        previous
        and previous["evidence_kind"] == "absent"
        and previous["generation_id"] != evidence.generation_id
        and previous["reason_code"] in ("first_healthy_absence", "two_healthy_absences")
    ):
        return "closed", "two_healthy_absences"
    return "unknown", "first_healthy_absence"


def apply_evidence(evidence_items: Iterable[LivenessEvidence]) -> LivenessSummary:
    counts: Counter[str] = Counter()
    marked = 0
    cleared = 0
    unchanged = 0
    with transaction("IMMEDIATE", path=Path(DB_PATH)) as connection:
        for evidence in evidence_items:
            row = connection.execute(
                "SELECT id, next_action, liveness_verdict FROM applications WHERE job_id=?",
                (evidence.job_id,),
            ).fetchone()
            if row is None:
                unchanged += 1
                continue
            application_id = row["id"]
            if _already_recorded(connection, application_id, evidence):
                unchanged += 1
                continue

            verdict, reason_code = _decision(connection, application_id, evidence)
            prior = row["liveness_verdict"]
            existing_note = row["next_action"] or ""
            next_action = existing_note
            if verdict == "closed":
                next_action = CLOSED_NOTE
                if not is_closure_note(existing_note):
                    add_timeline(
                        connection,
                        application_id,
                        "Posting closure confirmed by verified liveness evidence.",
                    )
                    marked += 1
                else:
                    unchanged += 1
            elif verdict == "live":
                if is_closure_note(existing_note):
                    next_action = ""
                    add_timeline(
                        connection,
                        application_id,
                        "Posting is live again — the earlier closure was retracted.",
                    )
                    cleared += 1
                else:
                    unchanged += 1
            else:
                unchanged += 1

            connection.execute(
                "UPDATE applications SET next_action=?, liveness_verdict=?, "
                "liveness_checked_at=?, liveness_reason_code=?, updated_at=? WHERE id=?",
                (
                    next_action,
                    verdict,
                    evidence.observed_at,
                    reason_code,
                    evidence.observed_at,
                    application_id,
                ),
            )
            connection.execute(
                "INSERT INTO liveness_event "
                "(application_id, prior_verdict, new_verdict, evidence_kind, "
                "generation_id, reason_code, observed_at, created_at, source_key) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    application_id,
                    prior,
                    verdict,
                    evidence.observation_kind,
                    evidence.generation_id,
                    reason_code,
                    evidence.observed_at,
                    evidence.observed_at,
                    evidence.source_key,
                ),
            )
            counts[verdict] += 1
    for verdict in ("live", "closed", "unknown"):
        counts.setdefault(verdict, 0)
    return LivenessSummary(dict(counts), marked, cleared, unchanged)


def preview_evidence(evidence_items: Iterable[LivenessEvidence]) -> tuple[dict[str, str], ...]:
    """Evaluate evidence against history without changing projections or events."""
    decisions: list[dict[str, str]] = []
    with connect(read_only=True, path=Path(DB_PATH)) as connection:
        for evidence in evidence_items:
            row = connection.execute(
                "SELECT id FROM applications WHERE job_id=?", (evidence.job_id,)
            ).fetchone()
            if row is None:
                continue
            verdict, reason_code = _decision(connection, row["id"], evidence)
            decisions.append({
                "jobId": evidence.job_id,
                "verdict": verdict,
                "reasonCode": reason_code,
            })
    return tuple(decisions)


def apply_verdicts(
    checks: list[dict[str, Any]], apps: list[dict[str, Any]] | None = None
) -> dict[str, int]:
    """Compatibility adapter for direct URL checks and older callers."""
    del apps
    evidence: list[LivenessEvidence] = []
    for check in checks:
        verdict = check.get("verdict")
        kind = {
            "live": "direct_live",
            "closed": "direct_closed",
        }.get(verdict, "unknown")
        evidence.append(
            LivenessEvidence(
                job_id=check.get("jobId") or "",
                source_key=f"legacy/{check.get('method') or 'unknown'}",
                observation_kind=kind,
                generation_id=None,
                source_state=SourceState.HEALTHY,
                observed_at=check.get("observedAt") or check.get("at") or "1970-01-01T00:00:00+00:00",
                detail_code=f"legacy_{verdict or 'unknown'}",
            )
        )
    summary = apply_evidence(evidence)
    return {"marked": summary.marked, "cleared": summary.cleared}
