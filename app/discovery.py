"""Live job discovery via Greenhouse's public job-board API.

This is a real, unauthenticated source: every job returned here is an actual
open posting fetched at request time. Coverage is limited to the companies
listed in config.GREENHOUSE_COMPANIES — that limit is real and surfaced to
the client rather than hidden.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import html
import logging
import re
import time
from typing import Any

import httpx

from .config import (
    CACHE_TTL_SECONDS,
    GREENHOUSE_COMPANIES,
    HTTP_TIMEOUT_SECONDS,
)
from .discovery_store import (
    DiscoverySnapshot,
    SourceResult,
    current_snapshot,
    prune_generations,
    record_generation,
)

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# The in-flight background refresh, if any. Guarded so a burst of requests
# arriving after the TTL expires starts one crawl rather than one each.
_refresh_task: "asyncio.Task[list[dict[str, Any]]] | None" = None

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
    """Every job across all configured sources, cached, refreshed behind you.

    An expired cache used to mean the *next* request paid for the refresh —
    five job boards and ~3,000 postings, thirty to forty seconds. That cost
    landed wherever it happened to fall, and where it fell most was
    `/api/approvals`, which needs the pool only to look up jobs it already has
    approvals for. Every fifteen minutes, whoever opened the approvals queue
    first sat on a grey skeleton for forty seconds.

    So a stale snapshot is served immediately and the refresh runs behind the
    response. Nothing here needs a pool fresher than its TTL — the postings
    barely move in fifteen minutes, and the criterion that actually decides
    whether a req has closed reads the stored application record, not this.
    Callers that genuinely need current data pass `force=True` and still block,
    which is what the explicit refresh button wants.
    """
    ts = time.time()
    cached = _cache.get("all")
    if cached is None and not force:
        restored = _restore_durable_cache()
        if restored is not None:
            _cache["all"] = restored
            cached = restored
    if cached and not force and ts - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    # Expired but present. Hand back what we have and refresh out of band.
    if cached and not force:
        _schedule_refresh()
        return cached[1]

    return await _refresh()


def _schedule_refresh() -> None:
    """Start a background refresh unless one is already running.

    The guard matters: without it, ten requests arriving after the TTL expires
    would each start their own full crawl of every board.
    """
    global _refresh_task

    if _refresh_task is not None and not _refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refresh_task = loop.create_task(_refresh_quietly())


async def _refresh_quietly() -> None:
    """Refresh in the background, where nothing is waiting to catch an error.

    A failure here must not surface: the caller already has its answer and the
    stale snapshot stays valid. `_last_failures` still records what broke, so
    the degraded state remains visible in the places that report it.
    """
    try:
        await _refresh()
    except Exception:  # noqa: BLE001 - nothing is awaiting this to raise into
        logger.exception("background job refresh failed; serving stale snapshot")


async def _refresh() -> list[dict[str, Any]]:
    """Actually crawl every source and replace the snapshot."""
    from .sources import fetch_source_results

    ts = time.time()

    from .imported import list_imported

    results = await fetch_source_results()
    for result in results:
        record_generation(result)
    prune_generations(keep=30)
    jobs, counts, failures = _project_snapshot(current_snapshot())

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


def _project_snapshot(
    snapshot: DiscoverySnapshot,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    from .sources import project_source_results

    results = tuple(
        SourceResult(
            source.source_key,
            source.state,
            tuple(job for job in snapshot.jobs if job.get("sourceKey") == source.source_key),
            source.error_code,
        )
        for source in snapshot.sources
    )
    return project_source_results(results)


def _restore_durable_cache() -> tuple[float, list[dict[str, Any]]] | None:
    snapshot = current_snapshot()
    if not snapshot.sources:
        return None
    jobs, counts, failures = _project_snapshot(snapshot)
    for job in jobs:
        job["origin"] = "fetched"
    _source_counts.clear()
    _source_counts.update(counts)
    _last_failures.clear()
    _last_failures.extend(failures)
    try:
        generated_at = datetime.fromisoformat(snapshot.generated_at or "").timestamp()
    except ValueError:
        generated_at = 0.0
    return generated_at, jobs


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
        # Title, company and description — with word boundaries.
        #
        # This searched the title alone, because including the company name
        # made every Databricks posting "match" the term "data". That was a
        # substring problem, not a reason to ignore two thirds of the posting:
        # the cost was that searching for a skill found almost nothing.
        # "python" returned 3 jobs — the ones with Python in their *title* —
        # while hundreds of postings requiring Python were unreachable, and
        # "stripe" found 1 of that employer's 540 openings.
        #
        # Word boundaries fix the original complaint properly: "data" no
        # longer matches "Databricks", and "go" no longer matches "goals",
        # which is the same rule scoring._contains has always used.
        patterns = [
            re.compile(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])") for t in terms
        ]

        # Where a term matched decides how good the match is, so the scope is
        # recorded rather than thrown away. Searching "data engineer" against
        # descriptions alone returned 2,043 postings led by a Support Engineer
        # role, because both words appear somewhere in almost any tech posting.
        # Title matches are what the words meant; description matches are the
        # long tail worth having for a skill like "airflow" that rarely appears
        # in a title.
        def _scope(job: dict[str, Any]) -> str | None:
            title = job.get("title", "").lower()
            company = (job.get("company") or {}).get("name", "").lower()
            if all(p.search(title) for p in patterns):
                return "title"
            if all(p.search(f"{title} {company}") for p in patterns):
                return "company"
            body = (job.get("description", "") or "").lower()
            if all(p.search(f"{title} {company} {body}") for p in patterns):
                return "description"
            return None

        matched: list[dict[str, Any]] = []
        for job in out:
            scope = _scope(job)
            if scope:
                job["matchScope"] = scope
                matched.append(job)
        out = matched

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
