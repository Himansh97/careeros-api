"""A one-way mirror of the application pipeline into Notion.

**Notion is a publishing target here, never a database.** Nothing in this module
reads pipeline state back, and nothing anywhere else may start: the moment two
systems can both write a status, they disagree, and the disagreement is silent.
This codebase has already paid that bill once. `_COMMITTED_STATUSES` said
"applied" while the UI wrote "submitted" — two vocabularies for one concept
inside a single repo — and six applications the candidate had actually sent were
quietly rewound and offered back to them to send again. A second *system* with
its own opinion of what "applied" means is that failure with a network in the
middle of it.

So the contract is deliberately lopsided:

* CareerOS owns every field this module writes. Editing them in Notion changes
  nothing here, and the mirror says so on every page it creates.
* Notion owns anything the candidate types in a property this module does not
  touch. `NOTES_PROPERTY` is left alone on update for exactly that reason.

What the mirror is actually for is the small set of things a localhost app
cannot do: reaching a phone, surviving the disk, and being shown to another
human. It is not a better view of the pipeline — the app is that.

Absent credentials mean the mirror does not run, never that something breaks.
`sync()` returns a report either way and raises nothing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import (
    HTTP_TIMEOUT_SECONDS,
    NOTION_MAX_RPS,
    NOTION_VERSION,
    notion_data_source_id,
    notion_token,
)

logger = logging.getLogger(__name__)

_API = "https://api.notion.com/v1"

# The property carrying the CareerOS application id. This is what makes the
# sync idempotent: rows are matched on it rather than on company and title,
# which are not unique — the candidate has four live Target applications and
# two at SoFi, and matching on a title would collapse or duplicate them.
KEY_PROPERTY = "CareerOS ID"

# Left alone once a row exists. This is the candidate's column, and the one
# place the lopsided contract runs the other way.
NOTES_PROPERTY = "Notes"

# Notion's cap on a single rich-text value. Longer strings are rejected by the
# API rather than truncated by it, so anything free-form is cut here first.
_RICH_TEXT_MAX = 2000


@dataclass
class SyncReport:
    """What a run did, in terms that can be printed or returned over HTTP."""

    ok: bool
    reason: str = ""
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"mirror not run — {self.reason}"
        parts = [
            f"{self.created} created",
            f"{self.updated} updated",
            f"{self.unchanged} unchanged",
        ]
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


def available() -> tuple[bool, str]:
    """Whether a sync would run, and why not when it would not."""
    if not notion_token():
        return False, "no NOTION_TOKEN configured"
    if not notion_data_source_id():
        return False, "no NOTION_DATA_SOURCE_ID configured"
    return True, ""


def _text(value: str | None, limit: int = _RICH_TEXT_MAX) -> list[dict[str, Any]]:
    """A rich-text value, clipped to what the API will accept.

    An empty list is a legitimately empty property. Notion rejects an
    over-length string outright rather than trimming it, so a long next-action
    note would fail the whole page write rather than arrive shortened.
    """
    text = (value or "").strip()
    if not text:
        return []
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return [{"type": "text", "text": {"content": text}}]


def _properties(record: dict[str, Any]) -> dict[str, Any]:
    """The CareerOS-owned columns for one application.

    `NOTES_PROPERTY` is absent by construction — see the module docstring.
    Adding it here would make every sync overwrite whatever the candidate wrote
    on their phone, which is the one thing the mirror must never do.
    """
    company = (record.get("company") or {}).get("name") or ""
    props: dict[str, Any] = {
        "Name": {"title": _text(record.get("title") or "Untitled role")},
        KEY_PROPERTY: {"rich_text": _text(record.get("id"))},
        "Company": {"rich_text": _text(company)},
        "Status": {"select": {"name": (record.get("status") or "unknown")}},
        "Next action": {"rich_text": _text(record.get("nextAction"))},
    }

    # Numbers are sent only when present. `"number": None` is how Notion clears
    # a value, so defaulting a missing score to 0 would publish a confident
    # wrong figure where "not scored yet" is the truth.
    for prop, key in (("Fit", "rawFitScore"), ("Resume", "resumeScore")):
        value = record.get(key)
        props[prop] = {"number": value if isinstance(value, (int, float)) else None}

    url = record.get("applyUrl") or None
    props["Apply URL"] = {"url": url if url else None}

    submitted = record.get("submittedAt")
    props["Submitted"] = {"date": {"start": submitted} if submitted else None}

    return props


class _Client:
    """A thin Notion client that is structurally incapable of reading rows.

    There is no query or retrieve method beyond the one the sync needs to find
    its own rows by key. That is not minimalism for its own sake — a read path
    is how "mirror" turns into "second source of truth" one convenient helper
    at a time.
    """

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        # Notion allows ~3 requests/second. One in flight at a time with a
        # small delay stays well under it and keeps ordering predictable, which
        # matters more than speed for ~40 rows.
        self._gap = 1.0 / max(1, NOTION_MAX_RPS)

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, payload: dict | None = None
    ) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                resp = await client.request(
                    method, f"{_API}{path}", headers=self._headers, json=payload
                )
            except httpx.HTTPError as exc:
                logger.warning("notion %s %s failed: %s", method, path, exc)
                return None

            if resp.status_code == 429:
                # Honour Retry-After when Notion sends one; it knows the
                # workspace-wide budget, which this process cannot see.
                wait = float(resp.headers.get("Retry-After", "1") or 1)
                await asyncio.sleep(min(wait, 10) * (attempt + 1))
                continue
            if resp.status_code >= 400:
                logger.warning(
                    "notion %s %s -> %s %s", method, path, resp.status_code, resp.text[:200]
                )
                return None
            await asyncio.sleep(self._gap)
            return resp.json()
        return None

    async def existing_rows(
        self, client: httpx.AsyncClient, data_source_id: str
    ) -> dict[str, str]:
        """Map CareerOS application id -> Notion page id, for rows we created.

        The only read in the module, and it reads nothing but the key: enough
        to update a row instead of duplicating it, and not enough to be tempted
        into treating Notion as an input.
        """
        found: dict[str, str] = {}
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            data = await self._request(
                client, "POST", f"/data_sources/{data_source_id}/query", payload
            )
            if not data:
                break
            for page in data.get("results", []):
                prop = (page.get("properties") or {}).get(KEY_PROPERTY) or {}
                parts = prop.get("rich_text") or []
                key = "".join(p.get("plain_text", "") for p in parts).strip()
                if key:
                    found[key] = page["id"]
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return found

    async def create(
        self, client: httpx.AsyncClient, data_source_id: str, props: dict[str, Any]
    ) -> bool:
        # Since 2025-09-03 a page's parent is a data source, not a database.
        body = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": props,
            "children": _provenance_blocks(),
        }
        return await self._request(client, "POST", "/pages", body) is not None

    async def update(
        self, client: httpx.AsyncClient, page_id: str, props: dict[str, Any]
    ) -> bool:
        return (
            await self._request(client, "PATCH", f"/pages/{page_id}", {"properties": props})
            is not None
        )


def _provenance_blocks() -> list[dict[str, Any]]:
    """Said once, on the page itself, where someone reading it will see it.

    A mirror that does not announce itself is a trap: the columns look
    editable, editing them appears to work, and the next sync silently reverts
    the edit with no indication that it ever existed.
    """
    return [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": _text(
                    "Generated by CareerOS. Every field except Notes is "
                    "overwritten on each sync — edit them in the app, not here. "
                    "Notes is yours and is never touched."
                ),
                "icon": {"type": "emoji", "emoji": "\U0001f501"},
            },
        }
    ]


async def sync(records: list[dict[str, Any]]) -> SyncReport:
    """Push applications to Notion. Never reads pipeline state back."""
    ok, reason = available()
    if not ok:
        return SyncReport(ok=False, reason=reason)

    token = notion_token()
    data_source_id = notion_data_source_id()
    assert token and data_source_id  # narrowed by available()

    api = _Client(token)
    report = SyncReport(ok=True)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        known = await api.existing_rows(client, data_source_id)
        for record in records:
            app_id = record.get("id")
            if not app_id:
                continue
            props = _properties(record)
            page_id = known.get(app_id)
            if page_id:
                good = await api.update(client, page_id, props)
                if good:
                    report.updated += 1
            else:
                good = await api.create(client, data_source_id, props)
                if good:
                    report.created += 1
            if not good:
                report.failed.append(f"{(record.get('company') or {}).get('name', '')}")

    return report
