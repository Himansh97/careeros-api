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


# Where an agent session leaves the Gmail it captured. Outside the repos, since
# a mailbox snapshot is personal data and must never be a candidate for `git add`.
GMAIL_SNAPSHOT_DIR = Path.home() / "careeros" / "gmail-snapshot"

# A snapshot older than this describes a mailbox that has moved on. Reconciling
# against it would mark stale conclusions with today's date.
SNAPSHOT_MAX_AGE_HOURS = 36


def _reconcile_gmail() -> None:
    sent = GMAIL_SNAPSHOT_DIR / "sent.json"
    inbox = GMAIL_SNAPSHOT_DIR / "inbox.json"

    if not sent.exists():
        log(
            "gmail: no snapshot to reconcile — run scripts/check_replies.py from a "
            f"session with the Gmail connector, or drop sent.json in {GMAIL_SNAPSHOT_DIR}"
        )
        return

    age_hours = (datetime.now(timezone.utc).timestamp() - sent.stat().st_mtime) / 3600
    if age_hours > SNAPSHOT_MAX_AGE_HOURS:
        log(
            f"gmail: snapshot is {age_hours / 24:.1f} days old — skipping rather than "
            "reconciling against a mailbox that has moved on"
        )
        return

    cmd = [sys.executable, str(ROOT / "scripts" / "check_replies.py"), "--sent", str(sent)]
    if inbox.exists():
        cmd += ["--inbox", str(inbox)]
    try:
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"gmail reconcile failed: {exc}")
        return

    if done.returncode != 0:
        log(f"gmail reconcile exited {done.returncode}: {done.stderr.strip()[:200]}")
        return

    summary = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    log(f"gmail: reconciled a snapshot {age_hours:.0f}h old")
    for line in summary[-4:]:
        log(f"  {line}")


async def main() -> int:
    from app.db import initialize
    from app.automation import run_autopilot
    from app.discovery import failed_sources, fetch_all_jobs, filter_jobs

    initialize()
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

    # Check staged postings before tailoring more: a role that closed overnight
    # should be flagged rather than quietly kept at the top of the queue.
    try:
        from app.liveness import check_all, firecrawl_key
        from app.store import add_timeline, connect, list_applications, now as _now

        apps = list_applications()
        checks = await check_all(apps, {j["id"] for j in jobs}, firecrawl_key())
        from app.liveness_sync import apply_verdicts

        closed = [c for c in checks if c["verdict"] == "closed"]
        changed = apply_verdicts(checks, apps)
        unverified = sum(1 for c in checks if c["verdict"] == "unverified")
        log(f"liveness: {len(closed)} closed ({changed['marked']} newly), "
            f"{changed['cleared']} stale flags cleared, {unverified} unverifiable"
            + (f" — {', '.join(c['company'] for c in closed)}" if closed else ""))
    except Exception as exc:  # noqa: BLE001 - never let this stop the run
        log(f"liveness check failed: {type(exc).__name__}: {exc}")

    result = await run_autopilot()
    if result.get("error"):
        log(f"autopilot did not run: {result['error']}")
    else:
        log(
            "autopilot: "
            + ", ".join(f"{k}={v}" for k, v in result.items() if isinstance(v, int))
        )

    # Reconcile against Gmail, if a session has left a snapshot to reconcile.
    #
    # This step cannot fetch its own mail. launchd runs with no Gmail
    # credentials and the API deliberately holds none, so the only thing that
    # can read the mailbox is an agent session with the connector — the same
    # constraint the recruiter monitor lives with. A session drops a snapshot
    # at GMAIL_SNAPSHOT_DIR; this consumes it and says plainly when it is old,
    # because a stale reconciliation that looks fresh is worse than none.
    _reconcile_gmail()

    # Alerts need no Gmail at all: outreach never sent, replies approved and
    # forgotten, applications already known to be blocked. Logged every run so
    # the log alone shows what is outstanding.
    try:
        from app.alerts import build_alerts

        alerts = build_alerts()
        high = [a for a in alerts if a["severity"] == "high"]
        log(f"alerts: {len(alerts)} outstanding, {len(high)} urgent")
        for a in high:
            log(f"  URGENT {a['title']} — {a['action']}")
    except Exception as exc:  # noqa: BLE001 - never let this stop the run
        log(f"alerts failed: {type(exc).__name__}: {exc}")

    # Mirror the pipeline to Notion, if it is configured. One way: CareerOS
    # owns every column this writes, and the mirror exists for the things a
    # localhost app cannot do — reaching a phone, surviving the disk, being
    # shown to another person. It is off unless NOTION_TOKEN is set, and off is
    # a normal state rather than a degraded one.
    try:
        from app.notion import available as notion_available, sync as notion_sync
        from app.store import list_applications as _list_applications

        ready, why = notion_available()
        if ready:
            report = await notion_sync(_list_applications())
            log(f"notion mirror: {report.summary}")
        else:
            log(f"notion mirror skipped — {why}")
    except Exception as exc:  # noqa: BLE001 - never let this stop the run
        log(f"notion mirror failed: {type(exc).__name__}: {exc}")

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
