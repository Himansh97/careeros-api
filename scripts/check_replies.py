#!/usr/bin/env python3
"""Check Gmail against the pipeline and flag what is still owed.

Two questions the app could not answer:

* **Did I actually send it?** Approving a recruiter reply is a decision to
  reply. If nothing followed, the app still showed it as handled — the GitLab
  reply to Izzy Chu read "Approved for draft creation. No email has been sent."
  for hours after it had, in fact, been sent.
* **Is anything waiting on me?** A SoFi application stopped on a Greenhouse
  security-code step on 11 Aug with "resubmit your application", and nothing in
  CareerOS noticed, because nothing was watching the inbox for blockers.

The API holds no Gmail credentials by design, so this takes a Gmail snapshot
captured in an agent session and reconciles it.

    # in an agent session with the Gmail connector:
    #   search_threads("in:sent newer_than:30d")   -> sent.json
    #   search_threads("in:inbox newer_than:30d")  -> inbox.json
    ./.venv/bin/python scripts/check_replies.py --sent sent.json --inbox inbox.json --dry-run
    ./.venv/bin/python scripts/check_replies.py --sent sent.json --inbox inbox.json

Writing is limited to the two facts Gmail is authoritative about: that a reply
went out, and that an application is blocked on a step. It never sends, never
drafts, and never changes an application's status.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.alerts import build_alerts  # noqa: E402
from app.recruiter_messages import list_messages, mark_draft_sent  # noqa: E402
from app.store import add_timeline, connect, list_applications, now  # noqa: E402
from app.db import initialize  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconcile_sent import best_match, flatten  # noqa: E402

# Phrases that mean an application stopped and is waiting on the candidate.
# Deliberately narrow: "thanks for applying" is not a blocker, and treating it
# as one would bury the real ones.
BLOCKERS = (
    ("security code", "Security code required — resubmit the application"),
    ("resubmit your application", "Application needs resubmitting"),
    ("action required", "Action required on this application"),
    ("complete your application", "Application left incomplete"),
    ("additional information", "Employer asked for more information"),
    ("schedule your interview", "Interview waiting to be scheduled"),
    ("assessment", "Assessment outstanding"),
)

# Senders that are never a person waiting on a reply. Matched against the whole
# address, not just the local part: `Alliant@notifications.ultipro.com` carries
# its giveaway in the domain and was passing an earlier `notifications?@` test.
AUTOMATED = re.compile(
    r"no-?reply|donotreply|do-not-reply|notification|alerts?[@.]|jobalert"
    r"|mailer-daemon|newsletter|marketing|messages\.|substack|beehiiv"
    r"|greenhouse-mail|ultipro|myworkday|icims|jobs2web|talent",
    re.IGNORECASE,
)


def company_tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (name or "").lower())}


def _company_of(app: dict) -> str:
    company = app.get("company")
    return company.get("name") if isinstance(company, dict) else str(company or "")


def find_blockers(inbox: list[dict], applications: list[dict]) -> list[tuple[dict, str, str]]:
    """Inbox messages that say an application is stuck, matched to the record.

    A blocker email often names only the employer — "your application to SoFi"
    does not say which role, and there are two SoFi applications. Matching on
    company alone picked whichever came first in the list, so consecutive runs
    tagged a different one each time and the same alert appeared twice.

    So: prefer the application whose job title actually appears in the message,
    and when nothing distinguishes them, pick deterministically and only once
    per (company, problem).
    """
    found: list[tuple[dict, str, str]] = []
    claimed: set[tuple[str, str]] = set()

    for message in inbox:
        text = f"{message.get('subject','')} {message.get('snippet','')}".lower()
        hit = next((label for marker, label in BLOCKERS if marker in text), None)
        if not hit:
            continue
        sender = (message.get("sender") or "").lower()

        matches = [
            app for app in applications
            if company_tokens(_company_of(app))
            and (
                company_tokens(_company_of(app)) & company_tokens(sender)
                or company_tokens(_company_of(app)) & company_tokens(text)
            )
        ]
        if not matches:
            continue

        # A title named in the message is decisive. Otherwise fall back to a
        # stable order so the same email always lands on the same record.
        titled = [a for a in matches if (a.get("title") or "").lower() in text]
        chosen = (titled or sorted(matches, key=lambda a: a.get("jobId") or ""))[0]

        key = (_company_of(chosen), hit)
        if key in claimed:
            continue
        claimed.add(key)
        found.append((chosen, hit, message.get("subject", "")))

    return found


def unanswered(inbox: list[dict], sent: list[dict]) -> list[dict]:
    """Real people who wrote and have had no reply from any of our sent mail."""
    replied_to = set()
    for m in sent:
        for key in ("toRecipients", "ccRecipients"):
            for a in m.get(key) or []:
                found = re.search(r"[\w.+-]+@[\w.-]+", a or "")
                if found:
                    replied_to.add(found.group(0).lower())

    out = []
    for message in inbox:
        sender = message.get("sender") or ""
        if AUTOMATED.search(sender):
            continue
        address = re.search(r"[\w.+-]+@[\w.-]+", sender)
        if not address or address.group(0).lower() in replied_to:
            continue
        out.append(message)
    return out


def main() -> int:
    initialize()
    ap = argparse.ArgumentParser()
    ap.add_argument("--sent", required=True, help="JSON: Gmail in:sent snapshot")
    ap.add_argument("--inbox", help="JSON: Gmail in:inbox snapshot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sent = flatten(json.loads(Path(args.sent).read_text()))
    inbox = flatten_inbox(json.loads(Path(args.inbox).read_text())) if args.inbox else []
    print(f"  {len(sent)} sent, {len(inbox)} inbox messages\n")

    # 1. Replies that did go out.
    marked = 0
    for message in list_messages():
        draft = message.get("draft") or {}
        msg_id = message.get("gmailMessageId")
        if not msg_id or draft.get("sentAt") or draft.get("status") == "dismissed":
            continue
        match, why = best_match(draft, sent)
        if match:
            print(f"  SENT      {(draft.get('subject') or '')[:52]}")
            print(f"            {why}")
            if not args.dry_run:
                mark_draft_sent(msg_id, match["id"], match.get("date"))
            marked += 1

    # 2. Applications blocked on a step.
    applications = list_applications()
    blocked = find_blockers(inbox, applications)
    for app, label, subject in blocked:
        company = app.get("company")
        company = company.get("name") if isinstance(company, dict) else str(company or "?")
        print(f"  BLOCKED   {company[:22]:<22} {label}")
        print(f"            from: {subject[:66]}")
        if not args.dry_run:
            app_id = app.get("id") or f"app_{app.get('jobId')}"
            with connect() as conn:
                conn.execute(
                    "UPDATE applications SET next_action=?, updated_at=? WHERE id=?",
                    (label, now(), app_id),
                )
                add_timeline(conn, app_id, f"Gmail says: {label}")

    # 3. People still waiting on a reply.
    waiting = unanswered(inbox, sent)
    for message in waiting[:10]:
        print(f"  NO REPLY  {(message.get('sender') or '')[:34]:<34} {(message.get('subject') or '')[:44]}")

    print(f"\n  {marked} reply(ies) confirmed sent, {len(blocked)} application(s) blocked, "
          f"{len(waiting)} unanswered")
    if args.dry_run:
        print("  Dry run. Nothing written.")
        return 0

    print(f"\n  {len(build_alerts())} open alerts — GET /api/alerts")
    return 0


def flatten_inbox(payload) -> list[dict]:
    threads = payload.get("threads", payload) if isinstance(payload, dict) else payload
    out = []
    for t in threads:
        for m in t.get("messages", [t]):
            if "SENT" not in (m.get("labelIds") or []):
                out.append(m)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
