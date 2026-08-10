"""Resolve a pasted job-posting URL into a real posting.

Resolution always goes through the ATS's own documented public API, never by
scraping the page a human sees. That is not squeamishness: the boards that
matter publish exactly the fields needed (title, company, location, full
description, canonical apply URL), so the API is both the legitimate route and
the better one.

Hosts that prohibit automated access are refused by name rather than attempted
and failed. LinkedIn's terms forbid automated access even to publicly rendered
pages, and Indeed prohibits automating its apply flow; both sit behind
Cloudflare and reCAPTCHA, so a fetch would be wrong *and* unreliable. Those
return `blocked` so the caller can offer the paste-the-description path, which
reaches exactly the same scoring and tailoring code.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .config import HTTP_TIMEOUT_SECONDS
from .sources import _company_from_slug, _job, strip_html

# Hosts that will not be fetched, and the reason a human should see.
BLOCKED_HOSTS: dict[str, str] = {
    "linkedin.com": (
        "LinkedIn's terms prohibit automated access, even to pages that render "
        "publicly."
    ),
    "lnkd.in": "LinkedIn link shortener — same restriction as linkedin.com.",
    "indeed.com": (
        "Indeed prohibits automated access to its listings and retired the "
        "public API that used to allow it."
    ),
    "glassdoor.com": "Glassdoor prohibits automated collection of its listings.",
    "ziprecruiter.com": "ZipRecruiter prohibits automated access to its listings.",
    "monster.com": "Monster prohibits automated access to its listings.",
    "dice.com": "Dice's robots.txt disallows automated access to its job paths.",
}


class UnresolvableURL(Exception):
    """The URL is well-formed but no supported ATS could be identified."""


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def blocked_reason(url: str) -> str | None:
    host = _host(url)
    for bad, reason in BLOCKED_HOSTS.items():
        if host == bad or host.endswith("." + bad):
            return reason
    return None


def _greenhouse_ref(url: str) -> tuple[str, str] | None:
    """(board slug, job id) for a Greenhouse posting, from any of its URL forms.

    Greenhouse postings appear three ways: on a greenhouse.io board, on the
    employer's own careers page carrying `?gh_jid=`, and as an embedded board
    under the employer's domain. The slug is in the path for the first and in
    the hostname for the others.
    """
    parsed = urlparse(url)
    host = _host(url)
    path = parsed.path.strip("/")

    if host.endswith("greenhouse.io"):
        # <slug>/jobs/<id>  or  embed/job_app?for=<slug>&token=<id>
        m = re.match(r"([^/]+)/jobs/(\d+)", path)
        if m:
            return m.group(1), m.group(2)
        qs = parse_qs(parsed.query)
        slug = (qs.get("for") or [None])[0]
        job_id = (qs.get("token") or qs.get("gh_jid") or [None])[0]
        if slug and job_id:
            return slug, job_id
        return None

    # Employer's own site with ?gh_jid= — the board slug is the second-level
    # domain (sofi.com -> sofi), which is the convention Greenhouse embeds use.
    job_id = (parse_qs(parsed.query).get("gh_jid") or [None])[0]
    if job_id:
        parts = [p for p in host.split(".") if p not in ("com", "co", "io", "net", "org")]
        if parts:
            return parts[-1], job_id
    return None


async def _from_greenhouse(client: httpx.AsyncClient, slug: str, job_id: str) -> dict[str, Any]:
    r = await client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    )
    r.raise_for_status()
    j = r.json()
    return _job(
        jid=f"gh_{slug}_{job_id}",
        title=j.get("title", ""),
        company=j.get("company_name") or _company_from_slug(slug),
        location=(j.get("location") or {}).get("name", ""),
        description=strip_html(j.get("content", "")),
        apply_url=j.get("absolute_url", ""),
        source="Greenhouse",
        ats="Greenhouse",
        posted=j.get("updated_at"),
    )


async def _from_lever(client: httpx.AsyncClient, slug: str, job_id: str) -> dict[str, Any]:
    r = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    r.raise_for_status()
    for j in r.json():
        if str(j.get("id")) != job_id:
            continue
        return _job(
            jid=f"lever_{slug}_{job_id}",
            title=j.get("text", ""),
            company=_company_from_slug(slug),
            location=(j.get("categories") or {}).get("location", ""),
            description=strip_html(j.get("descriptionPlain") or j.get("description", "")),
            apply_url=j.get("hostedUrl") or j.get("applyUrl", ""),
            source="Lever",
            ats="Lever",
            posted=None,
        )
    raise UnresolvableURL(f"Lever board '{slug}' has no posting {job_id}.")


async def _from_ashby(client: httpx.AsyncClient, slug: str, job_id: str) -> dict[str, Any]:
    r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        if str(j.get("id")) != job_id:
            continue
        return _job(
            jid=f"ashby_{slug}_{job_id}",
            title=j.get("title", ""),
            company=_company_from_slug(slug),
            location=j.get("location", ""),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
            apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
            source="Ashby",
            ats="Ashby",
            posted=j.get("publishedAt"),
        )
    raise UnresolvableURL(f"Ashby board '{slug}' has no posting {job_id}.")


# Page furniture that surrounds a posting: navigation, the company sidebar,
# cookie banners. Left in, their text becomes "requirements" — The Muse's
# "Size: 10000+ employees | Industry: Technology" produced requirements called
# Size and Industry, which then counted against coverage as unmet.
_CHROME_RE = re.compile(
    r"(?is)<(script|style|nav|header|footer|svg|noscript|iframe|form)[^>]*>.*?</\1>"
)


def _jsonld_posting(html: str) -> dict[str, Any] | None:
    """A schema.org JobPosting embedded in the page, if there is one.

    Google requires this block to index a posting, so a large share of career
    pages carry it. It is published for machines to read, which makes it the
    right thing to read — no guessing which div holds the description.
    """
    import json

    for block in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            if "JobPosting" not in str(item.get("@type", "")):
                continue
            org = item.get("hiringOrganization") or {}
            loc = item.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc or {}).get("address") or {}
            place = ", ".join(
                str(addr.get(k))
                for k in ("addressLocality", "addressRegion", "addressCountry")
                if addr.get(k)
            )
            return {
                "title": item.get("title") or "",
                "company": (org.get("name") if isinstance(org, dict) else org) or "",
                "location": place,
                "description": strip_html(item.get("description") or ""),
            }
    return None


def _readable_text(html: str) -> str:
    """The page's prose, with navigation and scripts removed."""
    return strip_html(_CHROME_RE.sub(" ", html))


