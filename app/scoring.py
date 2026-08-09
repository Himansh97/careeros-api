"""Deterministic fit scoring against the candidate's real, verified evidence.

No language model is involved, by design. Scoring works by matching job
requirements against `career_evidence.json` — so a claim can only be credited
if a real, sourced accomplishment backs it. A requirement with no backing
evidence is reported as a gap, never smoothed over.
"""
from __future__ import annotations

import re
from typing import Any

from .profile import CandidateProfile, EvidenceClaim
from .skills import extract_requirements


def _contains(haystack: str, needle: str) -> bool:
    """Word-boundary containment.

    Plain substring matching produced false positives that inflated scores:
    the skill "Go" matched inside "goals" and "algorithms", and "R" matched
    almost everything. A credited match must be a real word occurrence.
    """
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


# Owning a tool implies the practice it exists for. Kept deliberately tiny and
# explicit: each entry is an implication that holds unconditionally, and it only
# ever yields a *partial* match, because the tool is evidenced while the specific
# deliverable is not. Anything requiring a judgement call stays out — this table
# is the obvious place for fabrication to creep in.
TOOL_IMPLIES: dict[str, tuple[str, ...]] = {
    "dashboarding": ("tableau", "power bi"),
    "data visualization": ("tableau", "power bi"),
}

_STEM_SUFFIXES = ("ing", "es", "s")


def _stem(word: str) -> str:
    """Crude suffix strip so 'dashboarding' can meet 'dashboard'."""
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _find_evidence(
    skill: str, profile: CandidateProfile
) -> tuple[EvidenceClaim | None, str]:
    """Return the best evidence for a skill and the match type."""
    needle = skill.lower()
    parts = [p for p in needle.replace("/", " ").split() if len(p) > 2]

    # Industry experience is stated on every claim but was never consulted, so
    # a posting asking for financial-services background scored it a gap while
    # the candidate's current employer is a mortgage lender.
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        industry = claim.industry.lower()
        if _contains(industry, needle) or (
            parts and all(_contains(industry, p) for p in parts)
        ):
            return claim, "exact"

    # Exact: the skill name appears in a claim's declared skills.
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        for declared in claim.skills:
            if declared.lower() == needle:
                return claim, "exact"

    # Exact: the skill name appears verbatim in the claim text.
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        if _contains(claim.claim.lower(), needle):
            return claim, "exact"

    # Partial: every meaningful word of the skill appears somewhere in a claim.
    if parts:
        for claim in profile.evidence:
            if not claim.approved_for_resume:
                continue
            haystack = claim.claim.lower() + " " + " ".join(claim.skills).lower()
            if all(_contains(haystack, p) for p in parts):
                return claim, "partial"

    # Partial: listed in the profile's skills inventory but with no evidence
    # bullet behind it — real, but weaker than a demonstrated accomplishment.
    inventory = [s.lower() for group in profile.skills_inventory.values() for s in group]
    for listed in inventory:
        if listed == needle or _contains(listed, needle):
            return None, "partial"

    # Partial: a stemmed form matches ('dashboarding' vs a declared 'dashboard').
    stem = _stem(needle)
    if stem != needle:
        for claim in profile.evidence:
            if not claim.approved_for_resume:
                continue
            haystack = claim.claim.lower() + " " + " ".join(claim.skills).lower()
            if _contains(haystack, stem):
                return claim, "partial"

    # Partial: the candidate owns a tool this practice is performed with.
    for tool in TOOL_IMPLIES.get(needle, ()):
        if any(tool in listed for listed in inventory):
            return None, "partial"

    return None, "gap"


# Memoised scores, keyed by job id. Scoring is deterministic — same job, same
# profile, same result — and a search re-scores its whole candidate set on
# every filter change, so this turns repeat browsing from ~3s into ~0.
#
# The cache is tied to a specific profile OBJECT, and a reference to it is held
# alongside, so the entry can only be reused while that exact profile is still
# in play. Editing the evidence file reloads the profile, which replaces the
# object and drops every stale score with it.
_score_cache: dict[str, dict[str, Any]] = {}
_score_cache_profile: CandidateProfile | None = None


def score_job_cached(job: dict[str, Any], profile: CandidateProfile) -> dict[str, Any]:
    """score_job, memoised per job id for the lifetime of a loaded profile."""
    global _score_cache_profile

    if _score_cache_profile is not profile:
        _score_cache.clear()
        _score_cache_profile = profile

    job_id = job.get("id") or ""
    if not job_id:
        return score_job(job, profile)

    hit = _score_cache.get(job_id)
    if hit is None:
        hit = score_job(job, profile)
        _score_cache[job_id] = hit
    return hit


