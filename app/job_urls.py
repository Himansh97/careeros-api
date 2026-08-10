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

    raise UnresolvableURL(
        "That link isn't on a job board with a public API I can read "
        "(Greenhouse, Lever or Ashby). Paste the job description text instead "
        "and it will be scored exactly the same way."
    )
