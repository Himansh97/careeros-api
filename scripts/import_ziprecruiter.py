#!/usr/bin/env python3
"""Import ZipRecruiter results, enriching each with its real description.

ZipRecruiter has no public jobs API — the partner API needs approval, and there
is no open board endpoint like Greenhouse or SmartRecruiters. So it cannot be a
server-side source the way the other five are. What exists is an MCP connector
available inside an agent session, which is the same route Indeed and Dice took.

That has a consequence worth stating plainly: these are one-off snapshots, not
a live feed. The daily fetch cannot refresh them, because it has no connector.
They are the postings that need Firecrawl for liveness checking later.

The search results carry no description, only title, company, location and
salary — and a job the scorer cannot read cannot be assessed. So each posting's
page is fetched through Firecrawl before import, and anything that comes back
unreadable is skipped rather than stored with an empty description and given a
meaningless score.

    # in an agent session with the ZipRecruiter connector:
    #   search, then write the results array to a JSON file
    ./.venv/bin/python scripts/import_ziprecruiter.py ziprecruiter.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.imported import import_jobs  # noqa: E402
from app.liveness import FIRECRAWL_URL, firecrawl_key  # noqa: E402
from app.sources import strip_html  # noqa: E402

# Boilerplate that appears on every ZipRecruiter page. Left in, it becomes
# "requirements" the scorer tries to match against.
CHROME = (
    "report this job", "similar jobs", "job seekers", "employers",
    "create alert", "save this job", "sign in", "cookie",
)


def clean(markdown: str) -> str:
    lines = [
        ln for ln in (markdown or "").splitlines()
        if ln.strip() and not any(c in ln.lower() for c in CHROME)
    ]
    return strip_html("\n".join(lines))


async def describe(client: httpx.AsyncClient, url: str, key: str) -> str:
    try:
        r = await client.post(
            FIRECRAWL_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=90,
        )
    except httpx.HTTPError:
        return ""
    if r.status_code != 200:
        return ""
    return clean((r.json().get("data") or {}).get("markdown") or "")


def salary_text(entry: dict) -> str:
    s = entry.get("salary") or {}
    lo, hi = s.get("min_annual"), s.get("max_annual")
    if lo and hi:
        return f"${lo:,} - ${hi:,}"
    return ""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="JSON file: a ZipRecruiter results array")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = firecrawl_key()
    if not key:
        print("FIRECRAWL_API_KEY not set — descriptions cannot be fetched, so "
              "nothing would be scoreable. Aborting.", file=sys.stderr)
        return 1

    raw = json.loads(Path(args.results).read_text())
    entries = raw.get("results", raw) if isinstance(raw, dict) else raw
    print(f"  {len(entries)} ZipRecruiter results to enrich")

    jobs, skipped = [], []
    async with httpx.AsyncClient() as client:
        for e in entries:
            url = e.get("job_redirect_url") or ""
            title = e.get("title", "")
            desc = await describe(client, url, key) if url else ""
            if len(desc) < 400:
                # Too little text to be a real posting — usually a bot wall.
                skipped.append(f"{e.get('company','?')} — {title[:40]} ({len(desc)} chars)")
                continue
            jobs.append({
                "title": title,
                "company": e.get("company", "Unknown"),
                "location": e.get("location", ""),
                "description": desc,
                "applyUrl": url,
                "workArrangement": "remote" if e.get("is_remote") else None,
                "salaryText": salary_text(e),
            })
            print(f"  ok    {e.get('company','?')[:22]:<22} {title[:40]:<40} {len(desc)} chars")

    for s in skipped:
        print(f"  skip  {s}")

    if args.dry_run:
        print(f"\n  {len(jobs)} importable, {len(skipped)} skipped. Nothing written.")
        return 0

    stored = import_jobs(jobs, source="ZipRecruiter (imported)")
    print(f"\n  imported {stored}, skipped {len(skipped)}, "
          f"{len(jobs)} Firecrawl credits used")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
