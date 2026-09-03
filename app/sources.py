"""Job sources.

Every source here is a real, publicly documented, unauthenticated API. Each
returns actual open postings with working apply URLs.

Handshake is the one exception to "API": it publishes no search endpoint, but it
does advertise a sitemap of its public job pages in its own robots.txt and put a
schema.org `JobPosting` block on each of them. Structured data published for
machines to read is the same bargain a board API offers, so it is treated the
same way. See the config.py block for what bounds the cost.

Deliberately absent: LinkedIn, Indeed and Wellfound. None offers a public jobs
API, and all three forbid automated access to pages that render publicly —
Wellfound's terms name "automated or non-automated harvesting, collection or
'scraping'" specifically, and its `/company/*/jobs` pages already answer a
non-browser client with a bot challenge. Scraping them would risk the
candidate's account and breach those terms, so they are not implemented — the
gap is reported, not papered over.

Wellfound is worth a sentence more, because most of what it lists is reachable
anyway: on a sampled role page, 18 of its 31 postings carried an `atsSource` of
Greenhouse or Ashby, meaning the same posting is served by the employer's own
public board. The right response to wanting Wellfound coverage is to add those
employers to `GREENHOUSE_COMPANIES` / `ASHBY_COMPANIES`, not to crawl Wellfound.
"""
from __future__ import annotations

import time as _time

import asyncio
import datetime as _dt
import html
import json
import re
import time
from itertools import zip_longest
from typing import Any

import httpx

from .config import (
    GREENHOUSE_COMPANIES,
    HANDSHAKE_CONCURRENCY,
    HANDSHAKE_DETAIL_CAP,
    HANDSHAKE_INDEX_TTL_SECONDS,
    HANDSHAKE_JOB_URL,
    HANDSHAKE_SITEMAP,
    HANDSHAKE_TITLE_TERMS,
    HTTP_TIMEOUT_SECONDS,
)
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


# ── Handshake ────────────────────────────────────────────────────────────────
#
# Unlike every other source here, Handshake exposes no search. The config.py
# block explains why that is workable anyway and what bounds the cost.

_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S | re.I,
)
_HS_ID_RE = re.compile(r"/public/jobs/(\d+)")
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)

# id -> job dict, or None for "opened, and not a role this candidate wants".
# Caching the misses is the point: in a 400-posting sample only 9 were analyst
# work, so re-opening the other 391 on every crawl would be almost the whole
# bill. Ids are never reused, so a miss stays a miss.
_handshake_seen: dict[int, dict[str, Any] | None] = {}
_handshake_index: tuple[float, tuple[int, ...]] = (0.0, ())


def _handshake_title(raw: str, company: str) -> str:
    """Strip the "| Employer | Handshake" suffix the page title carries.

    Split-on-pipe-and-take-the-first is wrong: it survives "Analyst, Risk |
    Fidelity | Handshake" and mangles "Data | AI Engineer | Acme | Handshake".
    Only the two known trailing segments are removed.
    """
    title = (raw or "").strip()
    for suffix in (" | Handshake", f" | {company}" if company else ""):
        if suffix and title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def _handshake_place(posting: dict[str, Any]) -> str:
    """A location string `discovery.us_only` can read.

    `jobLocation` is a dict on most postings and a list on some. That is a real
    difference in the live feed rather than a hypothetical — calling `.get` on
    the list form raised AttributeError on the first sample taken.
    """
    if posting.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    place = posting.get("jobLocation")
    if isinstance(place, list):
        place = place[0] if place else None
    if not isinstance(place, dict):
        return ""
    address = place.get("address") or {}
    located = ", ".join(
        p for p in (address.get("addressLocality"), address.get("addressRegion")) if p
    )
    # `us_only` deliberately keeps anything it cannot classify, so a foreign
    # country has to be named for it to act. A US one is left off: "Dallas,
    # Texas" is what every other adapter produces.
    country = address.get("addressCountry") or ""
    if country in ("United States", "US", "USA"):
        return located or country
    if located and country:
        return f"{located}, {country}"
    return located or country


def _handshake_expired(posting: dict[str, Any], now: _dt.datetime | None = None) -> bool:
    """Whether the employer's own `validThrough` date has passed.

    Honouring it here means a closed posting never enters the pool at all,
    rather than being surfaced, scored, tailored against, and only then caught
    by the liveness check. An unparseable date is treated as no date: a posting
    is never dropped on the strength of a field we could not read.
    """
    raw = posting.get("validThrough") or ""
    if not raw:
        return False
    try:
        expires = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=_dt.timezone.utc)
    return expires < (now or _dt.datetime.now(_dt.timezone.utc))


