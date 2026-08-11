"""Live job discovery via Greenhouse's public job-board API.

This is a real, unauthenticated source: every job returned here is an actual
open posting fetched at request time. Coverage is limited to the companies
listed in config.GREENHOUSE_COMPANIES — that limit is real and surfaced to
the client rather than hidden.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

import httpx

from .config import (
    CACHE_TTL_SECONDS,
    GREENHOUSE_COMPANIES,
    HTTP_TIMEOUT_SECONDS,
)

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/div|/h[1-6])[^>]*>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _work_arrangement(location: str, description: str) -> str:
    loc = (location or "").lower()
    desc = (description or "")[:2000].lower()
    if "remote" in loc:
        return "remote"
    if "hybrid" in loc or "hybrid" in desc:
        return "hybrid"
    if "remote" in desc:
        return "remote"
    return "onsite"


async def _fetch_company(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        # A single board being unavailable must not fail the whole search.
        return []

    jobs: list[dict[str, Any]] = []
    for j in payload.get("jobs", []):
        description = _strip_html(j.get("content", ""))
        location = (j.get("location") or {}).get("name", "") or "Not specified"
        jobs.append(
            {
                "id": f"gh_{slug}_{j.get('id')}",
                "title": j.get("title", "").strip(),
                "company": {"id": slug, "name": slug.replace("-", " ").title()},
                "location": location,
                "workArrangement": _work_arrangement(location, description),
                "source": "Greenhouse",
                "atsPlatform": "Greenhouse",
                "postedAt": j.get("updated_at"),
                "discoveredAt": None,  # filled by caller
                "description": description,
                "applyUrl": j.get("absolute_url", ""),
            }
        )
    return jobs


_source_counts: dict[str, int] = {}
# Sources that errored on the most recent fetch, surfaced via /api/health so a
# degraded search is visible rather than looking like a quiet day on the boards.
_last_failures: list[str] = []


async def fetch_all_jobs(force: bool = False) -> list[dict[str, Any]]:
    """Fetch (and cache) every job across all configured sources."""
    from .sources import fetch_every_source

    ts = time.time()
    cached = _cache.get("all")
    if cached and not force and ts - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    from .imported import list_imported

    jobs, counts, failures = await fetch_every_source()

    # A source that errored returns no jobs, which is indistinguishable from an
    # employer with no openings — so a transient Greenhouse error would be
    # cached as "Stripe has zero postings", and every saved Stripe application
    # 404'd until the TTL expired. Carry forward the previous snapshot's jobs
    # for anything that failed this round rather than publishing the loss.
    if failures and cached:
        have = {j["id"] for j in jobs}
        recovered = [j for j in cached[1] if j["id"] not in have]
        if recovered:
            jobs = jobs + recovered
            for j in recovered:
                counts[j["source"]] = counts.get(j["source"], 0) + 1

    # Everything polled from a source API is something the system found.
    for j in jobs:
        j["origin"] = "fetched"

    # Imported postings (Indeed etc.) sit alongside live ones. They're flagged
    # so the UI can say they weren't fetched live.
    #
    # `origin` is a different question from `importedNotLive`, and the two were
    # being conflated. A Greenhouse link the candidate pasted is resolved
    # through the board's real API, so it is perfectly live — but it is still
    # there because the candidate put it there, not because discovery found it.
    # Anything in the imported store is theirs; that is what they want to
    # separate from the daily haul.
    existing = {(j["company"]["name"].lower(), j["title"].lower()) for j in jobs}
    for j in list_imported():
        key = (j["company"]["name"].lower(), j["title"].lower())
        if key in existing:
            continue
        existing.add(key)
        jobs.append({**j, "origin": "pasted"})
        counts[j["source"]] = counts.get(j["source"], 0) + 1

    _source_counts.clear()
    _source_counts.update(counts)
    _last_failures.clear()
    _last_failures.extend(failures)
    # Don't hold a degraded snapshot for the full TTL — retry sooner.
    _cache["all"] = (ts - CACHE_TTL_SECONDS * 0.8 if failures else ts, jobs)
    return jobs


def add_to_cache(job: dict[str, Any]) -> None:
    """Make a single job visible immediately, without refetching everything.

    Importing one pasted link used to call `fetch_all_jobs(force=True)`, which
    re-hit five job boards for ~3,000 postings so that one job would resolve —
    slow enough that the request timed out before returning. The cached list is
    just a list; the new job belongs at the front of it.
    """
    cached = _cache.get("all")
    if not cached:
        return
    ts, jobs = cached
    _cache["all"] = (ts, [job] + [j for j in jobs if j["id"] != job["id"]])


def source_counts() -> dict[str, int]:
    return dict(_source_counts)


def failed_sources() -> list[str]:
    """Sources that errored on the most recent fetch."""
    return list(_last_failures)


def us_only(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop roles based outside the United States.

    The candidate is on F-1/OPT, which authorises employment in the US only, so
    a role in Dublin or São Paulo can never be taken however well it scores.
    The eligibility gate already flagged these, but flagging happens after a
    role has been surfaced, ranked and — twice now — actually applied to. This
    removes them from the pool instead.

    `eligibility._foreign_location` is reused rather than reimplemented so
    there is one definition of "outside the US". It is deliberately
    conservative: an unrecognised or bare "Remote" location is kept, because
    losing a real US-remote role is worse than showing one that the
    eligibility gate will catch anyway.
    """
    from .eligibility import _foreign_location

    return [j for j in jobs if _foreign_location(j) is None]


def filter_jobs(
    jobs: list[dict[str, Any]],
    query: str | None = None,
    location: str | None = None,
    work_arrangements: list[str] | None = None,
    united_states_only: bool = True,
) -> list[dict[str, Any]]:
    out = us_only(jobs) if united_states_only else jobs

    if query:
        terms = [t for t in query.lower().split() if t]
        # Match against the job title only. Including the company name here
        # produced false positives — every Databricks posting "matched" the
        # term "data" purely because of the company's name.
        out = [j for j in out if all(t in j["title"].lower() for t in terms)]

    if location:
        loc = location.lower()
        if loc == "remote":
            out = [j for j in out if j["workArrangement"] == "remote"]
        else:
            out = [j for j in out if loc in j["location"].lower()]

    if work_arrangements:
        wanted = set(work_arrangements)
        out = [j for j in out if j["workArrangement"] in wanted]

    return out
