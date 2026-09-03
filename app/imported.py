"""Externally-imported job postings.

Some sources can't be called from a server: Indeed retired its public
Publisher API in 2024 and now gates access behind a sales-led partner
program, and LinkedIn's terms prohibit automated access outright.

Rather than pretend those sources don't exist or scrape them, this module
accepts postings gathered through a legitimate client-side channel (an
assistant session with Indeed access, a browser extension, a manual paste)
and treats them exactly like any other job: scored against real evidence,
tailorable, trackable.

Imported jobs are marked with their origin so the UI can be honest about
the fact that they were not fetched live.
"""
from __future__ import annotations

import json
from typing import Any

from .store import connect, now

def import_jobs(jobs: list[dict[str, Any]], source: str, live: bool = False) -> int:
    """Persist externally-sourced postings. Returns how many were stored.

    `company` is accepted either flat (a string, as a manual paste supplies) or
    already nested as `{"id", "name"}`, which is the shape `sources._job()`
    produces. A pasted Greenhouse link arrives in the second form and used to
    crash here on `.lower()`.

    `live=True` marks a posting that really was fetched from an ATS's own API —
    a pasted board link is not the same thing as a hand-typed description, and
    flagging both `importedNotLive` would misreport the honest one.
    """
    stored = 0
    with connect() as conn:
        for j in jobs:
            raw_company = j.get("company")
            if isinstance(raw_company, dict):
                company = {
                    "id": raw_company.get("id") or "unknown",
                    "name": raw_company.get("name") or "Unknown",
                }
            else:
                company = {
                    "id": (raw_company or "unknown").lower().replace(" ", "-"),
                    "name": raw_company or "Unknown",
                }
            job = {
                "id": j.get("id") or f"imported_{source}_{stored}",
                "title": j.get("title", "").strip(),
                "company": company,
                "location": j.get("location") or "Not specified",
                "workArrangement": j.get("workArrangement") or "onsite",
                "source": source,
                "atsPlatform": j.get("atsPlatform"),
                "postedAt": j.get("postedAt"),
                "discoveredAt": now(),
                "description": j.get("description") or "",
                "applyUrl": j.get("applyUrl") or "",
                "salaryText": j.get("salaryText"),
                "importedNotLive": not live,
            }
            conn.execute(
                "INSERT OR REPLACE INTO imported_jobs (id, payload, source, imported_at) VALUES (?,?,?,?)",
                (job["id"], json.dumps(job), source, now()),
            )
            stored += 1
    return stored


def _is_job(payload: dict[str, Any]) -> bool:
    """Whether a stored row is actually a job posting.

    28 of 61 rows in this table were not. They carry `applicationId`,
    `jobTitle`, `companyName`, `status` and `errorCode` -- the shape of a Tsenta
    tracked-application record, written here by something that treated this
    table as general storage. Discovery reads every row as a job and indexes it
    by `payload["company"]["name"]`, so each refresh died on KeyError: 'company'
    and the job pool silently stopped updating.

    Filtering at the read is deliberate rather than deleting the rows: they are
    real records of real applications and belong somewhere, just not in a list
    the discovery pipeline treats as postings.
    """
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("company"), dict)
        and bool(payload["company"].get("name"))
        and bool(payload.get("title"))
    )


def list_imported() -> list[dict[str, Any]]:
    """Every pasted posting. Rows that are not postings are skipped."""
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM imported_jobs").fetchall()
    return [p for p in (json.loads(r["payload"]) for r in rows) if _is_job(p)]


def non_job_rows() -> list[dict[str, Any]]:
    """Stored rows that are not postings, so the mess is visible rather than
    quietly filtered forever."""
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM imported_jobs").fetchall()
    return [p for p in (json.loads(r["payload"]) for r in rows) if not _is_job(p)]


def imported_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) n FROM imported_jobs GROUP BY source"
        ).fetchall()
    return {r["source"]: r["n"] for r in rows}
