"""Pluggable contact-lookup providers with automatic failover.

Every provider here was verified to expose a live API that works on a free
tier. Ruled out after checking their own docs, not a listicle:

  - Skrapp.io  — API access is Enterprise-only; the free plan has none.
  - Apollo.io  — free plan excludes API access; it starts at the paid tiers.
  - Snov.io    — free credits are trial-only and expire, so it can't be a
                 dependable fallback.

Providers are tried in order. A provider that has no key configured is
skipped silently; one that reports an exhausted quota or a restricted
account falls through to the next. If every provider is unavailable the
caller gets the accumulated reasons, never a fabricated contact.
"""
from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from .config import HTTP_TIMEOUT_SECONDS

RECRUITER_TITLE_HINTS = (
    "recruit",
    "talent",
    "people",
    "hiring",
    "staffing",
    "sourcer",
    "human resources",
)

# Errors that mean "this provider can't serve us right now" — try the next one
# rather than failing the whole lookup.
FAILOVER_REASONS = {
    "restricted_account",
    "usage_exceeded",
    "too_many_requests",
    "rate_limited",
    "quota_exceeded",
    "forbidden",
    "payment_required",
}


def _person(
    *,
    name: str,
    title: str | None,
    email: str | None,
    confidence: int,
    verified: bool,
    linkedin: str | None,
    provider: str,
) -> dict[str, Any]:
    t = (title or "").strip()
    return {
        "name": name.strip(),
        "title": t or "Unknown role",
        "email": email,
        "confidence": int(confidence or 0),
        "emailVerified": bool(verified),
        "linkedinUrl": linkedin,
        "isRecruiter": any(h in t.lower() for h in RECRUITER_TITLE_HINTS),
        "provider": provider,
    }


# --------------------------------------------------------------------------
# Hunter.io — 25-50 domain searches/month free.
# --------------------------------------------------------------------------
async def hunter(client: httpx.AsyncClient, domain: str, limit: int) -> dict[str, Any]:
    key = os.environ.get("HUNTER_API_KEY")
    if not key:
        return {"ok": False, "reason": "no_key", "provider": "hunter.io"}

    async def call(params: dict[str, Any]) -> httpx.Response:
        return await client.get(
            "https://api.hunter.io/v2/domain-search",
            params={**params, "domain": domain, "limit": limit, "api_key": key},
        )

    # HR department first — an unfiltered search returns whoever is indexed
    # most confidently, which at a big company is engineering and sales.
    r = await call({"department": "hr"})
    if r.status_code == 200 and not (r.json().get("data") or {}).get("emails"):
        r = await call({})

    if r.status_code != 200:
        errors = (r.json().get("errors") or [{}]) if r.headers.get("content-type", "").startswith("application/json") else [{}]
        return {
            "ok": False,
            "provider": "hunter.io",
            "reason": errors[0].get("id") or f"http_{r.status_code}",
            "detail": errors[0].get("details") or f"HTTP {r.status_code}",
        }

    data = r.json().get("data", {})
    people = [
        _person(
            name=" ".join(filter(None, [e.get("first_name"), e.get("last_name")])),
            title=e.get("position"),
            email=e.get("value"),
            confidence=e.get("confidence") or 0,
            verified=(e.get("verification") or {}).get("status") == "valid",
            linkedin=e.get("linkedin"),
            provider="hunter.io",
        )
        for e in data.get("emails", [])
        if e.get("first_name") or e.get("last_name")
    ]
    return {
        "ok": True,
        "provider": "hunter.io",
        "organization": data.get("organization"),
        "contacts": people,
    }


# --------------------------------------------------------------------------
# Tomba.io — 25 domain searches/month free. Uses paired key/secret headers.
# --------------------------------------------------------------------------
async def tomba(client: httpx.AsyncClient, domain: str, limit: int) -> dict[str, Any]:
    key = os.environ.get("TOMBA_API_KEY")
    secret = os.environ.get("TOMBA_SECRET")
    if not key or not secret:
        return {"ok": False, "reason": "no_key", "provider": "tomba.io"}

    r = await client.get(
        "https://api.tomba.io/v1/domain-search",
        params={"domain": domain, "limit": limit},
        headers={"X-Tomba-Key": key, "X-Tomba-Secret": secret},
    )
    if r.status_code != 200:
        err = {}
        try:
            err = r.json().get("errors") or {}
        except ValueError:
            pass
        return {
            "ok": False,
            "provider": "tomba.io",
            "reason": err.get("type") or f"http_{r.status_code}",
            "detail": err.get("message") or f"HTTP {r.status_code}",
        }

    data = r.json().get("data", {})
    people = []
    for e in data.get("emails", []):
        name = (e.get("full_name") or "").strip() or " ".join(
            filter(None, [e.get("first_name"), e.get("last_name")])
        )
        if not name:
            continue
        people.append(
            _person(
                name=name,
                title=e.get("position"),
                email=e.get("email"),
                confidence=e.get("score") or 0,
                verified=bool(e.get("verification", {}).get("status") == "valid")
                if isinstance(e.get("verification"), dict)
                else False,
                linkedin=e.get("linkedin"),
                provider="tomba.io",
            )
        )
    org = (data.get("organization") or {})
    return {
        "ok": True,
        "provider": "tomba.io",
        "organization": org.get("organization") if isinstance(org, dict) else None,
        "contacts": people,
    }