def score_job(job: dict[str, Any], profile: CandidateProfile) -> dict[str, Any]:
    """Score one job, returning the score plus a full explanation.

    Eligibility is evaluated first. A role the candidate legally cannot hold
    is capped and flagged rather than being allowed to top the rankings on
    skill match alone.
    """
    from .eligibility import check_eligibility

    eligibility = check_eligibility(job, profile)
    reqs = extract_requirements(job.get("description", ""))

    requirements: list[dict[str, Any]] = []
    for idx, (skill, is_required) in enumerate(reqs):
        claim, match = _find_evidence(skill, profile)
        requirements.append(
            {
                "id": f"r{idx}",
                "label": skill,
                "importance": "required" if is_required else "preferred",
                "match": match,
                "evidence": claim.claim if claim else None,
                "source": claim.source if claim else None,
            }
        )

    required = [r for r in requirements if r["importance"] == "required"]
    preferred = [r for r in requirements if r["importance"] == "preferred"]

    def coverage(items: list[dict[str, Any]]) -> float:
        if not items:
            return 1.0
        earned = sum(
            1.0 if r["match"] == "exact" else 0.5 if r["match"] == "partial" else 0.0
            for r in items
        )
        return earned / len(items)

    req_cov = coverage(required)
    pref_cov = coverage(preferred)

    # Weighted 100-point model. Mandatory coverage dominates, as it should.
    mandatory_pts = req_cov * 45
    technical_pts = coverage(requirements) * 20
    preferred_pts = pref_cov * 10
    evidence_pts = min(len([r for r in requirements if r["match"] == "exact"]) / 6, 1.0) * 15
    # Logistics: no evidence of a hard conflict is worth acknowledging, but it
    # is not proof of eligibility — hence partial credit, not full.
    logistics_pts = 10

    total = round(mandatory_pts + technical_pts + preferred_pts + evidence_pts + logistics_pts)
    total = max(0, min(100, total))

    # An ineligible role must never outrank one the candidate can actually
    # take. Skill fit is preserved separately so the reason stays legible.
    skill_score = total
    if eligibility["verdict"] == "INELIGIBLE":
        total = 0
    elif eligibility["verdict"] == "REVIEW_REQUIRED":
        total = min(total, 60)

    strong = [r["label"] for r in requirements if r["match"] == "exact"]
    partial = [r["label"] for r in requirements if r["match"] == "partial"]
    gaps = [r["label"] for r in requirements if r["match"] == "gap"]

    return {
        "rawFitScore": total,
        "skillScore": skill_score,
        "eligibility": eligibility,
        "requirements": requirements,
        "strongMatches": strong,
        "partialMatches": partial,
        "gaps": gaps,
        "matchBreakdown": {
            "overall": total,
            "mandatory": round(req_cov * 100),
            "technical": round(coverage(requirements) * 100),
            "experience": round(min(len(strong) / 6, 1.0) * 100),
            "domain": round(pref_cov * 100),
            "education": 100,
            "logistics": 100,
        },
        "explanation": _explain(total, strong, gaps, required, eligibility, skill_score),
    }


def _explain(
    total: int,
    strong: list[str],
    gaps: list[str],
    required: list[dict[str, Any]],
    eligibility: dict[str, Any] | None = None,
    skill_score: int | None = None,
) -> str:
    if eligibility and eligibility["verdict"] == "INELIGIBLE":
        reasons = "; ".join(b["detail"] for b in eligibility["blockers"])
        return (
            f"INELIGIBLE — {reasons} Skills would otherwise score "
            f"{skill_score}/100, but no resume changes overcome this."
        )
    if eligibility and eligibility["verdict"] == "REVIEW_REQUIRED":
        reasons = "; ".join(w["detail"] for w in eligibility["warnings"])
        return f"Capped pending review — {reasons} Skill match alone: {skill_score}/100."

    missing_required = [r["label"] for r in required if r["match"] == "gap"]
    bits = [f"Scored {total}/100 from {len(strong)} directly evidenced requirement(s)."]
    if missing_required:
        bits.append(
            "Missing required: " + ", ".join(missing_required[:5]) + "."
        )
    elif gaps:
        bits.append("Preferred-only gaps: " + ", ".join(gaps[:5]) + ".")
    else:
        bits.append("No unbacked requirements found.")
    return " ".join(bits)
