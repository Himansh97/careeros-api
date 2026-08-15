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
    "spacex", "databricks", "stripe", "datadog", "mongodb", "cloudflare", "brex",
    "verkada", "samsara", "elastic", "fivetran", "affirm", "gitlab", "lyft",
    "coinbase", "figma", "twilio", "reddit", "flexport", "asana", "robinhood",
    "instacart", "postman", "nuro", "gusto", "vercel", "sigmacomputing", "faire",
    "duolingo", "carta", "sofi", "mercury", "chime", "discord", "dropbox",
    "collibra", "betterment", "amplitude", "webflow", "airtable", "starburst",
    "calendly", "dremio",
]

HTTP_TIMEOUT_SECONDS = 20.0

# How many jobs get full evidence-based scoring on a search. Scoring parses a
# whole description (~3.5ms each), so the ~3,000 jobs the sources return would
# cost ~11s — too slow for an interactive request. Titles are pre-ranked first
# (see prescreen.py) so this budget is spent on plausible roles rather than on
# whichever jobs happened to be fetched first.
SCORE_BUDGET = 600
CACHE_TTL_SECONDS = 900  # 15 minutes
