#!/usr/bin/env python3
"""JSON-lines bridge between the heartbeat and recruiter draft queue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import recruiter_messages


_ERROR = "Request could not be processed."


def _stale_creating() -> list[dict[str, Any]]:
    """Return creating drafts old enough for Gmail reconciliation, unchanged."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with recruiter_messages._connection() as conn:
        rows = conn.execute(
            "SELECT gmail_message_id FROM recruiter_reply_drafts "
            "WHERE status='creating' AND updated_at < ? ORDER BY updated_at, id",
            (cutoff,),
        ).fetchall()
        return [
            recruiter_messages._row_to_message(
                conn, recruiter_messages._message_or_raise(conn, row["gmail_message_id"])
            )
            for row in rows
        ]


def _gmail_draft_id(value: Any) -> str:
    if not isinstance(value, str) or not (draft_id := value.strip()):
        raise ValueError("Missing Gmail draft ID")
    return draft_id


def _result(command: dict[str, Any]) -> Any:
    action = command.get("action")
    if action == "upsert":
        payload = command.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Missing payload")
        return recruiter_messages.upsert_message(payload)
    if action == "claim":
        return recruiter_messages.claim_approved_draft()
    if action == "created":
        return recruiter_messages.mark_draft_created(
            command.get("gmailMessageId", ""), _gmail_draft_id(command.get("gmailDraftId"))
        )
    if action == "failed":
        return recruiter_messages.mark_draft_failed(
            command.get("gmailMessageId", ""), command.get("code", ""), command.get("message", "")
        )
    if action == "requeue_stale":
        return _stale_creating()
    raise ValueError("Unknown action")


def handle(command: dict) -> dict:
    """Execute one command without exposing request data or exception details."""
    if not isinstance(command, dict):
        return {"ok": False, "result": None, "error": _ERROR}
    try:
        return {"ok": True, "result": _result(command), "error": None}
    except Exception:
        return {"ok": False, "result": None, "error": _ERROR}


def main() -> None:
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except (TypeError, ValueError):
            response = {"ok": False, "result": None, "error": _ERROR}
        else:
            response = handle(command)
        try:
            print(json.dumps(response, separators=(",", ":")), flush=True)
        except Exception:
            print('{"ok":false,"result":null,"error":"Request could not be processed."}', flush=True)


if __name__ == "__main__":
    main()
