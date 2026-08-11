"""Cheap title-based pre-ranking, run before full evidence scoring.

Full scoring parses a whole job description against the evidence library. At
~3.5ms a job that is fine for one posting and far too slow for the ~3,000 the
sources return, so search used to score only the first 120 in fetch order and
sort those by fit.

That produced a ranked list which was not actually a ranking: a SoFi Mortgage
Compliance Analyst scoring 98 sat at position 452 in the pool and was never
scored at all, while "Account Executive" and "Data Center Chiller Serviceman"
led the results because they happened to be fetched early.

This module closes that gap. Titles are short, so ranking every job by title
costs milliseconds, and the deep scorer then runs on the most plausible
candidates rather than an arbitrary prefix. It decides only what is worth
scoring — it never contributes to the score a job is reported with.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .profile import CandidateProfile

# Role words that describe what the candidate actually does. A title carrying
# one of these is worth the cost of full scoring.
ROLE_TERMS: tuple[str, ...] = (
    "analyst", "analytics", "data", "business intelligence", "reporting",
    "insights", "scientist", "consultant",
    "product manager", "project manager", "program manager",
    # "engineer" alone is deliberately NOT here. It used to be, because the
    # candidate's stated targets include "AI Engineer" — and the result was a
    # daily feed topped by Senior Fullstack Engineer (97), Forward Deployed
    # Engineer (95) and Staff Software Engineer (92). Those postings genuinely
    # mention Python, SQL, pipelines and stakeholders, so the scorer was not
    # wrong about the skills; it was wrong about the job. Only the engineering
    # titles this candidate actually targets count.
    "analytics engineer", "data engineer", "ai engineer", "ml engineer",
    "machine learning engineer", "bi engineer", "business systems",
)

# Titles that reliably do not fit this candidate regardless of description.
# Sales and field-service roles score well on generic requirement extraction
# ("communication", "stakeholders") without being remotely relevant, and were
# topping the unfiltered list before this existed.
NEGATIVE_TERMS: tuple[str, ...] = (
    # "account executive" alone missed "Key Accounts Executive", which reached
    # the daily feed at 92 — a sales role at the top of an analyst's list.
    "account executive", "accounts executive", "key account", "sales",
    # Creative and brand roles score well on generic requirement extraction
    # ("stakeholders", "reporting") while having nothing to do with analytics.
    "executive producer", "producer", "creative director", "art director",
    "brand creative", "content strategist", "social media",
    "recruiter", "nurse", "technician",
    "serviceman", "driver", "chef", "teacher", "security guard",
    "electrician", "plumber", "welder", "custodian", "warehouse",
    "attorney", "paralegal", "designer", "copywriter",
)

# Software-engineering titles. Scored down rather than excluded outright: a
# posting like "Analytics Engineer, Backend" is a real target, and the positive
# ROLE_TERMS above will out-weigh a single penalty. A pure "Senior Fullstack
# Engineer" collects no positive term at all and drops out of contention.
SOFTWARE_TERMS: tuple[str, ...] = (
    "fullstack", "full stack", "full-stack", "backend", "back-end", "back end",
    "frontend", "front-end", "front end", "software engineer", "swe",
    "forward deployed", "infrastructure engineer", "platform engineer",
    "site reliability", "devops engineer", "security engineer",
    "mobile engineer", "ios ", "android ", "web engineer", "systems engineer",
    "solutions architect", "cloud engineer", "network engineer",
)

# Seniority the candidate cannot hold at 3 years, or is past.
SENIORITY_PENALTY: tuple[tuple[str, int], ...] = (
    ("chief ", -8), ("vice president", -8), ("head of", -6), ("director", -6),
    ("principal", -4), ("distinguished", -6), ("intern", -4), ("apprentice", -4),
)

# Words inside an industry string that carry no discriminating signal.
_INDUSTRY_STOPWORDS = frozenset(
    {"and", "services", "service", "research", "academic", "higher", "solutions"}
)


@dataclass(frozen=True)
class ScreenTerms:
    """Everything the prescreen matches against, computed once per search.

    Previously these were derived inside the per-job loop via
    `profile.all_skills`, which is a property that rebuilds its set from the
    evidence file on every access — roughly 3,000 rebuilds per search, and the
    dominant cost of the whole request.
    """

    target_roles: tuple[str, ...]
    skills: tuple[str, ...]
    domains: tuple[str, ...]


def build_terms(profile: CandidateProfile) -> ScreenTerms:
    roles = tuple(
        str(r).lower().split("(")[0].strip()
        for r in (profile.preferences.get("target_roles") or [])
        if str(r).strip()
    )

    # Only multi-character skills that could plausibly appear in a title.
    skills = tuple(
        sorted({s for s in profile.all_skills if 3 <= len(s) <= 24})
    )

    # Industry words from the evidence library. This is what the title-only
    # version missed: "Mortgage Compliance Analyst" earns its 98 largely from
    # financial-services/mortgage domain overlap, which lives in the evidence
    # industries rather than in any skill or role name.
    domains: set[str] = set()
    for claim in profile.evidence:
        for word in re.split(r"[^a-z]+", claim.industry.lower()):
            if len(word) > 3 and word not in _INDUSTRY_STOPWORDS:
                domains.add(word)

    return ScreenTerms(roles, skills, tuple(sorted(domains)))


def prescreen_score(job: dict, terms: ScreenTerms) -> int:
    """Rank a job by title alone. Higher means more worth deep-scoring."""
    title = (job.get("title") or "").lower()
    if not title:
        return 0

    score = 0
    for term in ROLE_TERMS:
        if term in title:
            score += 3

    # An exact target-role match is the strongest signal available. These come
    # from job_preferences.yaml — the candidate's own stated targets.
    for role in terms.target_roles:
        if role and role in title:
            score += 6

    # The candidate's own industries. A domain match plus any analyst-shaped
    # role word is the pattern behind their strongest real matches.
    for domain in terms.domains:
        if domain in title:
            score += 5

    # Skills named in the title itself ("SQL Analyst", "Power BI Developer").
    for skill in terms.skills:
        if skill in title:
            score += 2

    for term in NEGATIVE_TERMS:
        if term in title:
            score -= 8

    for term in SOFTWARE_TERMS:
        if term in title:
            score -= 7
            break   # one penalty per title, not one per matching word

    for term, penalty in SENIORITY_PENALTY:
        if term in title:
            score += penalty

    return score


def rank_for_scoring(
    jobs: list[dict], profile: CandidateProfile, budget: int
) -> tuple[list[dict], int]:
    """Return the jobs worth deep-scoring, plus how many were set aside.

    Ties keep their original order, so a job is never reordered arbitrarily —
    within the same prescreen score, fetch order (which interleaves sources)
    still applies.
    """
    if len(jobs) <= budget:
        return jobs, 0

    terms = build_terms(profile)
    ranked = sorted(
        enumerate(jobs), key=lambda pair: (-prescreen_score(pair[1], terms), pair[0])
    )
    kept = [job for _, job in ranked[:budget]]
    return kept, len(jobs) - budget
