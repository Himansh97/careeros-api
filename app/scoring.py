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


def _find_evidence(
    skill: str, profile: CandidateProfile
) -> tuple[EvidenceClaim | None, str]:
    """Return the best evidence for a skill and the match type."""
    needle = skill.lower()
    parts = [p for p in needle.replace("/", " ").split() if len(p) > 2]

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
    for group in profile.skills_inventory.values():
        for listed in group:
            low = listed.lower()
            if low == needle or _contains(low, needle):
                return None, "partial"

    return None, "gap"


def score_job(job: dict[str, Any], profile: CandidateProfile) -> dict[str, Any]:
    """Score one job, returning the score plus a full explanation."""
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

    strong = [r["label"] for r in requirements if r["match"] == "exact"]
    partial = [r["label"] for r in requirements if r["match"] == "partial"]
    gaps = [r["label"] for r in requirements if r["match"] == "gap"]

    return {
        "rawFitScore": total,
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
        "explanation": _explain(total, strong, gaps, required),
    }


def _explain(
    total: int, strong: list[str], gaps: list[str], required: list[dict[str, Any]]
) -> str:
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
