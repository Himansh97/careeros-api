"""Recent movement in tech and AI, from public feeds, cached per source.

Three feeds, none of which needs an API key or an account:

* **Hacker News** — the front page, which is the fastest-moving signal of what
  engineers are actually paying attention to today.
* **arXiv** — new cs.AI and cs.LG submissions, which is where model and method
  advances land days or weeks before anyone writes them up.
* **GitHub** — repositories created in the last week that have already gathered
  stars, which catches tooling on the way up rather than after it is famous.

Proxied rather than fetched from the browser for the same reasons as
`skywatch`: one cache serves every page load instead of every visitor hammering
three APIs, and CORS stops being something third parties decide for us.

**Headlines are shown, never summarised.** There is deliberately no model
anywhere in this path. A summariser costs money on every refresh and can
misdescribe a paper it half-read, and a ticker that quietly gets a result
backwards is worse than one that shows a title and a link. What is displayed is
what the source published, and clicking it goes to the source.

**A failed feed is reported, never faked** — the rule the rest of this system
runs on. Each source carries its own TTL and its own error state, so an arXiv
outage removes the papers and says so rather than holding last week's and
presenting them as current.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Per-source TTLs, set by how fast the underlying thing actually changes. The
# HN front page reorders continuously; arXiv publishes in a daily batch; a
# week-old repository's star count is not news at ten-minute resolution.
_TTL = {"hackernews": 600.0, "arxiv": 21600.0, "github": 10800.0}

_cache: dict[str, tuple[float, Any]] = {}

_TIMEOUT = 12.0

# How many HN front-page items to resolve. Each id costs one small request, so
# this is the one place where the item count is a real cost rather than a
# display choice.
_HN_DEPTH = 45
_HN_KEEP = 25

# Topic tagging is keyword matching, not classification. It exists to colour a
# chip, and a wrong chip on a correct headline is a cosmetic bug; a model here
# would be a running cost and a new way to be wrong about someone's work.
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI", ("ai", "llm", "gpt", "claude", "gemini", "model", "neural", "transformer",
            "diffusion", "agent", "inference", "rag", "embedding", "openai",
            "anthropic", "deepmind", "mistral", "llama", "fine-tun", "prompt")),
    ("Data", ("data", "sql", "warehouse", "duckdb", "postgres", "spark", "pandas",
              "analytics", "etl", "pipeline", "dbt", "iceberg", "parquet", "olap")),
    ("Infra", ("kubernetes", "docker", "rust", "compiler", "database", "kernel",
               "distributed", "cache", "latency", "server", "cloud", "wasm")),
    ("Fintech", ("payment", "stripe", "bank", "mortgage", "fraud", "ledger",
                 "trading", "fintech", "compliance", "regulat")),
)


_TOPIC_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (label, re.compile(r"\b(?:" + "|".join(re.escape(n) for n in needles) + r")"))
    for label, needles in _TOPICS
)


def _fresh(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL[key]:
        return hit[1]
    return None


def _topic(text: str) -> str:
    """Match on word starts, not substrings.

    Plain `in` tagged "Google replaced Git tags for cert-ai-n source code" as
    AI. Anchoring to a word boundary keeps the short needles ("ai", "rag",
    "gpt") usable without them matching the inside of ordinary words, while
    still letting prefixes like "regulat" catch "regulatory".
    """
    low = (text or "").lower()
    for label, pattern in _TOPIC_PATTERNS:
        if pattern.search(low):
            return label
    return "Tech"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- the feeds


async def _hackernews(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The front page, resolved from ids to items."""
    r = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
    r.raise_for_status()
    ids = (r.json() or [])[:_HN_DEPTH]

    async def one(item_id: int) -> dict[str, Any] | None:
        resp = await client.get(
            f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
        )
        resp.raise_for_status()
        return resp.json()

    # A single dead item must not cost the whole feed, so these are gathered
    # with exceptions returned and skipped individually.
    raw = await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)

    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Exception) or not isinstance(item, dict):
            continue
        title = item.get("title")
        if not title or item.get("dead") or item.get("deleted"):
            continue
        # Self-posts have no url; link to the discussion instead of dropping a
        # story that is often the most interesting thing on the page.
        url = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"
        points = item.get("score") or 0
        out.append({
            "id": f"hn-{item['id']}",
            "title": title,
            "url": url,
            "source": "Hacker News",
            "topic": _topic(title),
            "at": _iso(item.get("time") or time.time()),
            "meta": f"{points} points",
            "_rank": points,
        })
    # Rank by points to pick the top stories, then drop the sort key so every
    # source returns the same item shape.
    out.sort(key=lambda i: -i["_rank"])
    return [{k: v for k, v in i.items() if k != "_rank"} for i in out[:_HN_KEEP]]


