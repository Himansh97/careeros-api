"""Restore applications that were rewound after being sent.

`_COMMITTED_STATUSES` — the guard that stops a re-tailor walking a sent
application backwards — listed "applied" and "interviewing", two spellings
nothing in the system writes. The status the "Mark applied" button actually
produces is "submitted", and it was not in the list. So every autopilot
re-tailor of a submitted application quietly reset it to ready or draft, and it
reappeared in the apply queue asking to be sent a second time.

The guard is fixed. This repairs the records it already let through.

A record is only restored when the send is evidenced two independent ways:
`submitted_at` is set, and the timeline carries an explicit "Moved to
Submitted" entry the candidate's own click wrote. `timestamps_inferred` rows
are skipped — a guessed timestamp is not proof of anything, and inventing a
send is a worse failure than leaving one to be re-marked by hand.

    ./.venv/bin/python scripts/repair_rewound_sends.py           # report
    ./.venv/bin/python scripts/repair_rewound_sends.py --write
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.store import _COMMITTED_STATUSES, add_timeline, connect, now  # noqa: E402

# What a rewound record looks like: somewhere at or before "sent" in the
# pipeline, despite carrying proof that it was sent.
_UNSENT = ("qualified", "tailoring", "draft", "ready", "applying")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, company, title, status, submitted_at, timestamps_inferred "
            "FROM applications WHERE submitted_at IS NOT NULL AND status IN "
            f"({','.join('?' * len(_UNSENT))})",
            _UNSENT,
        ).fetchall()

        repairable = []
        skipped_inferred = []
        for row in rows:
            if row["timestamps_inferred"]:
                skipped_inferred.append(row)
                continue
            proof = conn.execute(
                "SELECT COUNT(*) AS n FROM timeline WHERE application_id=? "
                "AND label = 'Moved to Submitted'",
                (row["id"],),
            ).fetchone()["n"]
            if proof:
                repairable.append(row)
            else:
                skipped_inferred.append(row)

    if not repairable and not skipped_inferred:
        print("no rewound sends found")
        return 0

    print(f"{len(repairable)} application(s) sent, then rewound:")
    for row in repairable:
        print(
            f"  {row['status']:9} -> submitted   {(row['company'] or '')[:20]:20} "
            f"sent {str(row['submitted_at'])[:16]}  {(row['title'] or '')[:36]}"
        )
    for row in skipped_inferred:
        print(
            f"  SKIPPED {(row['company'] or '')[:20]:20} "
            "— submitted_at is inferred or unbacked by a timeline entry"
        )

    if not args.write:
        print("\n  dry run — nothing written. Pass --write to apply.")
        return 0

    with connect() as conn:
        for row in repairable:
            conn.execute(
                "UPDATE applications SET status=?, next_action=?, updated_at=? WHERE id=?",
                ("submitted", "", now(), row["id"]),
            )
            add_timeline(
                conn,
                row["id"],
                f"Restored to submitted — sent {str(row['submitted_at'])[:16]}, "
                f"rewound to {row['status']} by a re-tailor",
            )
    print(f"\n  {len(repairable)} restored")

    assert "submitted" in _COMMITTED_STATUSES, (
        "the guard still omits 'submitted' — these records will be rewound again"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
