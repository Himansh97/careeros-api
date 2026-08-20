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
from app.liveness import check_all, firecrawl_key, summarize  # noqa: E402
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
    pool = {j["id"] for j in jobs}
    key = firecrawl_key()

    results = await check_all(apps, pool, key)
    results.sort(key=lambda r: (r["verdict"] != "closed", r["company"]))

    for r in results:
        print(f"{MARK.get(r['verdict'], '  ?        ')} {r['company'][:24]:<24} "
              f"{r['title'][:38]:<38} {r['why']}")

    summary = summarize(results)
    closed = [r for r in results if r["verdict"] == "closed"]
    unchecked = [r for r in results if r["verdict"] == "unverified"]

    if not args.dry_run:
        # Symmetric: closures recorded, and stale closures retracted. Applying
        # only the `closed` half is what let one bad fetch mark an application
        # dead forever.
        from app.liveness_sync import apply_verdicts

        changed = apply_verdicts(results, apps)
        if changed["cleared"]:
            print(f"\n  cleared {changed['cleared']} stale closed flag(s) — "
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
