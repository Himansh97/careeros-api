"""Recruiter contact discovery.

No job board exposes recruiter identities — Greenhouse, Ashby, and The Muse
all return zero contact fields (verified directly). So contacts come from
exactly two places, both honest:

1. **Manual entry.** Always available, no key required. If you find a
   recruiter on LinkedIn yourself, record them here.
2. **Hunter.io domain search.** Returns real, verified email addresses that
   Hunter has actually observed, each with its own confidence score. Requires
   the candidate's own free API key (25 searches/month).

What this module will never do is guess. Constructing `first.last@company.com`
from a name is fabrication dressed as discovery: it produces a plausible
address with no evidence the mailbox exists, and sending to it can bounce
against the company's mail server or reach the wrong person entirely.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

import httpx

from .config import HTTP_TIMEOUT_SECONDS
from .store import connect, now

RECRUITER_TITLE_HINTS = (
    "recruit",
    "talent",
    "people",
    "hiring",
    "staffing",
    "sourcer",
    "human resources",
    "hr ",
)

CONTACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    company TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    linkedin_url TEXT,
    confidence INTEGER DEFAULT 0,
    provider TEXT NOT NULL,
    why_selected TEXT,
    status TEXT DEFAULT 'not_started',
    created_at TEXT NOT NULL
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CONTACTS_SCHEMA)


def hunter_key() -> str | None:
    return os.environ.get("HUNTER_API_KEY") or None


# Hosts that are never the employer. An apply URL points at whoever hosts the
# posting, which for an aggregator or ATS is not the company hiring — so a
# lookup against it returns staff of the wrong organisation entirely. This
# listed only ATS vendors, so a Dice-hosted AIT Global posting resolved to
# dice.com and produced a Dice recruiter as the contact for an AIT Global job.
# Returning nothing is correct here: no contact beats the wrong contact.
THIRD_PARTY_HOSTS = (
    # ATS vendors
    "greenhouse.io", "ashbyhq.com", "lever.co", "workable.com",
    "smartrecruiters.com", "jobvite.com", "icims.com", "myworkdayjobs.com",
    "workday.com", "bamboohr.com", "breezy.hr", "recruitee.com",
    "teamtailor.com", "personio.de", "ripplematch.com", "rippling.com",
    # Job boards and aggregators
    "indeed.com", "dice.com", "ziprecruiter.com", "linkedin.com",
    "glassdoor.com", "monster.com", "careerbuilder.com", "simplyhired.com",
    "themuse.com", "arbeitnow.com", "remoteok.com", "weworkremotely.com",
    "builtin.com", "wellfound.com", "angel.co", "jobright.ai",
    # URL shorteners — the destination is unknown until followed
    "bit.ly", "tinyurl.com", "lnkd.in",
)


def company_domain(company_name: str, apply_url: str) -> str | None:
    """Infer the company's domain from its own apply URL.

    This is inference about a *company domain*, not about a person — and it's
    checkable, since the URL genuinely belongs to the employer. Returns None
    whenever the URL belongs to a third party rather than the employer.
    """
    m = re.search(r"https?://([^/]+)", apply_url or "")
    if not m:
        return None
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    if any(bad in host for bad in THIRD_PARTY_HOSTS):
        return None
    return host


async def resolve_domain_by_company(company_name: str) -> str | None:
    """Ask Hunter which domain a company name belongs to.

    Most postings are hosted on an ATS or a job board, so the apply URL yields
    no employer domain and contact lookup simply gave up — five of eight
    shortlisted applications had no addressee for that reason alone. Guessing
    the domain from the name would be fabrication ("Scope Infotech" ->
    scopeinfotech.com is a coin flip), but Hunter maintains the mapping, so
    asking is a lookup rather than a guess.

    The answer is only accepted when Hunter's own organisation name agrees with
    the company we asked about — otherwise a near-miss would attach a stranger's
    colleagues to the application.
    """
    key = hunter_key()
    if not key or not company_name.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            r = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={"company": company_name, "limit": 1, "api_key": key},
            )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None

    data = (r.json() or {}).get("data") or {}
    domain = data.get("domain")
    organization = (data.get("organization") or "").strip().lower()
    if not domain:
        return None

    # Require the names to agree, tolerating legal suffixes: Hunter answers
    # "AIT Global Inc." for "AIT Global", and demanding an exact match threw
    # away a correct domain. Containment either way accepts that while still
    # refusing an unrelated company.
    asked = re.sub(r"[^a-z0-9]", "", company_name.strip().lower())
    org = re.sub(r"[^a-z0-9]", "", organization)
    if org and asked and asked not in org and org not in asked:
        return None
    if any(bad in domain.lower() for bad in THIRD_PARTY_HOSTS):
        return None
    return domain


async def lookup_contacts(domain: str, limit: int = 10) -> dict[str, Any]:
    """Find contacts via the provider chain, failing over as quotas run out."""
    from .providers import find_contacts

    return await find_contacts(domain, limit)


async def hunter_domain_search(domain: str, limit: int = 10) -> dict[str, Any]:
    """Look up real, Hunter-verified addresses for a domain."""
    key = hunter_key()
    if not key:
        return {
            "available": False,
            "reason": "no_api_key",
            "detail": (
                "Set HUNTER_API_KEY to enable live contact lookup. Hunter's free "
                "tier allows 25 domain searches per month with no credit card."
            ),
            "contacts": [],
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            # Ask for the HR/recruiting department first. An unfiltered search
            # returns whoever Hunter has indexed most confidently — at a large
            # company that's engineering and sales leadership, who are the
            # wrong people to email about an open role.
            r = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "limit": limit,
                    "department": "hr",
                    "api_key": key,
                },
            )
            if r.status_code == 200 and not (r.json().get("data") or {}).get("emails"):
                # No HR contacts indexed — fall back to a general search so the
                # user at least sees who is reachable, clearly unflagged.
                r = await client.get(
                    "https://api.hunter.io/v2/domain-search",
                    params={"domain": domain, "limit": limit, "api_key": key},
                )
        except httpx.HTTPError as exc:
            return {"available": False, "reason": "request_failed", "detail": str(exc), "contacts": []}

    if r.status_code != 200:
        # Surface Hunter's own error rather than guessing at the cause. A 429
        # can mean either a spent quota or a restricted account, and those
        # need completely different fixes.
        try:
            errors = r.json().get("errors") or []
        except ValueError:
            errors = []
        first = errors[0] if errors else {}
        err_id = first.get("id") or ("invalid_key" if r.status_code == 401 else "error")
        detail = first.get("details") or f"HTTP {r.status_code}"

        hint = ""
        if err_id == "restricted_account":
            hint = (
                " Hunter commonly restricts free accounts created with a personal "
                "email (gmail/outlook) from domain search, and may ask you to "
                "confirm your address or use a work domain. Log in to hunter.io "
                "to see the specific reason."
            )
        elif err_id in {"too_many_requests", "usage_exceeded"}:
            hint = " Free tier allows 25-50 searches per month."

        return {
            "available": False,
            "reason": err_id,
            "detail": detail + hint,
            "domain": domain,
            "contacts": [],
        }

    data = r.json().get("data", {})
    people: list[dict[str, Any]] = []
    for e in data.get("emails", []):
        title = (e.get("position") or "").strip()
        name = " ".join(filter(None, [e.get("first_name"), e.get("last_name")])).strip()
        if not name:
            continue
        is_recruiter = any(h in title.lower() for h in RECRUITER_TITLE_HINTS)
        people.append(
            {
                "name": name,
                "title": title or "Unknown role",
                "email": e.get("value"),
                # Hunter's confidence reflects how strongly it has observed
                # this address in the wild. It is Hunter's number, not ours.
                "confidence": e.get("confidence") or 0,
                "emailVerified": (e.get("verification") or {}).get("status") == "valid",
                "linkedinUrl": e.get("linkedin"),
                "isRecruiter": is_recruiter,
                "provider": "hunter.io",
            }
        )

    # Recruiters first, then by Hunter's own confidence.
    people.sort(key=lambda p: (not p["isRecruiter"], -p["confidence"]))
    return {
        "available": True,
        "domain": domain,
        "organization": data.get("organization"),
        "contacts": people,
        "note": (
            "Addresses come from Hunter.io's index of publicly observed emails. "
            "Confidence is Hunter's own score. Nothing is sent automatically."
        ),
    }


def save_contact(payload: dict[str, Any]) -> dict[str, Any]:
    contact_id = payload.get("id") or f"c_{payload['company']}_{payload['name']}".lower().replace(" ", "-")
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO contacts
               (id, job_id, company, name, title, email, email_verified,
                linkedin_url, confidence, provider, why_selected, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                contact_id,
                payload.get("jobId"),
                payload["company"],
                payload["name"],
                payload.get("title"),
                payload.get("email"),
                1 if payload.get("emailVerified") else 0,
                payload.get("linkedinUrl"),
                int(payload.get("confidence") or 0),
                payload.get("provider", "manual"),
                payload.get("whySelected"),
                payload.get("status", "not_started"),
                now(),
            ),
        )
    return get_contact(contact_id) or {}


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "jobId": r["job_id"],
        "company": r["company"],
        "name": r["name"],
        "title": r["title"],
        "email": r["email"],
        "emailVerified": bool(r["email_verified"]),
        "linkedinUrl": r["linkedin_url"],
        "confidence": r["confidence"],
        "provider": r["provider"],
        "whySelected": r["why_selected"],
        "status": r["status"],
        "createdAt": r["created_at"],
    }


def list_contacts() -> list[dict[str, Any]]:
    with connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM contacts ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def get_contact(contact_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        _ensure_schema(conn)
        r = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    return _row(r) if r else None


def set_contact_status(contact_id: str, status: str) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("UPDATE contacts SET status=? WHERE id=?", (status, contact_id))
