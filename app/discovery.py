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


async def fetch_all_jobs(force: bool = False) -> list[dict[str, Any]]:
    """Fetch (and cache) every job across all configured sources."""
    from .sources import fetch_every_source

    ts = time.time()
    cached = _cache.get("all")
    if cached and not force and ts - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    jobs, counts = await fetch_every_source()
    _source_counts.clear()
    _source_counts.update(counts)
    _cache["all"] = (ts, jobs)
    return jobs


def source_counts() -> dict[str, int]:
    return dict(_source_counts)


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
