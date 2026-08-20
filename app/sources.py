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
import datetime as _dt
import html
import re
from itertools import zip_longest
from typing import Any

import httpx

from .config import GREENHOUSE_COMPANIES, HTTP_TIMEOUT_SECONDS
from .discovery_store import SourceResult, SourceState

# Slugs are verified against each board's live API rather than guessed — a dead
# slug returns nothing, which is indistinguishable from an employer with no
# openings. clipboardhealth and runwayml were dropped here for exactly that:
# both had been erroring silently.
ASHBY_COMPANIES = [
    # anthropic moved to Greenhouse; its Ashby board 404s and the failure was
    # logged every morning while 491 open roles stayed invisible. Listed in
    # GREENHOUSE_COMPANIES instead.
    "openai", "harvey", "elevenlabs", "sierra", "ramp", "decagon",
    "cursor", "perplexity", "vanta", "replit", "linear", "modal", "browserbase",
]

# Lever exposes the same kind of documented public API as Greenhouse and Ashby.
# leverdemo is deliberately excluded: it is Lever's own sample board, and its
# postings are not real jobs.
LEVER_COMPANIES = ["spotify", "palantir", "matchgroup"]

# Workday powers the careers page of most large employers, and exposes the same
# JSON its own front-end consumes. Each entry is (label, cxs base) verified
# against the live endpoint; capitalone, intuit, humana, elevance and nike were
# probed and rejected (401/404/422) rather than left in to fail silently.
WORKDAY_BOARDS: list[tuple[str, str]] = [
    ("Leidos", "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/External"),
    ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"),
    ("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site"),
    ("Adobe", "https://adobe.wd5.myworkdayjobs.com/wday/cxs/adobe/external_experienced"),
    ("CVS Health", "https://cvshealth.wd1.myworkdayjobs.com/wday/cxs/cvshealth/CVS_Health_Careers"),
    ("Mastercard", "https://mastercard.wd1.myworkdayjobs.com/wday/cxs/mastercard/CorporateCareers"),
    ("PayPal", "https://paypal.wd1.myworkdayjobs.com/wday/cxs/paypal/jobs"),
    ("Fidelity", "https://wd1.myworkdaysite.com/wday/cxs/fmr/FidelityCareers"),
    ("Truist", "https://truist.wd1.myworkdayjobs.com/wday/cxs/truist/Careers"),
    ("Target", "https://target.wd5.myworkdayjobs.com/wday/cxs/target/targetcareers"),
]

# The listing endpoint returns no description, and scoring cannot read a job
# without one — so each posting costs a second request. Searching server-side
# first keeps that bounded: these terms are what the candidate is actually
# looking for, so the detail fetches are spent on plausible roles instead of on
# a whole 700-role board.
WORKDAY_SEARCHES = ("data analyst", "business analyst", "business intelligence", "analytics")
WORKDAY_DETAIL_CAP = 25   # per board, per run

# SmartRecruiters, same pattern: a documented public API whose listing carries
# no description, so each posting costs a second request. Its API takes both a
# keyword and a country filter, so the narrowing happens server-side before we
# spend anything. Verified live; slugs that returned nothing are left out.
SMARTRECRUITERS_COMPANIES = ["BoschGroup", "Wabtec", "WesternDigital", "Visa"]
SMARTRECRUITERS_SEARCHES = ("data analyst", "business analyst", "business intelligence")
SMARTRECRUITERS_DETAIL_CAP = 20   # per company, per run

# Brands whose own capitalisation .title() destroys. The company name goes into
# the resume, the filename and the outreach greeting, so writing to "Sofi" or
# "Gitlab" is a small but real credibility cost with the people reading it.
BRAND_CASING = {
    "sofi": "SoFi",
    "gitlab": "GitLab",
    "github": "GitHub",
    "openai": "OpenAI",
    "paypal": "PayPal",
    "linkedin": "LinkedIn",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "ebay": "eBay",
    "spacex": "SpaceX",
    "deepmind": "DeepMind",
    "hubspot": "HubSpot",
    "salesforce": "Salesforce",
    "servicenow": "ServiceNow",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "runwayml": "Runway ML",
    "clipboardhealth": "Clipboard Health",
    "boschgroup": "Bosch",
    "westerndigital": "Western Digital",
}


def _company_from_slug(slug: str) -> str:
    """Human-readable employer name from an ATS board slug."""
    key = slug.replace("-", "").replace("_", "").lower()
    if key in BRAND_CASING:
        return BRAND_CASING[key]
    return slug.replace("-", " ").title()

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


