"""Autopilot: a real batch run over the live pipeline.

This is genuinely the same code paths the UI uses one job at a time —
discover, score, tailor, queue for approval — just run over a batch and
recorded so the UI can show what happened.

What it deliberately does not do is submit anything or send anything. Every
run ends with items in the approval queue. That boundary is the whole point:
automation does the research and the drafting, a human does the consequential
part.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from dataclasses import dataclass, field

from .discovery import fetch_all_jobs, filter_jobs
from .eligibility import check_eligibility
from .profile import load_profile
from .scoring import score_job
from .store import add_approval, connect, now, upsert_application, set_resume_score
from .tailor import tailor_resume

# Statuses that mean the candidate has already acted on a job. Mirrors
# `store._COMMITTED_STATUSES`, which exists for the same reason: re-running the
# pipeline must never quietly undo something the candidate did by hand.
_COMMITTED_STATUSES = ("applied", "interviewing", "offer", "rejected", "withdrawn")


@dataclass
class _SkipCounts:
    """Why jobs were held back, so a run can say so rather than look idle."""

    already_applied: int = 0
    posting_closed: int = 0
    not_eligible: int = 0
    _order: tuple[str, ...] = field(
        default=("already_applied", "posting_closed", "not_eligible"), repr=False
    )

    @property
    def total(self) -> int:
        return self.already_applied + self.posting_closed + self.not_eligible

    def describe(self) -> str:
        labels = {
            "already_applied": "already applied",
            "posting_closed": "posting closed",
            "not_eligible": "not eligible",
        }
        parts = [f"{getattr(self, k)} {labels[k]}" for k in self._order if getattr(self, k)]
        return ", ".join(parts)


def _committed_and_closed() -> tuple[set[str], set[str]]:
    """Job ids the candidate has acted on, and ones known to be closed.

    One query each, read before the tailoring loop — the alternative is a
    lookup per job inside a loop that already scores 150 of them.
    """
    with connect() as conn:
        committed = {
            r["job_id"]
            for r in conn.execute(
                "SELECT job_id FROM applications WHERE status IN "
                f"({','.join('?' * len(_COMMITTED_STATUSES))})",
                _COMMITTED_STATUSES,
            )
        }
        closed = {
            r["job_id"]
            for r in conn.execute(
                "SELECT job_id FROM applications WHERE next_action LIKE '%closed%'"
            )
        }
    return committed, closed


def _skip_reason(
    job: dict[str, Any],
    profile: Any,
    committed: set[str],
    closed: set[str],
    counts: _SkipCounts,
) -> str | None:
    """Return why this job should not be queued, or None to proceed."""
    job_id = job["id"]
    if job_id in committed:
        counts.already_applied += 1
        return "already applied"
    if job_id in closed:
        counts.posting_closed += 1
        return "posting closed"
    # REVIEW_REQUIRED is held back too. It means the posting says something the
    # candidate may not satisfy, and autopilot is not the place to resolve that
    # — it belongs in a deliberate look at the job, not a batch run.
    if check_eligibility(job, profile).get("verdict") != "ELIGIBLE":
        counts.not_eligible += 1
        return "not eligible"
    return None

AUTOMATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    stats TEXT,
    nodes TEXT
);
CREATE TABLE IF NOT EXISTS automation_rules (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL
);
"""

DEFAULT_RULES: dict[str, Any] = {
    "minimumFitToTailor": 75,
    "minimumResumeScore": 90,
    "maxApplicationsPerDay": 10,
    "submissionMode": "approval",
    "emailMode": "approval",
    "jobRecencyDays": 7,
    "autoRejectBelowFit": 55,
    "recruiterConfidenceMinimum": 70,
    "followUpDelayBusinessDays": 6,
    "targetQueries": ["data analyst", "business analyst", "analytics engineer"],
}

NODE_ORDER = [
    ("discover", "Discover"),
    ("deduplicate", "Deduplicate"),
    ("analyze", "Analyze"),
    ("score", "Score"),
    ("tailor", "Tailor"),
    ("audit", "Audit"),
    ("application", "Application"),
    ("approval", "Approval"),
    ("submit", "Submit"),
    ("outreach", "Outreach"),
    ("followup", "Follow-up"),
]


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(AUTOMATION_SCHEMA)


def get_rules() -> dict[str, Any]:
    with connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT payload FROM automation_rules WHERE id=1").fetchone()
    if not row:
        return dict(DEFAULT_RULES)
    stored = json.loads(row["payload"])
    return {**DEFAULT_RULES, **stored}


def save_rules(rules: dict[str, Any]) -> dict[str, Any]:
    merged = {**get_rules(), **{k: v for k, v in rules.items() if v is not None}}
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR REPLACE INTO automation_rules (id, payload) VALUES (1, ?)",
            (json.dumps(merged),),
        )
    return merged


def _nodes(states: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": label,
            "state": states.get(key, ("queued", ""))[0],
            "detail": states.get(key, ("queued", ""))[1] or None,
        }
        for key, label in NODE_ORDER
    ]


def latest_run() -> dict[str, Any] | None:
    with connect() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM automation_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "status": row["status"],
        "stats": json.loads(row["stats"]) if row["stats"] else {},
        "nodes": json.loads(row["nodes"]) if row["nodes"] else _nodes({}),
    }


_running = False


def is_running() -> bool:
    return _running


