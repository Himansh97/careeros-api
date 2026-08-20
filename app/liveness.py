"""Is a staged posting still open?

A SoFi Mortgage Compliance Analyst role was tailored, packaged and staged, and
then taken down without anything noticing. Nothing in the pipeline ever asked
whether a posting still existed, so a dead application sat at `ready`
indefinitely, indistinguishable from a live one.

Two checks, cheapest first:

* **API-sourced jobs** (Greenhouse, Ashby, Lever, Workday, The Muse) are checked
  against the pool that discovery already fetched. A posting that has vanished
  from its own board is closed. This costs nothing — the pool is in memory.
* **Locally-stored jobs** (Indeed, Dice, pasted links) cannot be checked that
  way: they live in our own database, so their presence proves only that we
  saved them. These need a real HTTP look at the posting, which is what
  Firecrawl is for.

Firecrawl is used only for that second group — roughly ten postings a day,
inside the free tier — and only when a key is configured. Without one the
check still runs and reports those as unverifiable rather than guessing.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

# Job ids whose source has a live public API. Everything else is stored locally
# and cannot be verified from the pool.
API_PREFIXES = ("gh_", "ashby_", "lever_", "workday_", "muse_")

# Phrases that mean a posting is gone. Deliberately specific: a page that merely
# fails to mention the job is not evidence of closure, and calling a live role
# dead would be worse than missing a dead one.
CLOSED_MARKERS = (
    "no longer accepting applications",
    "no longer accepting",
    "this job is no longer available",
    "this position is no longer available",
    "position has been filled",
    "posting has expired",
    "job posting has expired",
    "this position is closed",
    "job has been closed",
    "we are no longer considering",
    "applications are closed",
    "this job has expired",
)

# Pages that exist but say nothing useful. Treated as unknown, not as closed —
# an aggregator redirect or a login wall is not a statement about the job.
INCONCLUSIVE_MARKERS = (
    "verify you are human",
    "enable javascript",
    "sign in to continue",
    "access denied",
)


def firecrawl_key() -> str | None:
    return os.getenv("FIRECRAWL_API_KEY") or None


def classify(text: str, status_code: int) -> tuple[str, str]:
    """Return (verdict, why) for a fetched posting page."""
    if status_code in (404, 410):
        return "closed", f"HTTP {status_code} — the posting URL is gone"
    if status_code >= 400:
        return "unknown", f"HTTP {status_code}"

    low = (text or "").lower()
    if not low.strip():
        return "unknown", "empty page"

    hit = next((m for m in CLOSED_MARKERS if m in low), None)
    if hit:
        return "closed", f'page says "{hit}"'

    blocked = next((m for m in INCONCLUSIVE_MARKERS if m in low), None)
    if blocked:
        return "unknown", f"page is gated ({blocked})"

    return "live", "posting still reachable"


async def check_via_firecrawl(client: httpx.AsyncClient, url: str, key: str) -> tuple[str, str]:
    """Scrape one posting through Firecrawl and classify it."""
    try:
        r = await client.post(
            FIRECRAWL_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        return "unknown", f"firecrawl error: {type(exc).__name__}"

    if r.status_code == 402:
        return "unknown", "firecrawl credits exhausted"
    if r.status_code == 401:
        return "unknown", "firecrawl key rejected"
    if r.status_code != 200:
        return "unknown", f"firecrawl HTTP {r.status_code}"

    payload = r.json().get("data") or {}
    meta = payload.get("metadata") or {}
    # Firecrawl reports the origin's own status, which is the strongest signal
    # available — a 404 from the employer beats any text heuristic.
    origin_status = meta.get("statusCode") or 200
    return classify(payload.get("markdown") or "", int(origin_status))


async def check_all(
    applications: list[dict[str, Any]],
    pool_ids: set[str],
    key: str | None,
) -> list[dict[str, str]]:
    """Check every staged application, cheapest method first."""
    results: list[dict[str, str]] = []
    needs_scrape: list[dict[str, Any]] = []

    for app in applications:
        job_id = app.get("jobId") or ""
        company = app.get("company")
        company = company.get("name") if isinstance(company, dict) else str(company or "?")
        entry = {"jobId": job_id, "company": company, "title": app.get("title", "")}

        if job_id.startswith(API_PREFIXES):
            live = job_id in pool_ids
            results.append({
                **entry,
                "verdict": "live" if live else "closed",
                "why": "present on its board" if live else "no longer on its board",
                "method": "api",
            })
        else:
            needs_scrape.append({**entry, "applyUrl": app.get("applyUrl") or ""})

    if not needs_scrape:
        return results

    if not key:
        for entry in needs_scrape:
            results.append({
                **{k: v for k, v in entry.items() if k != "applyUrl"},
                "verdict": "unverified",
                "why": "no FIRECRAWL_API_KEY — this source has no public API to check",
                "method": "none",
            })
        return results

    async with httpx.AsyncClient() as client:
        for entry in needs_scrape:
            url = entry.pop("applyUrl", "")
            if not url:
                results.append({**entry, "verdict": "unknown", "why": "no apply URL",
                                "method": "none"})
                continue
            verdict, why = await check_via_firecrawl(client, url, key)
            results.append({**entry, "verdict": verdict, "why": why, "method": "firecrawl"})

    return results


# A summary that does not add up is worse than no summary. The script counted
# only `unverified` as unchecked and never counted `unknown` at all, so a run
# where Firecrawl rate-limited a dozen postings printed
#
#     55 checked — 10 closed, 42 live, 0 unverifiable
#
# where 10 + 42 = 52. Three postings vanished from their own report, and the
# line actively asserted that everything had been determined. Undetermined is
# not live, and a check that could not reach a page has to say so.
def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts that account for every result, plus why any went undetermined."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.get("verdict") or "unknown"] = counts.get(r.get("verdict") or "unknown", 0) + 1

    undetermined = [r for r in results
                    if r.get("verdict") not in ("live", "closed")]
    reasons: dict[str, int] = {}
    for r in undetermined:
        reasons[str(r.get("why") or "no reason given")] = (
            reasons.get(str(r.get("why") or "no reason given"), 0) + 1
        )

    summary = {
        "total": len(results),
        "counts": counts,
        "undetermined": len(undetermined),
        "reasons": reasons,
        # Transient and worth calling out separately: re-running later fixes it,
        # whereas a missing key or a dead URL does not.
        "rate_limited": sum(1 for r in undetermined if "429" in str(r.get("why") or "")),
    }
    accounted = counts.get("live", 0) + counts.get("closed", 0) + summary["undetermined"]
    if accounted != summary["total"]:      # pragma: no cover - arithmetic guard
        raise AssertionError(
            f"liveness summary does not account for every result: "
            f"{accounted} of {summary['total']}"
        )
    return summary
