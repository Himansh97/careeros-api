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
from app.liveness import check_all, firecrawl_key  # noqa: E402
from app.store import add_timeline, connect, list_applications, now  # noqa: E402

MARK = {"live": "  live     ", "closed": "  CLOSED   ",
        "unknown": "  unknown  ", "unverified": "  unchecked"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    apps = list_applications()
    if not apps:
        print("No staged applications.")
        return 0

    jobs = await fetch_all_jobs()
    pool = {j["id"] for j in jobs}
    key = firecrawl_key()

    results = await check_all(apps, pool, key)
    results.sort(key=lambda r: (r["verdict"] != "closed", r["company"]))

    for r in results:
        print(f"{MARK.get(r['verdict'], '  ?        ')} {r['company'][:24]:<24} "
              f"{r['title'][:38]:<38} {r['why']}")

    closed = [r for r in results if r["verdict"] == "closed"]
    unchecked = [r for r in results if r["verdict"] == "unverified"]

    if closed and not args.dry_run:
        # Recorded on the application rather than acted on. A closed posting is
        # information for the candidate, not grounds for the system to change
        # an application's state by itself.
        with connect() as conn:
            for r in closed:
                app_id = f"app_{r['jobId']}"
                conn.execute(
                    "UPDATE applications SET next_action=?, updated_at=? WHERE id=?",
                    ("Posting closed — no longer accepting", now(), app_id),
                )
                add_timeline(conn, app_id, f"Posting no longer live: {r['why']}")

    print(f"\n  {len(results)} checked — {len(closed)} closed, "
          f"{sum(1 for r in results if r['verdict'] == 'live')} live, "
          f"{len(unchecked)} unverifiable")

    if unchecked and not key:
        print("\n  Set FIRECRAWL_API_KEY in careeros-api/.env to check the rest.")
        print("  Free tier is 1,000 credits/month; this uses about "
              f"{len(unchecked)} per run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
