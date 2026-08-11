#!/usr/bin/env python3
"""Mark recruiter replies that actually went out.

CareerOS tracked the workflow only as far as an unsent Gmail draft. Whether
the candidate then sent it was invisible, so a reply sent on 11 Aug still read
as `approved` in the app days later — and a follow-up clock that starts at
"sent" cannot start at all.

The API holds no Gmail credentials by design, so it cannot look. This takes the
Sent folder captured by an agent session and reconciles it, the same way
`import_ziprecruiter.py` takes search results it cannot fetch itself.

    # in an agent session with the Gmail connector:
    #   search_threads("in:sent newer_than:30d"), write the threads to JSON
    ./.venv/bin/python scripts/reconcile_sent.py sent.json --dry-run
    ./.venv/bin/python scripts/reconcile_sent.py sent.json

Matching is deliberately conservative. A recipient address must match exactly;
subject wording is only a tiebreak, because the candidate edits subjects before
sending — the GitLab reply went out as "Senior Revenue Analytics Analyst —
Himanshu Srivastava" against a draft titled "... — AI in the loop". Anything
unmatched is reported, never guessed at: wrongly marking a reply as sent would
stop a follow-up that is genuinely still owed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recruiter_messages import list_messages, mark_draft_sent  # noqa: E402

_WORD = re.compile(r"[a-z0-9]+")
_NOISE = frozenset({"re", "fwd", "the", "a", "an", "for", "and", "at", "of", "to"})


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _NOISE}


def addresses(entry: dict) -> set[str]:
    out: set[str] = set()
    for key in ("toRecipients", "ccRecipients", "bccRecipients"):
        for a in entry.get(key) or []:
            m = re.search(r"[\w.+-]+@[\w.-]+", a or "")
            if m:
                out.add(m.group(0).lower())
    return out


def flatten(payload) -> list[dict]:
    """Accept either a raw thread list or the search_threads response shape."""
    threads = payload.get("threads", payload) if isinstance(payload, dict) else payload
    sent = []
    for t in threads:
        for m in t.get("messages", [t]):
            if "SENT" in (m.get("labelIds") or []):
                sent.append(m)
    return sent


def best_match(draft: dict, sent: list[dict]) -> tuple[dict | None, str]:
    want = {a.lower() for a in (draft.get("to") or [])}
    if not want:
        return None, "draft has no recipient"

    candidates = [m for m in sent if want & addresses(m)]
    if not candidates:
        return None, f"nothing sent to {', '.join(sorted(want))}"

    subject = tokens(draft.get("subject", ""))
    scored = sorted(
        candidates,
        key=lambda m: (len(subject & tokens(m.get("subject", ""))), m.get("date", "")),
        reverse=True,
    )
    top = scored[0]
    overlap = len(subject & tokens(top.get("subject", "")))
    return top, f"to {', '.join(sorted(want & addresses(top)))}, {overlap} subject words in common"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sent", help="JSON: Gmail search_threads output for in:sent")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sent = flatten(json.loads(Path(args.sent).read_text()))
    print(f"  {len(sent)} sent messages to match against")

    updated = skipped = 0
    for message in list_messages():
        draft = message.get("draft") or {}
        msg_id = message.get("gmailMessageId") or draft.get("gmailMessageId")
        if not msg_id:
            continue
        label = f"{(draft.get('subject') or '')[:44]}"
        if draft.get("sentAt"):
            print(f"  already sent   {label}")
            continue
        if draft.get("status") == "dismissed":
            print(f"  dismissed      {label}")
            continue

        match, why = best_match(draft, sent)
        if not match:
            print(f"  NOT SENT       {label} — {why}")
            skipped += 1
            continue

        print(f"  sent           {label}")
        print(f"                 {why}, on {match.get('date')}")
        if not args.dry_run:
            mark_draft_sent(msg_id, match["id"], match.get("date"))
        updated += 1

    verb = "would mark" if args.dry_run else "marked"
    print(f"\n  {verb} {updated} sent, {skipped} still outstanding")
    if args.dry_run:
        print("  Dry run. Nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
