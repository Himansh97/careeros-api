"""Regenerate stored outreach drafts against the current templates.

Drafts store their rendered text, so a change to `build_outreach` only affects
what is written next — the ones already sitting in the queue keep whatever
wording they were generated with. Thirteen of them had been waiting long enough
to predate a rewrite of the copy entirely.

**Only `drafted` records are touched.** A sent message is a record of something
the candidate actually said to a named person; rewriting it would make the
app's history disagree with their Sent folder, and the follow-up flow reads
these to decide what was already said. Replied ones are likewise left alone.

    ./.venv/bin/python scripts/refresh_outreach_copy.py           # report
    ./.venv/bin/python scripts/refresh_outreach_copy.py --write
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contacts import get_contact  # noqa: E402
from app.discovery import fetch_all_jobs  # noqa: E402
from app.outreach import build_outreach  # noqa: E402
from app.profile import load_profile  # noqa: E402
from app.scoring import score_job_cached  # noqa: E402
from app.store import connect  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    profile = load_profile()
    pool = {j["id"]: j for j in await fetch_all_jobs()}

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, job_id, contact_id, company, status, email_subject, "
            "email_draft, linkedin_draft FROM outreach WHERE status = 'drafted'"
        ).fetchall()

    updates: list[tuple[str, str, str, str, str]] = []
    missing = 0
    for row in rows:
        job = pool.get(row["job_id"])
        if job is None:
            # The posting has left the pool, so the draft cannot be rebuilt
            # against it. Its existing text stays rather than being cleared.
            missing += 1
            continue
        contact = get_contact(row["contact_id"]) if row["contact_id"] else None
        built = build_outreach(job, score_job_cached(job, profile), profile, contact)
        # Subject and LinkedIn note are compared too. Comparing the body alone
        # meant a fix that only touched the subject line reported "0 would
        # change" and left the old subjects in place — which is exactly what a
        # mid-word truncation fix does.
        changed = (
            built["emailDraft"] != row["email_draft"]
            or built["emailSubject"] != row["email_subject"]
            or built["linkedinDraft"] != row["linkedin_draft"]
        )
        if changed:
            updates.append(
                (
                    row["id"],
                    row["company"],
                    built["emailSubject"],
                    built["emailDraft"],
                    built["linkedinDraft"],
                )
            )

    print(f"{len(rows)} drafted outreach records; {len(updates)} would change")
    if missing:
        print(f"  {missing} skipped — posting no longer in the source pool")
    for _, company, subject, _, _ in updates[:6]:
        print(f"    {company[:20]:20} {subject[:56]}")

    if not updates:
        return 0
    if not args.write:
        print("\n  dry run — nothing written. Pass --write to apply.")
        return 0

    with connect() as conn:
        for oid, _, subject, body, linkedin in updates:
            conn.execute(
                "UPDATE outreach SET email_subject=?, email_draft=?, linkedin_draft=? "
                "WHERE id=? AND status='drafted'",
                (subject, body, linkedin, oid),
            )
    print(f"\n  {len(updates)} drafts refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
