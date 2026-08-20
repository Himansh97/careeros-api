"""Attachments on an outgoing draft, and the rule that a body cannot lie.

Shared by recruiter replies and outreach because they made the same mistake for
the same reason: neither had anywhere to record an attachment, so a message
saying "resume attached" arrived with nothing attached and no review step could
have caught it. One definition, imported twice — two copies of a rule like this
drift, and the one that drifts is the one nobody is looking at.

Paths, not bytes. Packet PDFs are regenerated whenever a resume is retailored,
so bytes copied in at draft time quietly become the stale version. Files are read
when the draft is claimed for sending, and a file that has since disappeared
blocks the send rather than silently dropping the attachment.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
import sqlite3
from typing import Any

# Phrases that promise an attachment. If the body makes the promise and nothing
# is attached, approval fails — the same shape as figure binding in the resume
# gate: a claim has to be backed by the thing it refers to.
ATTACHMENT_CLAIMS = re.compile(
    r"\b(resume|cv|c\.v\.)\s+(is\s+)?(attached|enclosed)\b"
    r"|\battach(ed|ing)\s+(is\s+)?(my|the|a)?\s*(resume|cv)\b"
    r"|\bplease\s+(find|see)\s+(the\s+)?attach",
    re.IGNORECASE,
)

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def normalize_attachments(value: Any) -> list[str]:
    """Absolute, de-duplicated paths. Existence is checked at approval.

    A draft may name a resume that has not been generated yet — the packet is
    often rebuilt between drafting and sending. What is not allowed is
    *approving* one whose file is missing.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        resolved = str(pathlib.Path(text).expanduser())
        if resolved not in out:
            out.append(resolved)
    return out


def stored_attachments(row: sqlite3.Row, column: str = "attachment_paths") -> list[str]:
    try:
        raw = row[column]
    except (IndexError, KeyError):
        return []
    return json.loads(raw) if raw else []


def validate(body: str, attachments: list[str]) -> None:
    """Raise if the body promises an attachment it does not have.

    Deliberately not a requirement that every message carry a resume — most
    should not. It only refuses the specific inconsistency of saying one is
    attached when none is.
    """
    if ATTACHMENT_CLAIMS.search(body or "") and not attachments:
        raise ValueError(
            "This draft says a resume is attached but has none. Attach the file, "
            "or reword the body so it does not promise one."
        )
    missing = [p for p in attachments if not pathlib.Path(p).is_file()]
    if missing:
        raise ValueError("Attachment file is missing: " + ", ".join(missing))


def read(paths: list[str]) -> list[dict[str, Any]]:
    """Base64 payloads shaped for the Gmail draft call.

    Read here, at send time, rather than stored at draft time — a resume
    retailored in between should go out in its current form, and a file that has
    vanished should stop the send instead of quietly producing a message with no
    attachment.
    """
    payloads = []
    for path in paths:
        file = pathlib.Path(path)
        if not file.is_file():
            raise ValueError(f"Attachment file is missing: {path}")
        payloads.append({
            "filename": file.name,
            "mimeType": MIME_BY_SUFFIX.get(file.suffix.lower(), "application/octet-stream"),
            "content": base64.b64encode(file.read_bytes()).decode("ascii"),
        })
    return payloads
