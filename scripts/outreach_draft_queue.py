#!/usr/bin/env python3
"""Hand approved outreach to an agent session that can create Gmail drafts.

    echo '{"action":"claim"}' | ./.venv/bin/python scripts/outreach_draft_queue.py

CareerOS holds no Gmail credentials and deliberately never will, so the last
hop -- putting a draft in the mailbox -- belongs to an agent session with the
connector. This is the seam: `claim` returns one approved outreach with its
attachments already base64-encoded and ready to pass straight to Gmail, so
nobody encodes a PDF by hand. `created` records the Gmail draft id.

Nothing here sends. The candidate reads the draft in Gmail and presses send.

Mirrors scripts/recruiter_message_queue.py, which does the same for replies.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import outreach_store  # noqa: E402

_ERROR = "Request could not be processed."


def _result(command: dict) -> object:
    action = command.get("action")
    if action == "claim":
        return outreach_store.claim_approved_outreach()
    if action == "created":
        draft_id = str(command.get("gmailDraftId") or "").strip()
        if not draft_id:
            raise ValueError("Missing Gmail draft ID")
        return outreach_store.mark_outreach_draft_created(
            command.get("outreachId", ""), draft_id
        )
    if action == "failed":
        return outreach_store.mark_outreach_failed(
            command.get("outreachId", ""), command.get("message", "")
        )
    if action == "pending":
        return [
            o for o in outreach_store.list_outreach()
            if o.get("draftStatus") in ("approved", "creating")
        ]
    raise ValueError("Unknown action")


def handle(command: dict) -> dict:
    """Execute one command without leaking request data or exception detail."""
    if not isinstance(command, dict):
        return {"ok": False, "result": None, "error": _ERROR}
    try:
        return {"ok": True, "result": _result(command), "error": None}
    except Exception:
        return {"ok": False, "result": None, "error": _ERROR}


def main() -> int:
    raw = sys.stdin.read().strip()
    try:
        command = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "result": None, "error": _ERROR}))
        return 1
    print(json.dumps(handle(command), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
