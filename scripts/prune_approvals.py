#!/usr/bin/env python3
"""Clear approvals that autopilot should never have raised.

`run_autopilot` used to rank on fit alone, so every run re-queued the same top
roles regardless of what had happened to them since. The gate in
`automation._skip_reason` stops that going forward, but the rows already in the
queue stay pending until something resolves them — 26 items of which 15 were
noise.

Only three categories are touched, each one a fact the candidate has already
established or the system has already recorded:

* the candidate has **already applied** — status is committed
* the posting is **closed** — liveness wrote that onto the application
* the candidate is **not eligible** — location, citizenship, clearance or ITAR

Anything else is left alone. Marked `rejected` rather than deleted, so the row
survives as a record and the decision can be read back.

    ./.venv/bin/python scripts/prune_approvals.py --dry-run
    ./.venv/bin/python scripts/prune_approvals.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.automation import _SkipCounts, _committed_and_closed, _skip_reason  # noqa: E402
from app.discovery import fetch_all_jobs  # noqa: E402
from app.imported import list_imported  # noqa: E402
from app.profile import load_profile  # noqa: E402
from app.store import connect, list_approvals, resolve_approval  # noqa: E402
from app.db import initialize  # noqa: E402


async def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    profile = load_profile()
    # The pool alone is not enough to resolve every approval. `fetch_all_jobs`
    # drops an imported posting when a live one already has the same company
    # and title, so a Leidos "Data Analyst" imported from Dice was invisible
    # here while an INELIGIBLE approval for it sat in the queue. The imported
    # store is the fallback, and it is only a fallback — the live copy wins.
    jobs = {j["id"]: j for j in list_imported()}
    jobs.update({j["id"]: j for j in await fetch_all_jobs()})
    committed, closed = _committed_and_closed()

    pending = [a for a in list_approvals() if a.get("status") == "pending"]
    if not pending:
        print("No pending approvals.")
        return 0

    counts = _SkipCounts()
    stale: list[tuple[dict, str]] = []
    for a in pending:
        job_id = a.get("jobId") or ""
        job = jobs.get(job_id)
        if job is None:
            # Not in the current pool at all. That is only evidence of closure
            # for an API-sourced job, whose board we just read — a locally
            # stored posting is absent for the ordinary reason that it was
            # never in the pool to begin with.
            if job_id in closed or job_id in committed:
                stale.append((a, "already applied" if job_id in committed else "posting closed"))
            continue
        reason = _skip_reason(job, profile, committed, closed, counts)
        if reason:
            stale.append((a, reason))

    for a, reason in sorted(stale, key=lambda t: t[1]):
        print(f"  {reason:<16} {(a.get('companyName') or '?')[:22]:<22} "
              f"{(a.get('jobTitle') or '')[:44]}")

    keep = len(pending) - len(stale)
    print(f"\n  {len(pending)} pending — {len(stale)} stale, {keep} real")

    if args.dry_run:
        print("  Dry run. Nothing written.")
        return 0

    for a, _ in stale:
        resolve_approval(a["id"], "rejected")

    with connect() as conn:
        left = conn.execute(
            "SELECT COUNT(*) c FROM approvals WHERE status='pending'"
        ).fetchone()["c"]
    print(f"  Cleared {len(stale)}. {left} pending remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
