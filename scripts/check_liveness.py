#!/usr/bin/env python3
"""Check whether staged postings are still open, and record what changed.

    ./.venv/bin/python scripts/check_liveness.py
    ./.venv/bin/python scripts/check_liveness.py --dry-run

Free for API-sourced jobs. Locally-stored ones (Indeed, Dice, pasted links)
need Firecrawl, and are reported as unverified when no key is set rather than
silently assumed live.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.discovery import fetch_all_jobs  # noqa: E402
from app.discovery_store import current_snapshot  # noqa: E402
from app.liveness import check_all, evidence_from_snapshot, firecrawl_key, summarize  # noqa: E402
from app.liveness_sync import apply_evidence, preview_evidence  # noqa: E402
from app.store import add_timeline, connect, list_applications, now  # noqa: E402
from app.db import initialize  # noqa: E402

MARK = {"live": "  live     ", "closed": "  CLOSED   ",
        "unknown": "  unknown  ", "unverified": "  unchecked"}


async def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    apps = list_applications()
    if not apps:
        print("No staged applications.")
        return 0

    jobs = await fetch_all_jobs()
    del jobs
    key = firecrawl_key()

    snapshot = current_snapshot()
    initial = evidence_from_snapshot(apps, snapshot)
    direct_ids = {item.job_id for item in initial if item.source_key == "manual/direct"}
    direct_apps = [a for a in apps if a.get("jobId") in direct_ids]
    direct = await check_all(direct_apps, set(), key, direct_only=True)
    evidence = evidence_from_snapshot(apps, snapshot, direct)
    decisions = preview_evidence(evidence)
    apps_by_job = {app.get("jobId"): app for app in apps}
    results = [
        {
            **decision,
            "company": (apps_by_job[decision["jobId"]].get("company") or "?"),
            "title": apps_by_job[decision["jobId"]].get("title") or "",
            "why": decision["reasonCode"],
        }
        for decision in decisions
    ]
    results.sort(key=lambda r: (r["verdict"] != "closed", r["company"]))

    summary = summarize(results)
    if args.dry_run:
        print(
            f"liveness dry run: {summary['counts'].get('live', 0)} live, "
            f"{summary['counts'].get('closed', 0)} closed, "
            f"{summary['undetermined']} unknown"
        )
        for reason, count in sorted(summary["reasons"].items()):
            print(f"  {count:>3}  {reason}")
        return 0

    for r in results:
        print(f"{MARK.get(r['verdict'], '  ?        ')} {r['company'][:24]:<24} "
              f"{r['title'][:38]:<38} {r['why']}")

    unchecked = [r for r in results if r["reasonCode"] == "missing_firecrawl_key"]

    changed = apply_evidence(evidence)
    if changed.cleared:
        print(f"\n  cleared {changed.cleared} stale closed flag(s) — "
              "those postings are live again")

    print(f"\n  {summary['total']} checked — {summary['counts'].get('closed', 0)} closed, "
          f"{summary['counts'].get('live', 0)} live, "
          f"{summary['undetermined']} undetermined")

    # Undetermined is not live. Saying so, with the reason, is the difference
    # between "everything is fine" and "I could not reach twelve of these".
    if summary["undetermined"]:
        print("\n  Undetermined — these were NOT confirmed open or closed:")
        for why, n in sorted(summary["reasons"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}  {why}")
        if summary["rate_limited"]:
            print(f"\n  {summary['rate_limited']} of those were rate-limited (HTTP 429).")
            print("  That is transient — re-run later and they will resolve.")

    if unchecked and not key:
        print("\n  Set FIRECRAWL_API_KEY in careeros-api/.env to check the rest.")
        print("  Free tier is 1,000 credits/month; this uses about "
              f"{len(unchecked)} per run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