def classify_source_error(exc: BaseException) -> str:
    """Map provider failures to a bounded, non-sensitive operational code."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limited"
        return "network"
    if isinstance(exc, httpx.HTTPError):
        return "network"
    return "parse"


async def _safe_result(coro, source_key: str) -> SourceResult:
    """Capture one adapter result without persisting raw exception text."""
    try:
        jobs = await coro
    except Exception as exc:  # noqa: BLE001 - isolation is the adapter boundary
        return SourceResult(
            source_key=source_key,
            state=SourceState.DEGRADED,
            jobs=(),
            error_code=classify_source_error(exc),
        )
    return SourceResult(
        source_key=source_key,
        state=SourceState.HEALTHY,
        jobs=tuple(jobs),
    )


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
                company=_company_from_slug(slug),
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
                company=_company_from_slug(slug),
                location=j.get("location", ""),
                description=desc,
                apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
                source="Ashby",
                ats="Ashby",
                posted=j.get("publishedAt"),
            )
        )
    return out


async def lever(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    r = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        posted = j.get("createdAt")
        if isinstance(posted, (int, float)):
            # Lever stamps in epoch milliseconds; everything downstream expects
            # an ISO string like the other sources produce.
            posted = _dt.datetime.fromtimestamp(posted / 1000, _dt.timezone.utc).isoformat()
        out.append(
            _job(
                jid=f"lever_{slug}_{j.get('id')}",
                title=j.get("text", ""),
                company=slug.replace("-", " ").title(),
                location=cats.get("location", ""),
                description=j.get("descriptionPlain")
                or strip_html(j.get("description", "")),
                apply_url=j.get("applyUrl") or j.get("hostedUrl", ""),
                source="Lever",
                ats="Lever",
                posted=posted if isinstance(posted, str) else None,
            )
        )
    return out


async def workday(client: httpx.AsyncClient, label: str, base: str) -> list[dict[str, Any]]:
    """Search one Workday board, then fetch descriptions for the best matches."""
    seen: dict[str, dict[str, Any]] = {}
    for term in WORKDAY_SEARCHES:
        r = await client.post(
            f"{base}/jobs",
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term},
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            continue
        for post in r.json().get("jobPostings", []):
            path = post.get("externalPath")
            if path and path not in seen:
                seen[path] = post

    async def detail(path: str, post: dict[str, Any]) -> dict[str, Any] | None:
        try:
            d = await client.get(f"{base}{path}", headers={"Accept": "application/json"})
            info = d.json().get("jobPostingInfo", {}) if d.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            info = {}
        description = strip_html(info.get("jobDescription", ""))
        if not description:
            # Without a description the scorer has nothing to read, and the role
            # would land with no requirements and a meaningless score. Drop it
            # rather than publish a job that cannot be assessed.
            return None
        return _job(
            jid=f"workday_{label.lower().replace(' ', '')}_{info.get('jobReqId') or path.rsplit('_', 1)[-1]}",
            title=info.get("title") or post.get("title", ""),
            company=label,
            location=info.get("location") or post.get("locationsText", ""),
            description=description,
            apply_url=info.get("externalUrl", "") or f"{base.split('/wday/')[0]}{path}",
            source="Workday",
            ats="Workday",
            posted=info.get("startDate") or info.get("posted"),
        )

    picked = list(seen.items())[:WORKDAY_DETAIL_CAP]
    results = await asyncio.gather(*(detail(p, j) for p, j in picked))
    return [j for j in results if j]


async def smartrecruiters(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    """Search one SmartRecruiters board, then fetch the matching descriptions."""
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    seen: dict[str, dict[str, Any]] = {}
    for term in SMARTRECRUITERS_SEARCHES:
        # country=us is applied at the source: this candidate cannot take a role
        # abroad, and filtering here means we never pay to fetch one.
        r = await client.get(base, params={"q": term, "limit": 20, "country": "us"})
        if r.status_code != 200:
            continue
        for post in r.json().get("content", []):
            pid = post.get("id")
            if pid and pid not in seen:
                seen[pid] = post

    async def detail(post: dict[str, Any]) -> dict[str, Any] | None:
        try:
            d = await client.get(post.get("ref", ""))
            info = d.json() if d.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            info = {}

        sections = ((info.get("jobAd") or {}).get("sections") or {})
        description = strip_html(
            " ".join(
                (v or {}).get("text", "")
                for v in sections.values()
                if isinstance(v, dict)
            )
        )
        if not description:
            # A posting the scorer cannot read cannot be assessed, so it is
            # dropped rather than published with a meaningless score.
            return None

        loc = post.get("location") or {}
        city = ", ".join(p for p in (loc.get("city"), loc.get("region")) if p)
        return _job(
            jid=f"sr_{slug.lower()}_{post.get('id')}",
            title=post.get("name", ""),
            company=_company_from_slug(slug),
            location=city or (loc.get("country") or "").upper(),
            description=description,
            apply_url=info.get("applyUrl") or post.get("ref", ""),
            source="SmartRecruiters",
            ats="SmartRecruiters",
            posted=post.get("releasedDate"),
        )

    picked = list(seen.values())[:SMARTRECRUITERS_DETAIL_CAP]
    results = await asyncio.gather(*(detail(p) for p in picked))
    return [j for j in results if j]


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


async def fetch_source_results() -> tuple[SourceResult, ...]:
    """Fetch each configured adapter independently and preserve its health."""
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        tasks: list[Any] = []
        tasks += [_safe_result(greenhouse(client, s), f"greenhouse/{s}")
                  for s in GREENHOUSE_COMPANIES]
        tasks += [_safe_result(ashby(client, s), f"ashby/{s}")
                  for s in ASHBY_COMPANIES]
        tasks += [_safe_result(lever(client, s), f"lever/{s}")
                  for s in LEVER_COMPANIES]
        tasks += [_safe_result(workday(client, label, base), f"workday/{label.lower()}")
                  for label, base in WORKDAY_BOARDS]
        tasks += [_safe_result(smartrecruiters(client, s), f"smartrecruiters/{s.lower()}")
                  for s in SMARTRECRUITERS_COMPANIES]
        tasks += [_safe_result(muse(client, c), f"muse/{c.lower().replace(' ', '-')}")
                  for c in MUSE_CATEGORIES]
        tasks.append(_safe_result(arbeitnow(client), "arbeitnow"))
        tasks.append(_safe_result(remoteok(client), "remoteok"))
        return tuple(await asyncio.gather(*tasks))


def project_source_results(
    results: tuple[SourceResult, ...],
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Compatibility projection with deduplication and fair interleaving."""
    failures = [
        f"{result.source_key}: {result.error_code}"
        for result in results
        if result.state is not SourceState.HEALTHY
    ]

    deduped: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}

    for result in results:
        kept: list[dict[str, Any]] = []
        for job in result.jobs:
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

    return jobs, counts, failures


async def fetch_every_source() -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Compatibility API over structured per-adapter results."""
    return project_source_results(await fetch_source_results())