async def _arxiv(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """New cs.AI and cs.LG submissions, newest first."""
    r = await client.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": "cat:cs.AI OR cat:cs.LG",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": "15",
        },
    )
    r.raise_for_status()

    # Atom, not JSON. stdlib rather than a new dependency on a feed parser --
    # this is the only XML in the codebase and it is not worth a package.
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)

    out: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        link_el = entry.find("a:id", ns)
        when_el = entry.find("a:published", ns)
        if title_el is None or link_el is None or title_el.text is None:
            continue
        # arXiv wraps titles across lines; a ticker needs one line.
        title = " ".join(title_el.text.split())
        category = entry.find("a:category", ns)
        primary = category.get("term") if category is not None else "cs.AI"
        out.append({
            "id": f"arxiv-{(link_el.text or '').rsplit('/', 1)[-1]}",
            "title": title,
            "url": link_el.text,
            "source": "arXiv",
            "topic": "AI",
            "at": (when_el.text if when_el is not None else _iso(time.time())),
            "meta": primary,
        })
    return out


async def _github(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Repositories created in the last week that already have traction."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    r = await client.get(
        "https://api.github.com/search/repositories",
        params={
            "q": f"created:>{since} stars:>50",
            "sort": "stars",
            "order": "desc",
            "per_page": "15",
        },
        headers={"Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()

    out: list[dict[str, Any]] = []
    for repo in (r.json().get("items") or []):
        name = repo.get("full_name")
        if not name:
            continue
        description = repo.get("description") or ""
        stars = repo.get("stargazers_count") or 0
        out.append({
            "id": f"gh-{repo.get('id')}",
            # The name alone says nothing; the description is what makes it
            # scannable in a strip moving past at reading speed.
            "title": f"{name} — {description}" if description else name,
            "url": repo.get("html_url"),
            "source": "GitHub",
            "topic": _topic(f"{name} {description}"),
            "at": repo.get("created_at") or _iso(time.time()),
            "meta": f"{stars:,} stars",
        })
    return out


# ------------------------------------------------------------------ assembly

_FETCHERS = {"hackernews": _hackernews, "arxiv": _arxiv, "github": _github}

_SOURCES = {
    "hackernews": "Hacker News front page",
    "arxiv": "arXiv cs.AI and cs.LG, newest submissions",
    "github": "GitHub repositories created in the last 7 days",
}


async def newsfeed() -> dict[str, Any]:
    """Every feed, each cached separately, with failures named."""
    collected: dict[str, list[dict[str, Any]]] = {}
    failures: list[str] = []

    wanted = []
    for key in _FETCHERS:
        cached = _fresh(key)
        if cached is not None:
            collected[key] = cached
        else:
            wanted.append(key)

    if wanted:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": "CareerOS/1.0"}
        ) as client:
            done = await asyncio.gather(
                *(_FETCHERS[k](client) for k in wanted), return_exceptions=True
            )
        for key, value in zip(wanted, done):
            if isinstance(value, Exception):
                # Named, not swallowed. The strip prints which feed is missing
                # rather than quietly showing a shorter list.
                logger.warning("newsfeed: %s failed: %s", key, value)
                failures.append(key)
                continue
            _cache[key] = (time.time(), value)
            collected[key] = value

    items: list[dict[str, Any]] = []
    for key in _FETCHERS:
        items.extend(collected.get(key) or [])
    # Newest first. Sources disagree about precision, so this is a best effort
    # over ISO strings rather than a claim about exact ordering.
    items.sort(key=lambda i: str(i.get("at") or ""), reverse=True)

    return {
        "items": items,
        "failures": failures,
        "readAt": _iso(time.time()),
        "sources": _SOURCES,
    }