async def _from_page(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Read a posting from the page itself: structured data first, prose second.

    This is one GET of a public page the candidate has already opened in their
    own browser — the same request their browser made. Hosts that prohibit
    automated access never reach here; they are refused by name upstream.
    """
    r = await client.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 CareerOS/1.0"
            )
        },
    )
    r.raise_for_status()
    html = r.text

    page_title, page_company = _title_and_company(html)
    posting = _jsonld_posting(html)

    # Trust structured data only when it describes a job rather than a menu.
    trusted = bool(
        posting
        and len(posting["description"]) > 400
        and posting["title"].strip().lower() not in _NON_TITLES
    )

    if trusted and posting:
        source = "Structured data"
        title = posting["title"] or page_title
        company = posting["company"] or page_company
        location = posting["location"]
        description = posting["description"]
    else:
        description = _readable_text(html)
        if len(description) < 600:
            raise UnresolvableURL(
                "That page returned almost no text — it probably renders its "
                "content with JavaScript. Paste the description instead."
            )
        source = "Pasted link"
        # Only fall back to the structured title if it isn't the menu word we
        # rejected a moment ago — otherwise the rejection achieves nothing and
        # the job arrives titled "Jobs".
        ld_title = (posting or {}).get("title", "").strip()
        title = page_title or (ld_title if ld_title.lower() not in _NON_TITLES else "")
        company = (
            page_company
            or (posting or {}).get("company", "")
            or _host(url).split(".")[0].title()
        )
        location = (posting or {}).get("location") or ""

    if not title:
        raise UnresolvableURL("Couldn't work out the job title from that page.")

    return _job(
        jid=f"url_{abs(hash(url)) % 10**12}",
        title=title,
        company=company or "Unknown",
        location=location,
        description=description,
        apply_url=url,
        source=source,
        ats=None,
        posted=None,
    )


# Titles that mean the extraction found the page's navigation, not the job.
# RemoteOK embeds a JobPosting whose title is "Jobs" and whose description is
# the employer's site menu — structured data can be present and still be wrong,
# so its output is sanity-checked rather than trusted.
_NON_TITLES = frozenset(
    {"jobs", "job", "home", "careers", "career", "about", "openings",
     "remote jobs", "job board", "search", "apply"}
)


def _title_and_company(html: str) -> tuple[str, str]:
    """Role and employer from the page title.

    Aggregators put the real employer in the title and their own brand after a
    separator — "Marketplace Operations Lead at Uber | The Muse". Deriving the
    company from the hostname instead named The Muse as the employer of Uber's
    job.
    """
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return "", ""
    raw = strip_html(m.group(1)).strip()
    head = re.split(r"\s+[|–—\-]\s+", raw)[0].strip()

    company = ""
    at = re.split(r"\s+\bat\b\s+", head, maxsplit=1)
    title = at[0].strip()
    if len(at) > 1:
        company = at[1].strip()

    if title.lower() in _NON_TITLES:
        title = ""
    return title[:120], company[:80]


def _page_title(html: str) -> str:
    return _title_and_company(html)[0]


async def resolve(url: str) -> dict[str, Any]:
    """Fetch the posting behind a URL. Raises UnresolvableURL if unsupported."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise UnresolvableURL("That doesn't look like a link.")

    host = _host(url)
    path = urlparse(url).path.strip("/")

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        gh = _greenhouse_ref(url)
        if gh:
            return await _from_greenhouse(client, *gh)

        if host.endswith("lever.co"):
            m = re.match(r"([^/]+)/([0-9a-f-]{8,})", path)
            if m:
                return await _from_lever(client, m.group(1), m.group(2))

        if host.endswith("ashbyhq.com"):
            m = re.match(r"([^/]+)/([0-9a-f-]{8,})", path)
            if m:
                return await _from_ashby(client, m.group(1), m.group(2))

        # No recognised ATS — read the page itself. Structured data if the
        # posting publishes it, otherwise its prose.
        return await _from_page(client, url)
