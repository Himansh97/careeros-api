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
    "databricks",
    "stripe",
    "airtable",
    "gitlab",
    "cloudflare",
    "affirm",
    "instacart",
    "doordash",
    "dropbox",
    "flexport",
    "reddit",
    "robinhood",
    "sofi",
    "twilio",
    "wealthsimple",
]

HTTP_TIMEOUT_SECONDS = 20.0
CACHE_TTL_SECONDS = 900  # 15 minutes
