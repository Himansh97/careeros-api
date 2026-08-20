"""Recompute every stored resume score against the current audit.

Stored scores are written at tailor time and never revisited. That is fine
while the scoring is stable and wrong the moment it changes — and it changed:
five of the eight audit categories stopped being constants, which moved the
observed median from 90 to 81 without a single document getting worse.

The consequence was silent. The Resumes list, the approval board's RESUME
station and the `ready` status all read the stored number, so they were
reporting a pre-rebase scale while newly tailored resumes used the new one.
Two scales in the same list, with no way to tell which was which.

It also reconciles `status`, which was written by a rule that no longer holds.
`set_resume_score` used to mark every un-sent application "ready" whatever the
score was, so eight applications scoring 50-79 sat in the list labelled Ready
with "Review and approve" beside them. That is now gated on `READY_SCORE`, but
the rows already written keep the old label until something reconciles them.

Dry-run by default. `set_resume_score` is used for the score write, so an
application that has already been sent does not get rewound to "ready" — a score
refresh is not evidence that an application was un-sent. The status-only pass
writes directly, with a timeline line that says what actually happened rather
than claiming a re-tailor that did not occur.

    ./.venv/bin/python scripts/rescore_resumes.py            # report
    ./.venv/bin/python scripts/rescore_resumes.py --write
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import READY_SCORE  # noqa: E402
from app.discovery import fetch_all_jobs  # noqa: E402
from app.profile import load_profile  # noqa: E402
from app.scoring import score_job_cached  # noqa: E402
from app.store import (  # noqa: E402
    _COMMITTED_STATUSES,
    add_timeline,
    connect,
    list_applications,
    now,
    set_resume_score,
)
from app.tailor import tailor_resume  # noqa: E402
from app.db import initialize  # noqa: E402


def reconcile_status(write: bool) -> int:
    """Bring `status` back in line with the score that decides it.

    Only un-sent applications are touched. A sent application's status records
    something the candidate did, and no amount of rescoring un-sends it.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, company, title, status, resume_score FROM applications "
            "WHERE resume_score IS NOT NULL"
        ).fetchall()

    stale = [
        r
        for r in rows
        if (r["status"] or "") not in _COMMITTED_STATUSES
        and (r["status"] or "") in ("ready", "draft")
        and (r["status"] == "ready") != ((r["resume_score"] or 0) >= READY_SCORE)
    ]
    if not stale:
        print("every un-sent application's status already matches its score")
        return 0

    print(f"\n{len(stale)} application(s) labelled against the old readiness rule:")
    for r in stale:
        want = "ready" if (r["resume_score"] or 0) >= READY_SCORE else "draft"
        print(
            f"  {r['resume_score']:3}  {(r['company'] or '')[:22]:22} "
            f"{r['status']} -> {want}"
        )

    if not write:
        return 0

    with connect() as conn:
        for r in stale:
            score = r["resume_score"] or 0
            ready = score >= READY_SCORE
            conn.execute(
                "UPDATE applications SET status=?, next_action=?, updated_at=? WHERE id=?",
                (
                    "ready" if ready else "draft",
                    "Review and approve"
                    if ready
                    else f"Strengthen the resume — {READY_SCORE - score} short of {READY_SCORE}",
                    now(),
                    r["id"],
                ),
            )
            add_timeline(
                conn,
                r["id"],
                f"Status corrected to {'ready' if ready else 'draft'} — "
                f"resume score {score} against a readiness bar of {READY_SCORE}",
            )
    print(f"  {len(stale)} statuses corrected")
    return len(stale)


async def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    profile = load_profile()
    pool = {j["id"]: j for j in await fetch_all_jobs()}

    changes: list[tuple[str, str, int, int]] = []
    skipped_missing = 0

    for record in list_applications():
        job = pool.get(record.get("jobId"))
        if job is None:
            # The posting has left the pool, so it cannot be re-tailored. Its
            # stored score stays as the last thing that was actually measured.
            skipped_missing += 1
            continue
        stored = record.get("resumeScore")
        fresh = tailor_resume(job, score_job_cached(job, profile), profile)["resumeScore"]
        if stored != fresh:
            changes.append((record["id"], (record.get("company") or {}).get("name", ""),
                            stored or 0, fresh))

    if not changes:
        print("every stored score already matches the current audit")
        reconcile_status(args.write)
        return 0

    olds = [c[2] for c in changes]
    news = [c[3] for c in changes]
    print(f"{len(changes)} of {len(list_applications())} applications would change")
    print(f"  stored median {statistics.median(olds):.0f} -> {statistics.median(news):.0f}")
    print(f"  stored >=90   {sum(1 for x in olds if x >= 90)} -> {sum(1 for x in news if x >= 90)}")
    if skipped_missing:
        print(f"  {skipped_missing} skipped — posting no longer in the source pool")
    print()
    for _, company, old, new in sorted(changes, key=lambda c: c[2] - c[3], reverse=True)[:12]:
        print(f"  {company[:24]:24} {old:3} -> {new:3}  ({new - old:+d})")

    if not args.write:
        reconcile_status(False)
        print("\n  dry run — nothing written. Pass --write to apply.")
        return 0

    for app_id, _, _, new in changes:
        set_resume_score(app_id, new)
    print(f"\n  {len(changes)} scores updated")
    reconcile_status(True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