def _handshake_job(job_id: int, posting: dict[str, Any]) -> dict[str, Any] | None:
    """One schema.org JobPosting, or None if it should not enter the pool."""
    if not isinstance(posting, dict) or posting.get("@type") != "JobPosting":
        return None

    company = ((posting.get("hiringOrganization") or {}).get("name") or "").strip()
    title = _handshake_title(posting.get("title", ""), company)
    if not title or not company:
        return None
    if not any(term in title.lower() for term in HANDSHAKE_TITLE_TERMS):
        return None
    if _handshake_expired(posting):
        return None

    description = strip_html(posting.get("description", ""))
    if not description:
        # Same rule as SmartRecruiters: a posting the scorer cannot read cannot
        # be assessed, and a meaningless score is worse than an absent one.
        return None

    return _job(
        jid=f"hs_{job_id}",
        title=title,
        company=company,
        location=_handshake_place(posting),
        description=description,
        apply_url=HANDSHAKE_JOB_URL.format(job_id=job_id),
        source="Handshake",
        ats=None,
        posted=posting.get("datePosted"),
    )


def parse_handshake_page(html_text: str) -> dict[str, Any] | None:
    """The JobPosting block from a public job page, if it has one."""
    for raw in _LD_RE.findall(html_text or ""):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


async def handshake_ids(client: httpx.AsyncClient) -> tuple[int, ...]:
    """Every public posting id, newest first. Cached — the sitemap is daily."""
    global _handshake_index

    cached_at, cached = _handshake_index
    if cached and time.time() - cached_at < HANDSHAKE_INDEX_TTL_SECONDS:
        return cached

    index = await client.get(HANDSHAKE_SITEMAP)
    index.raise_for_status()
    ids: set[int] = set()
    for sitemap_url in _SITEMAP_LOC_RE.findall(index.text):
        page = await client.get(sitemap_url)
        if page.status_code != 200:
            continue
        ids.update(int(m) for m in _HS_ID_RE.findall(page.text))

    if not ids:
        # Raise rather than cache an empty answer for six hours because the
        # sitemap format moved. `_safe_result` turns this into a named failure,
        # which is the whole point of reporting a gap instead of papering it.
        raise ValueError("handshake sitemap yielded no job ids")

    ordered = tuple(sorted(ids, reverse=True))
    _handshake_index = (time.time(), ordered)
    return ordered


