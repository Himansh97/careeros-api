"""Mirror the application pipeline into Notion.

One way. CareerOS is the source of truth for every column this writes; Notion
owns only what the candidate types into `Notes`. See `app/notion.py` for why the
contract is deliberately lopsided rather than a two-way sync.

Three modes, in the order you need them:

    # 1. once — build the database and print the id to put in .env
    ./.venv/bin/python scripts/sync_notion.py --setup <parent_page_id>

    # 2. any time — what a sync would do
    ./.venv/bin/python scripts/sync_notion.py

    # 3. the real thing, and what the daily job runs
    ./.venv/bin/python scripts/sync_notion.py --write

`--setup` takes the id of a Notion page you have shared with the integration.
A Notion integration can only see pages explicitly shared with it, so a token
with no shared pages is not broken — it simply has nothing in scope, which the
API reports as a 404 rather than a permission error.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import (  # noqa: E402
    HTTP_TIMEOUT_SECONDS,
    NOTION_VERSION,
    notion_token,
)
from app.notion import KEY_PROPERTY, NOTES_PROPERTY, available, sync  # noqa: E402
from app.store import list_applications  # noqa: E402

# The schema the mirror writes. Names are load-bearing — `app.notion` addresses
# properties by name, so renaming a column in Notion detaches it from the sync.
_SCHEMA = {
    "Name": {"title": {}},
    KEY_PROPERTY: {"rich_text": {}},
    "Company": {"rich_text": {}},
    "Status": {"select": {}},
    "Fit": {"number": {}},
    "Resume": {"number": {}},
    "Apply URL": {"url": {}},
    "Submitted": {"date": {}},
    # Created for the candidate, then never written again.
    NOTES_PROPERTY: {"rich_text": {}},
}


async def setup(parent_page_id: str) -> int:
    token = notion_token()
    if not token:
        print("no NOTION_TOKEN in careeros-api/.env — nothing to set up with")
        return 1

    # Since 2025-09-03 a database is a container and the schema belongs to the
    # data source inside it, so `properties` nests under `initial_data_source`
    # rather than sitting at the top level as it did in 2022-06-28.
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "CareerOS — Applications"}}],
        "initial_data_source": {"properties": _SCHEMA},
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            "https://api.notion.com/v1/databases",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json=body,
        )

    if resp.status_code >= 400:
        print(f"Notion refused the database ({resp.status_code}):")
        print(f"  {resp.text[:400]}")
        if resp.status_code == 404:
            print(
                "\n  A 404 here almost always means the page is not shared with the "
                "integration.\n  Open the page in Notion -> ... -> Connections -> add "
                "your integration, then retry."
            )
        return 1

    data = resp.json()
    sources = data.get("data_sources") or []
    if not sources:
        print("database created but it reported no data source — cannot continue")
        return 1

    print("Created 'CareerOS — Applications'.\n")
    print("Add this to careeros-api/.env:\n")
    print(f"    NOTION_DATA_SOURCE_ID={sources[0]['id']}\n")
    print("Then run:  ./.venv/bin/python scripts/sync_notion.py --write")
    return 0


async def run(write: bool) -> int:
    ok, reason = available()
    if not ok:
        print(f"mirror is off — {reason}")
        print("\n  This is a normal state. CareerOS works identically without it.")
        return 0

    records = list_applications()
    if not write:
        print(f"{len(records)} applications would be mirrored to Notion.")
        print("  Columns written: Name, Company, Status, Fit, Resume, Apply URL, Submitted")
        print(f"  Never written:   {NOTES_PROPERTY} — that column is yours")
        print("\n  dry run — nothing sent. Pass --write to sync.")
        return 0

    report = await sync(records)
    print(report.summary)
    for name in report.failed:
        print(f"  failed: {name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", metavar="PARENT_PAGE_ID", help="create the database once")
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    if args.setup:
        return asyncio.run(setup(args.setup))
    return asyncio.run(run(args.write))


if __name__ == "__main__":
    raise SystemExit(main())
