"""Resume tailoring by selecting and ranking real evidence.

Tailoring here means: choose which verified accomplishments to lead with, and
in what order, based on what a specific job asks for. It never rewrites a
claim into something stronger than the source, and never adds a claim that
isn't in career_evidence.json. Every bullet carries its source.
"""
from __future__ import annotations

from typing import Any

from .profile import CandidateProfile, EvidenceClaim
from .scoring import _contains


def _relevance(claim: EvidenceClaim, wanted: list[str]) -> tuple[int, list[str]]:
    """How many of the job's requirements this claim demonstrably supports."""
    haystack = (claim.claim + " " + " ".join(claim.skills)).lower()
    hits = [w for w in wanted if _contains(haystack, w.lower())]
    return len(hits), hits


def tailor_resume(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> dict[str, Any]:
    wanted = score["strongMatches"] + score["partialMatches"]

    ranked: list[tuple[int, list[str], EvidenceClaim]] = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        n, hits = _relevance(claim, wanted)
        ranked.append((n, hits, claim))
    ranked.sort(key=lambda t: -t[0])

    # Group the most relevant claims by employer, preserving relevance order.
    sections: list[dict[str, Any]] = []
    seen_employers: list[str] = []
    for n, hits, claim in ranked:
        if n == 0 and len(sections) >= 3:
            continue  # don't pad with irrelevant history
        if claim.employer not in seen_employers:
            seen_employers.append(claim.employer)
            sections.append(
                {
                    "id": claim.employer.lower().replace(" ", "-")[:40],
                    "heading": claim.employer,
                    "subheading": claim.date_range,
                    "bullets": [],
                }
            )
        section = next(s for s in sections if s["heading"] == claim.employer)
        section["bullets"].append(
            {
                "id": claim.claim_id,
                "text": claim.claim,
                "changeType": "reordered" if n > 0 else "unchanged",
                "whyChanged": (
                    f"Prioritized — directly supports {', '.join(hits[:4])}."
                    if hits
                    else None
                ),
                "evidence": {
                    "source": f"{claim.source} — {claim.claim_id}",
                    "verifiedStatement": claim.claim,
                    "usedToSupport": ", ".join(hits) if hits else "General background",
                },
            }
        )

    resume_score, audit = _audit(job, score, sections)

    return {
        "jobId": job["id"],
        "jobTitle": job["title"],
        "companyName": job["company"]["name"],
        "version": 1,
        "status": "ready" if resume_score >= 90 else "draft",
        "rawFitScore": score["rawFitScore"],
        "resumeScore": resume_score,
        "scoreHistory": [resume_score],
        "sections": sections,
        "audit": audit,
        "updatedAt": None,
    }


def _audit(
    job: dict[str, Any], score: dict[str, Any], sections: list[dict[str, Any]]
) -> tuple[int, dict[str, Any]]:
    reqs = score["requirements"]
    required = [r for r in reqs if r["importance"] == "required"]
    exact = [r for r in reqs if r["match"] == "exact"]
    gaps = [r for r in reqs if r["match"] == "gap"]
    missing_required = [r for r in required if r["match"] == "gap"]

    bullets = sum(len(s["bullets"]) for s in sections)

    def pct(n: float, d: float) -> float:
        return (n / d) if d else 1.0

    categories = [
        ("requirement_coverage", "Requirement coverage", 25, pct(len(exact), max(len(reqs), 1))),
        ("relevant_experience", "Relevant experience", 20, pct(bullets, 6)),
        ("technical_skills", "Technical skills", 15, pct(len(exact), max(len(required), 1))),
        ("achievements", "Achievements", 10, 0.9),
        ("readability", "Readability", 10, 1.0 if bullets <= 8 else 0.8),
        ("ats_structure", "ATS structure", 10, 1.0),
        ("keyword_alignment", "Keyword alignment", 5, pct(len(exact), max(len(reqs), 1))),
        ("education", "Education", 5, 1.0),
    ]

    scored = []
    total = 0
    for key, label, mx, ratio in categories:
        pts = int(round(min(1.0, max(0.0, ratio)) * mx))
        total += pts
        scored.append({"key": key, "label": label, "score": pts, "max": mx})

    decision = "SHORTLIST" if total >= 90 else "REVIEW" if total >= 75 else "REJECT"

    works = []
    if exact:
        works.append(
            f"{len(exact)} requirement(s) backed by sourced, verifiable accomplishments."
        )
    if bullets:
        works.append(f"{bullets} bullet(s) selected, each traceable to career evidence.")
    if not missing_required:
        works.append("Every required skill found in the posting has supporting evidence.")

    concerns = []
    if missing_required:
        concerns.append(
            "Required but unevidenced: "
            + ", ".join(r["label"] for r in missing_required[:5])
            + ". Reported as gaps rather than implied."
        )
    if gaps and not missing_required:
        concerns.append(
            "Preferred-only gaps: " + ", ".join(r["label"] for r in gaps[:5]) + "."
        )
    if not concerns:
        concerns.append("No unbacked claims detected.")

    return total, {
        "overall": total,
        "decision": decision,
        "categories": scored,
        "whatWorks": works or ["Resume assembled from verified evidence only."],
        "concerns": concerns,
    }