# --------------------------------------------------------------------------
# Anymail Finder — REST API, free trial credits.
# --------------------------------------------------------------------------
async def anymail(client: httpx.AsyncClient, domain: str, limit: int) -> dict[str, Any]:
    key = os.environ.get("ANYMAIL_API_KEY")
    if not key:
        return {"ok": False, "reason": "no_key", "provider": "anymailfinder.com"}

    r = await client.get(
        "https://api.anymailfinder.com/v5.0/search/company.json",
        params={"domain": domain},
        headers={"Authorization": f"Bearer {key}"},
    )
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("error_explained", "")
        except ValueError:
            pass
        return {
            "ok": False,
            "provider": "anymailfinder.com",
            "reason": "quota_exceeded" if r.status_code == 402 else f"http_{r.status_code}",
            "detail": detail or f"HTTP {r.status_code}",
        }

    payload = r.json()
    people = [
        _person(
            name=e.get("full_name") or e.get("email", "").split("@")[0],
            title=e.get("job_title"),
            email=e.get("email"),
            confidence=90 if e.get("validation") == "valid" else 50,
            verified=e.get("validation") == "valid",
            linkedin=None,
            provider="anymailfinder.com",
        )
        for e in (payload.get("results") or [])
    ][:limit]
    return {"ok": True, "provider": "anymailfinder.com", "organization": None, "contacts": people}


PROVIDERS: list[tuple[str, Callable]] = [
    ("hunter.io", hunter),
    ("tomba.io", tomba),
    ("anymailfinder.com", anymail),
]


def configured_providers() -> list[dict[str, Any]]:
    return [
        {"name": "hunter.io", "configured": bool(os.environ.get("HUNTER_API_KEY")), "freeTier": "25-50 domain searches/month"},
        {
            "name": "tomba.io",
            "configured": bool(os.environ.get("TOMBA_API_KEY") and os.environ.get("TOMBA_SECRET")),
            "freeTier": "25 searches/month",
        },
        {"name": "anymailfinder.com", "configured": bool(os.environ.get("ANYMAIL_API_KEY")), "freeTier": "trial credits"},
    ]


async def find_contacts(domain: str, limit: int = 10) -> dict[str, Any]:
    """Try each configured provider in turn until one returns contacts."""
    attempts: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        for name, fn in PROVIDERS:
            try:
                result = await fn(client, domain, limit)
            except httpx.HTTPError as exc:
                attempts.append({"provider": name, "reason": "request_failed", "detail": str(exc)})
                continue

            if result.get("ok") and result.get("contacts"):
                result["contacts"].sort(key=lambda p: (not p["isRecruiter"], -p["confidence"]))
                return {
                    "available": True,
                    "domain": domain,
                    "provider": result["provider"],
                    "organization": result.get("organization"),
                    "contacts": result["contacts"],
                    "attempts": attempts,
                    "note": (
                        f"Addresses come from {result['provider']}'s index of publicly "
                        "observed emails. Confidence is that provider's own score. "
                        "Nothing is sent automatically."
                    ),
                }

            if result.get("ok"):
                attempts.append({"provider": name, "reason": "no_results"})
            else:
                attempts.append(
                    {
                        "provider": name,
                        "reason": result.get("reason"),
                        "detail": result.get("detail"),
                    }
                )

    unconfigured = [a for a in attempts if a["reason"] == "no_key"]
    return {
        "available": False,
        "domain": domain,
        "reason": "all_providers_unavailable",
        "detail": (
            "No provider returned contacts. "
            + (
                f"{len(unconfigured)} provider(s) have no API key configured. "
                if unconfigured
                else ""
            )
            + "See attempts for each provider's reason."
        ),
        "attempts": attempts,
        "contacts": [],
    }
