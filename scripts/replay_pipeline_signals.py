#!/usr/bin/env python3
"""Apply status signals from messages that arrived before anything read them.

    ./.venv/bin/python scripts/replay_pipeline_signals.py [--dry-run]

The classifier has been labelling inbound mail `application confirmation` and
`application_progressed` since the beginning, and nothing acted on the labels.
This walks the messages already on file and advances what they are evidence of.

Safe to re-run. `advance` refuses to move an application backwards and stamps
each date only if it is empty, so a second pass changes nothing.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline_signals import SIGNALS, apply_signal, match_application  # noqa: E402
from app.recruiter_messages import list_messages  # noqa: E402
from app.store import get_application  # noqa: E402
from app.db import initialize  # noqa: E402


def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    messages = list_messages()
    advanced = held = skipped = 0

    for message in messages:
        classification = (message.get("classification") or "").strip().lower()
        if classification not in SIGNALS:
            skipped += 1
            continue

        subject = (message.get("subject") or "")[:52]
        if args.dry_run:
            match = match_application(message)
            app = get_application(match["applicationId"]) if match["applicationId"] else None
            if match["confident"]:
                target = SIGNALS[classification]
                # `company` is nested {id, name} in the serialised application.
                name = (app["company"] or {}).get("name", "?")
                if app["status"] == target:
                    skipped += 1
                    print(f"  ALREADY {target.upper()}  {name} — {subject}")
                    continue
                advanced += 1
                print(f"  WOULD ADVANCE  {subject}")
                print(f"                 {name} — {app['status']} -> {target}")
            else:
                held += 1
                print(f"  HOLD           {subject}")
                print(f"                 {match['why']}")
            continue

        result = apply_signal(message)
        if result.get("advanced"):
            advanced += 1
            app = get_application(result["applicationId"])
            name = (app["company"] or {}).get("name", "?")
            print(f"  ADVANCED  {name} -> {result['status']}   ({subject})")
        else:
            held += 1
            print(f"  HELD      {subject}")
            print(f"            {result.get('reason')}")

    print(f"\n  {len(messages)} messages · {advanced} advanced · {held} held · "
          f"{skipped} carry no status signal")
    if held:
        print("  Held messages are reported, never guessed at — a wrong match moves")
        print("  the wrong application and you would have no reason to look.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