async def handshake(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Newest public Handshake postings that match the candidate's titles."""
    ids = await handshake_ids(client)
    fresh = [i for i in ids if i not in _handshake_seen][:HANDSHAKE_DETAIL_CAP]
    gate = asyncio.Semaphore(HANDSHAKE_CONCURRENCY)

    async def detail(job_id: int) -> None:
        async with gate:
            try:
                page = await client.get(HANDSHAKE_JOB_URL.format(job_id=job_id))
            except httpx.HTTPError:
                # Left unrecorded so the next crawl retries it. A transient
                # timeout must not blacklist a posting for the life of the
                # process — that is how a source quietly shrinks over a week.
                return
            if page.status_code == 200:
                posting = parse_handshake_page(page.text)
                _handshake_seen[job_id] = _handshake_job(job_id, posting) if posting else None
            elif page.status_code in (404, 410):
                # Handshake's own answer, not a network blip: never open again.
                _handshake_seen[job_id] = None

    await asyncio.gather(*(detail(i) for i in fresh))

    # Everything matched so far, not merely what this pass opened. Returning
    # only the new ones would make the source report a few hundred roles on the
    # first crawl and a handful on every crawl after it, and the pool would
    # lose the rest fifteen minutes later.
    live = [job for job in _handshake_seen.values() if job is not None]
    live.sort(key=lambda j: j["postedAt"] or "", reverse=True)

    # Bound the memo. Only misses are dropped, and oldest-id first, so the
    # matched roles and the recent decisions both survive.
    if len(_handshake_seen) > 20000:
        for job_id in sorted(_handshake_seen)[:5000]:
            if _handshake_seen[job_id] is None:
                del _handshake_seen[job_id]
    return live


# Every label an adapter above stamps onto a job, for the places that report
# what discovery covers.
#
# It is a derived fact and it was maintained by hand, so it drifted: the health
# endpoint had been claiming six sources since before Workday and SmartRecruiters
# were written, and a candidate reading it had no way to know their roles were
# coming from boards the page did not mention. `test_handshake_source` fails if
# an adapter stamps a label that is not in here.
# Where a region's postings come from. `countries` is the ISO filter the API
# takes; the label is what the UI shows.
REGIONS: dict[str, dict[str, Any]] = {
    "us": {"label": "United States", "countries": "US", "location": ""},
    "in": {"label": "India", "countries": "IN", "location": ""},
}
DEFAULT_REGION = "us"


SOURCE_LABELS: tuple[str, ...] = (
    "Greenhouse",
    "Ashby",
    "Lever",
    "Workday",
    "SmartRecruiters",
    "The Muse",
    "Arbeitnow",
    "RemoteOK",
    "Handshake",
    "JobDataLake",
)

# Job boards deliberately not implemented, and why. Served to the UI so the gap
# is stated on the page rather than left to look like an oversight.
NOT_COVERED: dict[str, str] = {
    "linkedin": (
        "No public jobs API; their terms prohibit automated access even to "
        "publicly rendered pages. Not implemented by choice."
    ),
    # Reachable after all, through JobDataLake's aggregation. The old note
    # here said no free public search API was available to a server, which was
    # true of Indeed directly and stopped being the whole picture once an
    # aggregator with a documented API covered the same postings.
    "indeed": (
        "No direct API for a server, but the same postings are largely reachable "
        "through JobDataLake, which is implemented."
    ),
    "naukri": (
        "robots.txt sets `Disallow: /` for a named list of AI crawlers that "
        "includes claudebot, Claude-User and Claude-SearchBot, last updated "
        "2026-05-13, and the search pages do not answer a non-browser client at "
        "all. There is no public jobs API. Not implemented, and not a candidate "
        "for implementation. India coverage comes from JobDataLake instead."
    ),
    "wellfound": (
        "Their terms prohibit \"automated or non-automated harvesting, "
        "collection or 'scraping'\" of listings, and their pages answer a "
        "non-browser client with a bot challenge. Most of what Wellfound lists "
        "is reachable anyway — on a sampled page, 18 of 31 postings were served "
        "from the employer's own Greenhouse or Ashby board — so the way to "
        "cover it is to add those employers, not to crawl Wellfound."
    ),
}


async def fetch_source_results(region: str = DEFAULT_REGION) -> tuple[SourceResult, ...]:
    """Fetch each configured adapter independently and preserve its health.

    `region` only reaches JobDataLake. The nine direct board readers are lists
    of specific employers rather than searches, so there is nothing regional to
    vary in them -- a Greenhouse board returns what that company posts wherever
    it posts it. Filtering those by country here would silently hide postings
    the candidate asked for, so the region narrows the aggregator and leaves
    the direct readers alone.
    """
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
        tasks.append(_safe_result(handshake(client), "handshake"))
        # Returns nothing at all when no key is configured, which is why it can
        # be appended unconditionally.
        tasks.append(
            _safe_result(fetch_jobdatalake(client, region=region), f"jobdatalake/{region}")
        )
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


# ------------------------------------------------------------- JobDataLake
# An aggregator, deliberately, where the nine above are direct board readers.
#
# The case for it is coverage this codebase cannot otherwise reach. Workday
# publishes no board a server can read, and a large share of the financial
# services employers being targeted post there; iCIMS and Rippling are the
# same. It also covers markets outside the US, which is the whole basis of the
# India region -- Naukri is not an option and never will be, since its
# robots.txt sets `Disallow: /` for a named list of AI crawlers that includes
# claudebot, and its pages do not answer a non-browser client at all.
#
# Terms permit this: it is a documented HTTP API with published rate limits and
# an issued key, which is the same bargain Greenhouse and Ashby offer. That is
# the line the rest of this module draws, and it is why LinkedIn and Wellfound
# are refused rather than crawled.
JOBDATALAKE_URL = "https://api.jobdatalake.com/v1/jobs"



def _jobdatalake_job(row: dict[str, Any], description: str) -> dict[str, Any] | None:
    """One API row as a CareerOS job, or None if it cannot be used.

    Two ways a row is dropped. Without an apply URL there is nowhere to send
    the candidate, and the pipeline's whole output is somewhere to click.

    Without a description it must not enter the pool at all, which is the less
    obvious one. `score_job` on an empty description returns **85 with no
    gaps** -- higher than a real posting the candidate is a worse fit for,
    because there are no stated requirements to miss. That is the same failure
    AGENTS.md records from the mortgage-compliance posting that scored 98/100
    with "no gaps" for a job he was not qualified for. A job with no
    requirements text is unscoreable, and an unscoreable job that ranks above
    scoreable ones is worse than an absent one.
    """
    apply_url = (row.get("url") or row.get("apply_url") or "").strip()
    title = (row.get("title") or "").strip()
    if not apply_url or not title or not description.strip():
        return None

    company = row.get("company")
    if isinstance(company, dict):
        name = company.get("name") or company.get("company_name") or ""
    else:
        name = row.get("company_name") or company or ""
    name = str(name).strip() or "Unknown"

    locations = row.get("locations") or []
    place = ", ".join(str(x) for x in locations[:2] if x) if isinstance(locations, list) else str(locations)
    if row.get("remote_type") == "fully_remote" and "remote" not in place.lower():
        place = f"{place}, Remote".strip(", ")

    return _job(
        jid=f"jdl_{row.get('job_handle') or row.get('handle') or row.get('id')}",
        title=title,
        company=name,
        location=place or "Not specified",
        description=strip_html(description),
        apply_url=apply_url,
        source="JobDataLake",
        # The domain is the closest thing the list carries to an originating
        # ATS. The apply URL names the real one, and the apply flow reads that.
        ats=None,
        posted=_jobdatalake_date(row.get("posted_at")),
    )


def _jobdatalake_date(value: Any) -> str | None:
    """`posted_at` in two shapes, because the two endpoints disagree.

    The search endpoint returns Unix milliseconds (1788459717875); the detail
    endpoint returns ISO ('2026-09-03T18:21:57.875Z') for the same posting.
    Hydration merges detail over search, so handling only the integer form
    silently dropped every date on every job.
    """
    import datetime as _dt

    if isinstance(value, str) and value.strip():
        try:
            return _dt.datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            ).date().isoformat()
        except ValueError:
            return None

    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).date().isoformat()


