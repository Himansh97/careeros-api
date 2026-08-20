#!/usr/bin/env python3
"""Reconstruct application timestamps that were never recorded.

`submitted_at` was returned as a hardcoded `None` for the life of the table, so
nothing captured when an application actually went out. The column exists now,
but the history in front of it is blank — and every conversion or timing
question needs that history to mean anything.

Two sources, and the difference between them matters:

* **Observed** — a timeline entry saying the candidate submitted. That is a
  record of the event itself.
* **Inferred** — the outreach email's `sent_at`. Outreach and application went
  out in the same sitting, so the date is close, but it is a *different event*.
  Treating it as observed would put a guess into the training data for the
  learning features this backfill exists to enable.

Rows filled from the second source are flagged `timestamps_inferred`, and the
API reports that flag, so a reconstructed date can never later be mistaken for
a measured one.

    ./.venv/bin/python scripts/backfill_outcomes.py --dry-run
    ./.venv/bin/python scripts/backfill_outcomes.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store import connect  # noqa: E402
from app.db import initialize  # noqa: E402

# Timeline wording that records a real submission.
SUBMIT_LABELS = ("application submitted", "submitted by candidate", "moved to submitted")

# Statuses that mean the employer answered.
RESPONDED = ("recruiter_contacted", "screening", "interview", "offer", "rejected")


def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    observed: list[tuple[str, str, str]] = []
    inferred: list[tuple[str, str, str]] = []
    unknown: list[tuple[str, str]] = []

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, job_id, company, title, status, submitted_at FROM applications"
        ).fetchall()

        for row in rows:
            if row["submitted_at"]:
                continue
            # Only applications that actually went out have a submit date. A
            # 'ready' application has not been sent, and inventing a date for it
            # would manufacture a funnel that never happened.
            if row["status"] not in ("applied", "submitted", *RESPONDED):
                continue

            event = conn.execute(
                "SELECT at, label FROM timeline WHERE application_id=? "
                "ORDER BY id",
                (row["id"],),
            ).fetchall()
            hit = next(
                (e for e in event if any(m in e["label"].lower() for m in SUBMIT_LABELS)),
                None,
            )
            if hit:
                observed.append((row["id"], hit["at"], f"{row['company']} — {row['title'][:34]}"))
                continue

            sent = conn.execute(
                "SELECT sent_at FROM outreach WHERE job_id=? AND sent_at IS NOT NULL "
                "ORDER BY sent_at LIMIT 1",
                (row["job_id"],),
            ).fetchone()
            if sent:
                inferred.append((row["id"], sent["sent_at"], f"{row['company']} — {row['title'][:34]}"))
            else:
                unknown.append((row["id"], f"{row['company']} — {row['title'][:34]}"))

    for _, at, label in observed:
        print(f"  observed   {at[:10]}  {label}")
    for _, at, label in inferred:
        print(f"  inferred   {at[:10]}  {label}")
    for _, label in unknown:
        print(f"  UNKNOWN    ----------  {label}  (marked applied, no evidence of when)")

    print(
        f"\n  {len(observed)} observed, {len(inferred)} inferred, "
        f"{len(unknown)} left blank"
    )

    if args.dry_run:
        print("  Dry run. Nothing written.")
        return 0

    with connect() as conn:
        for app_id, at, _ in observed:
            conn.execute(
                "UPDATE applications SET submitted_at=? WHERE id=?", (at, app_id)
            )
        for app_id, at, _ in inferred:
            conn.execute(
                "UPDATE applications SET submitted_at=?, timestamps_inferred=1 WHERE id=?",
                (at, app_id),
            )

    print(f"  Wrote {len(observed) + len(inferred)}. "
          f"{len(inferred)} flagged as reconstructed, not measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
