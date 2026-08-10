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

    # Imported postings (Indeed etc.) sit alongside live ones. They're flagged
    # so the UI can say they weren't fetched live.
    existing = {(j["company"]["name"].lower(), j["title"].lower()) for j in jobs}
    for j in list_imported():
        key = (j["company"]["name"].lower(), j["title"].lower())
        if key in existing:
            continue
        existing.add(key)
        jobs.append(j)
        counts[j["source"]] = counts.get(j["source"], 0) + 1

    _source_counts.clear()
    _source_counts.update(counts)
    _last_failures.clear()
    _last_failures.extend(failures)
    # Don't hold a degraded snapshot for the full TTL — retry sooner.
    _cache["all"] = (ts - CACHE_TTL_SECONDS * 0.8 if failures else ts, jobs)
    return jobs


def source_counts() -> dict[str, int]:
    return dict(_source_counts)


def failed_sources() -> list[str]:
    """Sources that errored on the most recent fetch."""
    return list(_last_failures)


def filter_jobs(
    jobs: list[dict[str, Any]],
    query: str | None = None,
    location: str | None = None,
    work_arrangements: list[str] | None = None,
) -> list[dict[str, Any]]:
    out = jobs

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
