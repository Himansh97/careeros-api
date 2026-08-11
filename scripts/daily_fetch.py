#!/usr/bin/env python3
"""Daily discovery run, for a scheduler rather than a person.

Nothing in CareerOS was ever fetched on a timer: `fetch_all_jobs()` is lazy and
cached for fifteen minutes, so the pipeline only moved when someone opened the
app. A SoFi posting expired while staged and nothing noticed, because nothing
was looking.

This runs the same autopilot the UI triggers — discover, score, tailor the
strongest matches, queue for approval — and then refreshes the state snapshot.
It talks to the app directly rather than over HTTP, so it does not need the
API server to be running.

It still submits nothing and sends nothing. The run ends with items in the
approval queue, exactly as the interactive path does.

    ./.venv/bin/python scripts/daily_fetch.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG = Path.home() / "Library" / "Logs" / "careeros-daily.log"


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


async def main() -> int:
    from app.automation import run_autopilot
    from app.discovery import failed_sources, fetch_all_jobs, filter_jobs

    log("--- daily fetch starting ---")

    # force=True: the point of a scheduled run is fresh data, and a 15-minute
    # cache from yesterday would otherwise satisfy it silently.
    jobs = await fetch_all_jobs(force=True)
    us = filter_jobs(jobs)
    log(f"fetched {len(jobs)} jobs, {len(us)} US-based")

    failures = failed_sources()
    if failures:
        # A source that errors returns nothing, which looks identical to a quiet
        # day on that board. Say so rather than let it read as no news.
        log(f"WARNING sources failed: {', '.join(failures)}")

    result = await run_autopilot()
    if result.get("error"):
        log(f"autopilot did not run: {result['error']}")
    else:
        log(
            "autopilot: "
            + ", ".join(f"{k}={v}" for k, v in result.items() if isinstance(v, int))
        )

    # Refresh the handoff snapshot so the next session — human or agent — opens
    # a file that reflects this run rather than the last manual one.
    snapshot = ROOT.parent / "careeros" / "scripts" / "snapshot_state.py"
    if snapshot.exists():
        try:
            subprocess.run(
                ["python3", str(snapshot)], cwd=snapshot.parent.parent,
                capture_output=True, text=True, timeout=300,
            )
            log("state snapshot refreshed")
        except (subprocess.SubprocessError, OSError) as exc:
            log(f"snapshot failed: {exc}")

    log("--- daily fetch done ---")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - a scheduled job must log, not vanish
        log(f"FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