async def run_autopilot(max_tailor: int | None = None) -> dict[str, Any]:
    """Discover → score → tailor the strongest matches → queue for approval."""
    global _running
    if _running:
        return {"error": "already_running"}

    _running = True
    rules = get_rules()
    started = now()
    states: dict[str, tuple[str, str]] = {}

    with connect() as conn:
        _ensure(conn)
        cur = conn.execute(
            "INSERT INTO automation_runs (started_at, status, nodes) VALUES (?,?,?)",
            (started, "running", json.dumps(_nodes(states))),
        )
        run_id = cur.lastrowid

    def mark(key: str, state: str, detail: str = "") -> None:
        states[key] = (state, detail)
        with connect() as c:
            _ensure(c)
            c.execute(
                "UPDATE automation_runs SET nodes=? WHERE id=?",
                (json.dumps(_nodes(states)), run_id),
            )

    stats: dict[str, Any] = {}
    try:
        profile = load_profile()

        mark("discover", "running", "Fetching live postings")
        all_jobs = await fetch_all_jobs()
        stats["discovered"] = len(all_jobs)
        mark("discover", "complete", f"{len(all_jobs)} postings across all sources")

        mark("deduplicate", "complete", "Deduped on company + title during fetch")

        mark("analyze", "running", "Matching against target queries")
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for q in rules["targetQueries"]:
            for j in filter_jobs(all_jobs, query=q):
                if j["id"] not in seen:
                    seen.add(j["id"])
                    candidates.append(j)
        stats["analyzed"] = len(candidates)
        mark("analyze", "complete", f"{len(candidates)} matched target roles")

        committed_job_ids, closed_job_ids = _committed_and_closed()

        mark("score", "running", f"Scoring {len(candidates)} roles")
        scored = []
        for j in candidates[:150]:  # bound the work so a run stays responsive
            s = score_job(j, profile)
            if s["rawFitScore"] >= rules["autoRejectBelowFit"]:
                scored.append((s, j))
        scored.sort(key=lambda t: -t[0]["rawFitScore"])
        stats["qualified"] = len(scored)
        mark("score", "complete", f"{len(scored)} above the auto-reject threshold")

        limit = max_tailor if max_tailor is not None else rules["maxApplicationsPerDay"]
        eligible_enough = [
            (s, j) for s, j in scored if s["rawFitScore"] >= rules["minimumFitToTailor"]
        ]

        # Fit is not the only thing that decides whether a job is worth queuing.
        # Ranking on score alone meant every run re-raised the same top roles —
        # 10 of 26 pending approvals were jobs already applied to, 3 were
        # postings liveness had already found closed, and 3 the candidate is
        # not eligible for. An approval queue that is mostly noise is one the
        # candidate learns to skim, which defeats the point of having a gate.
        skipped = _SkipCounts()
        to_tailor = [
            (s, j) for s, j in eligible_enough
            if not _skip_reason(j, profile, committed_job_ids, closed_job_ids, skipped)
        ][:limit]

        if skipped.total:
            mark("score", "complete",
                 f"{len(scored)} above threshold, {skipped.total} set aside "
                 f"({skipped.describe()})")

        mark("tailor", "running", f"Tailoring {len(to_tailor)} resumes")
        tailored = 0
        queued = 0
        for s, j in to_tailor:
            resume = tailor_resume(j, s, profile)
            record = upsert_application(j, s)
            if record:
                set_resume_score(record["id"], resume["resumeScore"])
                add_approval(
                    "application",
                    j["id"],
                    {
                        "jobTitle": j["title"],
                        "companyName": j["company"]["name"],
                        "title": "Ready to apply",
                        "whatCareerOSWantsToDo": (
                            f"A tailored resume (score {resume['resumeScore']}) is ready "
                            f"for {j['title']} at {j['company']['name']}. Open the posting "
                            f"to apply — CareerOS does not submit on your behalf."
                        ),
                        "whyApprovalRequired": (
                            "CareerOS never submits an application. It prepares "
                            "everything and stops here so you review and apply yourself."
                        ),
                        "rawFitScore": s["rawFitScore"],
                        "resumeScore": resume["resumeScore"],
                        "applyUrl": j.get("applyUrl"),
                    },
                )
                queued += 1
            tailored += 1
            await asyncio.sleep(0)  # yield so the event loop stays responsive

        stats["tailored"] = tailored
        stats["queuedForApproval"] = queued
        mark("tailor", "complete", f"{tailored} resumes generated from real evidence")
        mark("audit", "complete", f"{tailored} recruiter audits scored")
        mark("application", "complete", f"{queued} applications prepared")
        mark(
            "approval",
            "blocked",
            f"{queued} awaiting your review — nothing submits automatically",
        )
        mark("submit", "queued", "Requires your approval first")
        mark(
            "outreach",
            "queued",
            "Run per-job from the Recruiter tab (each lookup spends a provider credit)",
        )
        mark("followup", "idle", "Starts once outreach has actually been sent")

        finished = now()
        with connect() as conn:
            _ensure(conn)
            conn.execute(
                "UPDATE automation_runs SET finished_at=?, status=?, stats=?, nodes=? WHERE id=?",
                (finished, "complete", json.dumps(stats), json.dumps(_nodes(states)), run_id),
            )
        return {"runId": run_id, "status": "complete", "stats": stats, "nodes": _nodes(states)}

    except Exception as exc:  # noqa: BLE001 - surface the failure, don't hide it
        mark("discover", "failed", str(exc)[:160])
        with connect() as conn:
            _ensure(conn)
            conn.execute(
                "UPDATE automation_runs SET finished_at=?, status=?, stats=? WHERE id=?",
                (now(), "failed", json.dumps({"error": str(exc)[:300]}), run_id),
            )
        return {"runId": run_id, "status": "failed", "error": str(exc)[:300]}
    finally:
        _running = False
