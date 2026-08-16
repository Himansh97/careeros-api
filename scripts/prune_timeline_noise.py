"""Collapse repeated "Resume tailored" entries that record no change.

`set_resume_score` logged every call, and autopilot re-tailors on every pass —
landing on the same score nearly every time. The result was a timeline that is
85% "Resume tailored", with 1158 of those 1330 entries repeating the number
immediately above them. One application carried twenty identical lines at
score 83, which is how a real event (a send, a closure, a score that actually
moved) becomes impossible to find.

`set_resume_score` now only writes on a change. This prunes what it already
wrote.

Only *consecutive* repeats are removed, and the first of each run is always
kept — that entry is the moment the score genuinely reached that value, and it
is the one with the honest timestamp. A score that went 83 -> 71 -> 83 keeps
all three; nothing that records a change is touched, and no other label is
considered.

    ./.venv/bin/python scripts/prune_timeline_noise.py           # report
    ./.venv/bin/python scripts/prune_timeline_noise.py --write
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.store import connect  # noqa: E402

_PREFIX = "Resume tailored"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    with connect() as conn:
        rows = conn.execute(
            "SELECT rowid AS rid, application_id, label FROM timeline "
            "ORDER BY application_id, rowid"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM timeline").fetchone()["n"]

    doomed: list[int] = []
    previous: tuple[str, str] | None = None
    for row in rows:
        key = (row["application_id"], row["label"])
        # Only consecutive identical tailoring lines. Anything else resets the
        # run, so an interleaved real event always protects the entry after it.
        if row["label"].startswith(_PREFIX) and key == previous:
            doomed.append(row["rid"])
        previous = key

    if not doomed:
        print(f"nothing to prune — {total} timeline rows, none repeating")
        return 0

    print(f"{total} timeline rows; {len(doomed)} repeat the entry above them")
    print(f"  {total - len(doomed)} would remain ({100 * len(doomed) // total}% removed)")

    with connect() as conn:
        worst = conn.execute(
            "SELECT application_id AS a, label, COUNT(*) AS n FROM timeline "
            "WHERE label LIKE ? GROUP BY application_id, label "
            "HAVING n > 2 ORDER BY n DESC LIMIT 5",
            (_PREFIX + "%",),
        ).fetchall()
    if worst:
        print("\n  worst offenders:")
        for row in worst:
            print(f"    {row['n']:3} x  {row['a'][:34]:34} {row['label']}")

    if not args.write:
        print("\n  dry run — nothing written. Pass --write to apply.")
        return 0

    with connect() as conn:
        conn.executemany("DELETE FROM timeline WHERE rowid=?", [(r,) for r in doomed])
        left = conn.execute("SELECT COUNT(*) AS n FROM timeline").fetchone()["n"]
    print(f"\n  {len(doomed)} removed, {left} rows remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
