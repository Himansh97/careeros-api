#!/usr/bin/env python3
"""Confirm every configured job board still answers.

    ./.venv/bin/python scripts/check_boards.py

A board that has moved does not fail loudly. It returns 404, the crawler records
one dead source among eighty, and downstream it is indistinguishable from an
employer with no openings — so nothing looks wrong and the roles simply stop
arriving.

That is what happened to Anthropic. Its Ashby board 404'd every morning for
long enough to be scrolled past in the log while 491 open roles stayed
invisible; the company had moved to Greenhouse. Discovery cannot tell the
difference between "no jobs" and "no board", so something has to ask directly.

Exits non-zero when any board is unreachable, so it can gate a scheduled run.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import GREENHOUSE_COMPANIES  # noqa: E402
from app.sources import ASHBY_COMPANIES  # noqa: E402

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


async def check(client: httpx.AsyncClient, kind: str, slug: str) -> tuple[str, str, str, int]:
    try:
        r = await client.get(URLS[kind].format(slug=slug))
    except httpx.HTTPError as exc:
        return kind, slug, type(exc).__name__, 0
    if r.status_code != 200:
        return kind, slug, f"HTTP {r.status_code}", 0
    try:
        count = len(r.json().get("jobs") or [])
    except ValueError:
        return kind, slug, "unparseable", 0
    # An empty board is reported too. It is usually a hiring freeze rather than
    # a fault, but it is also exactly what a moved board looks like from here.
    return kind, slug, "ok" if count else "empty", count


async def main() -> int:
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        results = await asyncio.gather(
            *[check(client, "greenhouse", s) for s in GREENHOUSE_COMPANIES],
            *[check(client, "ashby", s) for s in ASHBY_COMPANIES],
        )

    broken = [r for r in results if r[2] not in ("ok", "empty")]
    empty = [r for r in results if r[2] == "empty"]
    total = sum(r[3] for r in results)

    for kind, slug, why, _ in sorted(broken):
        print(f"  BROKEN  {kind:11} {slug:24} {why}")
    for kind, slug, _, _ in sorted(empty):
        print(f"  empty   {kind:11} {slug:24} no openings, or the board moved")

    print(f"\n  {len(results)} boards · {len(broken)} broken · {len(empty)} empty "
          f"· {total:,} roles reachable")
    if broken:
        print("  A broken board is invisible downstream — it reads as an employer")
        print("  with no openings. Find where the company posts now.")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
