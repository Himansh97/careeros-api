"""What people actually report about a company's interview process.

The interview pack deliberately refused to include company research, on the
grounds that there was no source for it and repeating an invented company fact
to someone who works there is worse than saying nothing. That reasoning holds —
what changes it is having a real source.

This stores researched intel: the rounds a company runs, the kinds of question
reported, what candidates say gets people rejected, and what to prepare. Every
record carries the URLs it came from and the date it was gathered.

Three rules make the difference between research and fabrication:

* **Nothing is generated.** A company nobody has researched returns "not
  researched", never a plausible-looking list of questions. Generic interview
  advice dressed as company-specific intel is the exact failure this codebase
  exists to avoid, and it is worse here than elsewhere because the candidate
  would walk into a room and rely on it.
* **Everything carries its sources.** A claim about a hiring process is only as
  good as where it came from, and the candidate needs to weigh a first-hand
  report differently from an SEO guide.
* **Age is shown.** Hiring processes change. Two-year-old intel presented as
  current is a quiet way of being wrong.

The API does no searching itself, for the same reason it holds no Gmail
credentials: an agent session with web access gathers the material, and
`scripts/import_interview_intel.py` loads it. Runtime scraping of review sites
is also not on the table — several prohibit automated access in their terms.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .store import connect

# Beyond this, a hiring process may well have changed. Not a reason to hide the
# intel — a reason to say how old it is.
STALE_AFTER_DAYS = 240

# Titles vary endlessly; processes do not. "Senior Data Analyst" and "Data
# Analyst II" meet the same loop, so intel is keyed on the family.
_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data-engineer", ("data engineer", "analytics engineer", "platform data")),
    ("data-scientist", ("data scientist", "machine learning", "research scientist")),
    # "Senior Revenue Analytics Analyst" fell through every needle to "general"
    # because "revenue analyst" does not appear in it. Analytics-shaped analyst
    # titles are endlessly varied; match the shape, not the exact phrase.
    ("data-analyst", ("data analyst", "business intelligence", "bi developer",
                      "reporting analyst", "insights analyst", "analytics analyst",
                      "revenue analytics", "analytics manager", "data & reporting")),
    ("product-manager", ("product manager", "product owner")),
    ("business-analyst", ("business analyst", "business systems", "operations analyst",
                          "business operations")),
    ("finance-analyst", ("financial analyst", "fp&a", "revenue analyst",
                         "sec reporting", "accounting")),
    ("product-analyst", ("product analyst",)),
)


def role_family(title: str) -> str:
    """Map a job title onto the loop it will actually meet."""
    low = (title or "").lower()
    for family, needles in _FAMILIES:
        if any(n in low for n in needles):
            return family
    return "general"


def _key(company: str, family: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-")
    return f"{slug}:{family}"


def _age_days(stamp: str) -> int:
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - when).total_seconds() // 86400))


def save_intel(
    company: str,
    family: str,
    payload: dict[str, Any],
    sources: list[dict[str, str]],
    researched_at: str | None = None,
) -> str:
    """Store researched intel. Sources are required, not optional."""
    if not sources:
        raise ValueError(
            "interview intel must carry its sources — an unsourced claim about a "
            "hiring process is indistinguishable from a guess"
        )
    now = datetime.now(timezone.utc).isoformat()
    ident = _key(company, family)
    with connect() as conn:
        conn.execute(
            """INSERT INTO interview_intel
               (id, company, role_family, payload, sources, researched_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 payload=excluded.payload, sources=excluded.sources,
                 researched_at=excluded.researched_at, updated_at=excluded.updated_at""",
            (
                ident,
                company,
                family,
                json.dumps(payload),
                json.dumps(sources),
                researched_at or now,
                now,
            ),
        )
    return ident


def get_intel(company: str, title: str) -> dict[str, Any]:
    """Intel for this company and role family, or an honest absence."""
    family = role_family(title)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM interview_intel WHERE id=?", (_key(company, family),)
        ).fetchone()
        if row is None:
            # Fall back to any research on this company before giving up. A
            # company's loop is broadly similar across analyst families, and
            # near-miss intel clearly labelled as such beats nothing — the
            # `exactFamily` flag below is what keeps that honest.
            slug = _key(company, "")[:-1]
            row = conn.execute(
                "SELECT * FROM interview_intel WHERE id LIKE ? ORDER BY researched_at DESC LIMIT 1",
                (f"{slug}:%",),
            ).fetchone()

    if row is None:
        return {
            "researched": False,
            "company": company,
            "roleFamily": family,
            "note": (
                f"No interview research on file for {company}. Nothing is generated "
                "here — a plausible list of questions you had not actually verified "
                "would be worse than none, because you would rely on it in the room."
            ),
        }

    age = _age_days(row["researched_at"])
    return {
        "researched": True,
        "company": row["company"],
        "roleFamily": row["role_family"],
        "exactFamily": row["role_family"] == family,
        "researchedAt": row["researched_at"],
        "ageDays": age,
        "stale": age > STALE_AFTER_DAYS,
        "sources": json.loads(row["sources"]),
        **json.loads(row["payload"]),
    }


def list_intel() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT company, role_family, researched_at FROM interview_intel "
            "ORDER BY company"
        ).fetchall()
    return [
        {
            "company": r["company"],
            "roleFamily": r["role_family"],
            "researchedAt": r["researched_at"],
            "ageDays": _age_days(r["researched_at"]),
        }
        for r in rows
    ]