async def fetch_jobdatalake(
    client: Any,
    query: str = "*",
    region: str = DEFAULT_REGION,
    per_page: int = 60,
    posted_within_days: int = 30,
    hydrate_limit: int = 60,
) -> list[dict[str, Any]]:
    """Postings from JobDataLake for one region, or nothing when unconfigured.

    Two calls per job, not one. The search endpoint returns no description --
    only title, company, locations and skills -- and a job with no requirements
    text cannot be scored (see `_jobdatalake_job`). So each row is hydrated
    from `/v1/jobs/:handle`, and any that cannot be is dropped.

    That costs `1 + n` credits per refresh, which is why `hydrate_limit` is
    bounded and modest. The free tier is 1,000 credits; a 60-job refresh is 61.
    """
    import asyncio

    from .config import jobdatalake_key

    key = jobdatalake_key()
    if not key:
        return []

    spec = REGIONS.get(region) or REGIONS[DEFAULT_REGION]
    headers = {"X-API-Key": key}
    params: dict[str, Any] = {
        "q": query or "*",
        "per_page": min(per_page, 100),
        "sort_by": "posted_at:desc",
        "posted_after": int((_time.time() - posted_within_days * 86400) * 1000),
    }
    if spec["countries"]:
        params["countries"] = spec["countries"]
    if spec["location"]:
        params["location"] = spec["location"]

    response = await client.get(JOBDATALAKE_URL, params=params, headers=headers, timeout=20.0)
    response.raise_for_status()
    body = response.json()
    rows = body.get("jobs") or body.get("data") or body.get("results") or []

    async def hydrate(row: dict[str, Any]) -> dict[str, Any] | None:
        handle = row.get("job_handle") or row.get("handle")
        if not handle:
            return None
        try:
            detail = await client.get(
                f"{JOBDATALAKE_URL}/{handle}", headers=headers, timeout=20.0
            )
            detail.raise_for_status()
            payload = detail.json()
            full = payload.get("job") if isinstance(payload.get("job"), dict) else payload
        except Exception:
            # One posting failing to hydrate is not a source outage. The
            # surrounding _safe_result would mark the whole adapter unhealthy.
            return None
        return _jobdatalake_job({**row, **full}, full.get("description") or "")

    # Their published limit is 10 requests per second. Eight in flight leaves
    # room for the rest of the crawl, which runs concurrently with this.
    gate = asyncio.Semaphore(8)

    async def bounded(row: dict[str, Any]) -> dict[str, Any] | None:
        async with gate:
            return await hydrate(row)

    hydrated = await asyncio.gather(*(bounded(r) for r in rows[:hydrate_limit]))
    return [j for j in hydrated if j]
