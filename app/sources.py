"""Job sources.

Every source here is a real, publicly documented, unauthenticated API. Each
returns actual open postings with working apply URLs.

Deliberately absent: LinkedIn and Indeed. Neither offers a public jobs API,
and LinkedIn's terms prohibit automated access even to pages that render
publicly. Scraping them would risk the candidate's account and breach those
terms, so they are not implemented — the gap is reported, not papered over.
"""
from __future__ import annotations

import asyncio
import html
import re
from itertools import zip_longest
from typing import Any

import httpx

from .config import GREENHOUSE_COMPANIES, HTTP_TIMEOUT_SECONDS

ASHBY_COMPANIES = ["ramp", "linear", "vanta", "clipboardhealth", "runwayml"]

# The Muse categories relevant to this candidate's target roles.
MUSE_CATEGORIES = [
    "Data Science",
    "Data and Analytics",
    "Business Operations",
    "Project Management",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/div|/h[1-6])[^>]*>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(ln.strip() for ln in text.split("\n") if ln.strip())


def _arrangement(location: str, description: str) -> str:
    loc = (location or "").lower()
    desc = (description or "")[:2000].lower()
    if "remote" in loc:
        return "remote"
    if "hybrid" in loc or "hybrid" in desc:
        return "hybrid"
    if "remote" in desc:
        return "remote"
    return "onsite"


def _job(
    *,
    jid: str,
    title: str,
    company: str,
    location: str,
    description: str,
    apply_url: str,
    source: str,
    ats: str | None,
    posted: str | None,
) -> dict[str, Any]:
    return {
        "id": jid,
        "title": (title or "").strip(),
        "company": {"id": company.lower().replace(" ", "-"), "name": company},
        "location": location or "Not specified",
        "workArrangement": _arrangement(location, description),
        "source": source,
        "atsPlatform": ats,
        "postedAt": posted,
        "discoveredAt": None,
        "description": description,
        "applyUrl": apply_url,
    }


async def _safe(coro) -> list[dict[str, Any]]:
    """A failing source must never take down the whole search."""
    try:
        return await coro
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


async def greenhouse(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    r = await client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    )
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        desc = strip_html(j.get("content", ""))
        out.append(
            _job(
                jid=f"gh_{slug}_{j.get('id')}",
                title=j.get("title", ""),
                company=slug.replace("-", " ").title(),
                location=(j.get("location") or {}).get("name", ""),
                description=desc,
                apply_url=j.get("absolute_url", ""),
                source="Greenhouse",
                ats="Greenhouse",
                posted=j.get("updated_at"),
            )
        )
    return out


async def ashby(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))
        out.append(
            _job(
                jid=f"ashby_{slug}_{j.get('id')}",
                title=j.get("title", ""),
                company=slug.replace("-", " ").title(),
                location=j.get("location", ""),
                description=desc,
                apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
                source="Ashby",
                ats="Ashby",
                posted=j.get("publishedAt"),
            )
        )
    return out


async def muse(client: httpx.AsyncClient, category: str, pages: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        r = await client.get(
            "https://www.themuse.com/api/public/jobs",
            params={"category": category, "page": page},
        )
        if r.status_code != 200:
            break
        for j in r.json().get("results", []):
            locs = ", ".join(l.get("name", "") for l in j.get("locations", []))
            out.append(
                _job(
                    jid=f"muse_{j.get('id')}",
                    title=j.get("name", ""),
                    company=(j.get("company") or {}).get("name", "Unknown"),
                    location=locs,
                    description=strip_html(j.get("contents", "")),
                    apply_url=(j.get("refs") or {}).get("landing_page", ""),
                    source="The Muse",
                    ats=None,
                    posted=j.get("publication_date"),
                )
            )
    return out


async def arbeitnow(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await client.get("https://www.arbeitnow.com/api/job-board-api")
    r.raise_for_status()
    out = []
    for j in r.json().get("data", []):
        out.append(
            _job(
                jid=f"arbeitnow_{j.get('slug')}",
                title=j.get("title", ""),
                company=j.get("company_name", "Unknown"),
                location=j.get("location", ""),
                description=strip_html(j.get("description", "")),
                apply_url=j.get("url", ""),
                source="Arbeitnow",
                ats=None,
                posted=None,
            )
        )
    return out


async def remoteok(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    # RemoteOK's terms require a follow link back to the posting; applyUrl
    # points at their listing, which satisfies that.
    r = await client.get(
        "https://remoteok.com/api", headers={"User-Agent": "CareerOS/1.0"}
    )
    r.raise_for_status()
    payload = r.json()
    out = []
    for j in payload[1:] if payload else []:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(
            _job(
                jid=f"remoteok_{j.get('id')}",
                title=j.get("position", ""),
                company=j.get("company", "Unknown"),
                location=j.get("location") or "Remote",
                description=strip_html(j.get("description", "")),
                apply_url=j.get("url", ""),
                source="RemoteOK",
                ats=None,
                posted=j.get("date"),
            )
        )
    return out


async def fetch_every_source() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fetch all sources in parallel. Returns (jobs, per-source counts)."""
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        tasks: list[Any] = []
        tasks += [_safe(greenhouse(client, s)) for s in GREENHOUSE_COMPANIES]
        tasks += [_safe(ashby(client, s)) for s in ASHBY_COMPANIES]
        tasks += [_safe(muse(client, c)) for c in MUSE_CATEGORIES]
        tasks.append(_safe(arbeitnow(client)))
        tasks.append(_safe(remoteok(client)))
        results = await asyncio.gather(*tasks)

    deduped: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}

    for batch in results:
        kept: list[dict[str, Any]] = []
        for job in batch:
            # Dedupe across sources on (company, title) — aggregators reuse
            # the same postings that appear on company boards.
            key = (job["company"]["name"].lower(), job["title"].lower())
            if key in seen:
                continue
            seen.add(key)
            kept.append(job)
            counts[job["source"]] = counts.get(job["source"], 0) + 1
        if kept:
            deduped.append(kept)

    # Interleave one job per source at a time. Concatenating the batches left
    # the pool in fetch order, so the largest board sat entirely at the front:
    # anything downstream that reads a prefix — scoring caps this, browsing
    # without a query hits it too — saw only that one company. Round-robin
    # makes any prefix representative of the whole pool.
    jobs: list[dict[str, Any]] = []
    for row in zip_longest(*deduped):
        jobs.extend(job for job in row if job is not None)

    return jobs, counts
