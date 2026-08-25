"""Configuration and paths.

Candidate data lives in the separate `careeros` repo (data + docs, PII
gitignored). This service reads it rather than duplicating it.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Secrets (e.g. HUNTER_API_KEY) live in a gitignored .env beside this package
# so they never reach the repo or a command line that lands in shell history.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CAREEROS_DIR = Path(
    os.environ.get("CAREEROS_DIR", Path.home() / "careeros")
).expanduser()

# Resume wording only. Scoring and bullet selection stay deterministic and
# model-free — that is a stated design position, not an omission — and every
# generated sentence still has to pass `verify_override` before it can reach a
# document. See app/rewrite.py.
#
# Sonnet by default. The argument for Opus was register — whether a bullet reads
# as someone who has done the work or as someone describing it from outside —
# but that is a claim to test rather than assume, and it is cheap to test: the
# containment gate is identical either way, so the only difference is how the
# prose reads. Start on the cheaper model, keep the calibration corpus, and
# switch if the output does not hold up.
#
# Whichever model runs, it is never trusted. Every generated sentence passes
# `verify_override`, and a model that fails or is absent falls back to the
# deterministic pipeline.
#
#     ANTHROPIC_MODEL=claude-opus-5   # to compare
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def anthropic_key() -> str | None:
    """The API key, or None when the resume writer should stay rule-based.

    Returning None rather than raising is the whole contract: a missing key
    means today's deterministic resume, not a broken one. Nothing in the
    pipeline may treat the model as required.
    """
    return os.getenv("ANTHROPIC_API_KEY") or None

# The Notion API version this code is written against.
#
# Pinned deliberately, and it must stay pinned. Notion shipped at least five
# breaking or behaviour-changing versions in the first half of 2026 alone —
# pagination defaults, webhook secret validation, rate-limit header formats —
# and 2025-09-03 is itself the one that split a `database` into a container
# holding one or more `data_source`s, moving the query endpoint and changing
# what a page's parent is. An unpinned client does not fail loudly on any of
# that; it starts writing to the wrong shape.
NOTION_VERSION = "2025-09-03"

# Notion's published ceiling is "an average of three requests per second, with
# some bursts beyond the average allowed". The mirror is ~40 applications, so
# this is never the constraint — it exists so that a future caller which loops
# something larger degrades into slowness rather than a wall of 429s.
NOTION_MAX_RPS = 3


def notion_token() -> str | None:
    """The Notion integration secret, or None when the mirror is off.

    Same contract as `anthropic_key`: absent means the feature does not run,
    never that something breaks. The mirror is a convenience — a copy of the
    pipeline on a phone — and CareerOS has to work identically without it.
    """
    return os.getenv("NOTION_TOKEN") or None


def notion_data_source_id() -> str | None:
    """The data source the mirror writes rows into.

    A *data source*, not a database. Since API version 2025-09-03 a database is
    a container that holds one or more data sources, and the rows live in the
    latter — so this is the id that `/v1/data_sources/{id}/query` takes and that
    a new page names as its parent.
    """
    return os.getenv("NOTION_DATA_SOURCE_ID") or None


DB_PATH = Path(
    os.environ.get("CAREEROS_DB", Path.home() / "careeros-api" / "careeros.db")
).expanduser()

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3311",
    "http://127.0.0.1:3311",
]

# Companies whose Greenhouse boards we search. Greenhouse exposes a public,
# unauthenticated job-board API per company, so this is a real live source —
# but it only covers companies that use Greenhouse and that we list here.
# Expanding this list is the main lever on discovery coverage.
GREENHOUSE_COMPANIES: list[str] = [
    # Verified live against boards-api.greenhouse.io rather than assumed.
    # doordash and wealthsimple were removed: both had been returning HTTP
    # errors, which reads downstream as "no openings" rather than "board gone".
    # anthropic moved here from Ashby, whose board now 404s. The failure was
    # logged every morning and read as one dead source among eighty, while 491
    # open roles stayed invisible.
    "anthropic",
    "spacex", "databricks", "stripe", "datadog", "mongodb", "cloudflare", "brex",
    "verkada", "samsara", "elastic", "fivetran", "affirm", "gitlab", "lyft",
    "coinbase", "figma", "twilio", "reddit", "flexport", "asana", "robinhood",
    "instacart", "postman", "nuro", "gusto", "vercel", "sigmacomputing", "faire",
    "duolingo", "carta", "sofi", "mercury", "chime", "discord", "dropbox",
    "collibra", "betterment", "amplitude", "webflow", "airtable", "starburst",
    "calendly", "dremio",
]

HTTP_TIMEOUT_SECONDS = 20.0

# ── Handshake ────────────────────────────────────────────────────────────────
#
# Handshake is the only source here without a search endpoint. What it does
# publish is a sitemap of its public job pages — advertised in its own
# robots.txt, which allows `/public` for every user agent — and each of those
# pages carries a schema.org `JobPosting` block. That is structured data put out
# deliberately for machines to read, so it is fair game in the same way
# Greenhouse's board API is.
#
# The absence of a search endpoint is the whole cost model. There are ~59,000
# public postings and no way to ask for the analyst ones, so finding a role
# means opening the page. Two things keep that bounded:
#
#   * IDs are monotonic with `datePosted` — verified against the live feed,
#     where id 11329267 posted 2026-08-20 and id 11174249 posted 2026-07-01.
#     Sorting the sitemap descending therefore yields genuinely the newest
#     postings, so a fixed budget spent from the top is spent on fresh roles.
#   * `_handshake_seen` memoises every id already opened, so only ids that
#     appeared since the last crawl cost a request. The first crawl of a
#     process pays the full budget; the ones after it pay for the delta.
#
# The sitemap itself is ~5MB across three files and only changes daily, so it
# is cached far longer than CACHE_TTL_SECONDS.
HANDSHAKE_SITEMAP = "https://joinhandshake.com/api/handshake-public-jobs.xml"
HANDSHAKE_JOB_URL = "https://app.joinhandshake.com/public/jobs/{job_id}"
HANDSHAKE_INDEX_TTL_SECONDS = 21600.0   # 6h; the sitemap is rebuilt daily
HANDSHAKE_DETAIL_CAP = 400              # newest ids opened per crawl
HANDSHAKE_CONCURRENCY = 6               # polite; the host sets no rate limit

# Handshake's public feed is dominated by hourly retail, campus and clinical
# work — a 400-posting sample of it scored 2.3% on these terms. Every other
# source narrows server-side (Workday and SmartRecruiters take a query, the ATS
# boards are one employer each); Handshake cannot, so the narrowing happens on
# the title here instead. Without it a single crawl would push several hundred
# warehouse roles into a pool that interleaves one job per source.
HANDSHAKE_TITLE_TERMS = (
    "analyst", "analytics", "business intelligence", "data scien",
    "data engineer", "data warehouse", "reporting", "insights",
    "business systems", "product manager", "project manager", "program manager",
    "machine learning", "ai engineer", "data ",
)

# How many jobs get full evidence-based scoring on a search. Scoring parses a
# whole description (~3.5ms each), so the ~3,000 jobs the sources return would
# cost ~11s — too slow for an interactive request. Titles are pre-ranked first
# (see prescreen.py) so this budget is spent on plausible roles rather than on
# whichever jobs happened to be fetched first.
SCORE_BUDGET = 600
CACHE_TTL_SECONDS = 900  # 15 minutes

# The resume score at or above which an application is genuinely ready to send.
#
# It lives here because two places decide readiness and they disagreed:
# `tailor_resume` returned "ready" if the score cleared 80, while
# `store.set_resume_score` wrote status="ready" for every un-sent application
# regardless of the number it had just been handed. That was invisible while
# the audit was mostly constants and every score landed in the 80s and 90s.
# The moment the scoring started discriminating, resumes scoring 50 appeared in
# the list marked "Ready" — the score's one job is to say which documents are
# worth sending, and the status was contradicting it.
READY_SCORE = 80
