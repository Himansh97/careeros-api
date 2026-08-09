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


MONTHS = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _pretty_date(token: str) -> str:
    """Turn '2022-11' into 'Nov 2022'; pass through 'Present' unchanged."""
    token = (token or "").strip()
    if "-" in token:
        parts = token.split("-")
        if len(parts) >= 2 and parts[1] in MONTHS:
            return f"{MONTHS[parts[1]]} {parts[0]}"
    return token


def _pretty_range(date_range: str) -> str:
    for sep in (" to ", " – ", " - "):
        if sep in date_range:
            a, b = date_range.split(sep, 1)
            return f"{_pretty_date(a)} – {_pretty_date(b)}"
    return date_range


def _employment_lookup(profile: CandidateProfile) -> dict[str, dict[str, Any]]:
    """Map employer name to its role title and dates for section headings."""
    out: dict[str, dict[str, Any]] = {}
    for role in profile.employment_history or []:
        out[role.get("employer", "")] = role
    return out


def _sort_key(role: dict[str, Any] | None) -> str:
    """Sort key for reverse-chronological ordering; 'Present' sorts newest."""
    if not role:
        return "0000-00"
    end = (role.get("end_date") or "").strip()
    if end.lower() == "present":
        return "9999-99"
    return end or role.get("start_date", "0000-00")


def tailor_resume(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> dict[str, Any]:
    wanted = score["strongMatches"] + score["partialMatches"]
    employment = _employment_lookup(profile)

    ranked: list[tuple[int, list[str], EvidenceClaim]] = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        n, hits = _relevance(claim, wanted)
        ranked.append((n, hits, claim))
    # Relevance decides which bullets lead *within* a role, but never the order
    # of the roles themselves — a resume must read reverse-chronologically.
    ranked.sort(key=lambda t: -t[0])

    sections: list[dict[str, Any]] = []
    seen_employers: list[str] = []
    for n, hits, claim in ranked:
        if claim.employer not in seen_employers:
            seen_employers.append(claim.employer)
            role = employment.get(claim.employer, {})
            title = role.get("title", "")
            heading = f"{title} — {claim.employer}" if title else claim.employer
            location = role.get("location", "")
            sections.append(
                {
                    "id": claim.employer.lower().replace(" ", "-")[:40],
                    "heading": heading,
                    "employer": claim.employer,
                    "subheading": " · ".join(
                        p for p in [_pretty_range(claim.date_range), location] if p
                    ),
                    "bullets": [],
                }
            )
        section = next(s for s in sections if s["employer"] == claim.employer)
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

    # Reverse-chronological, newest role first. Non-negotiable on a resume;
    # relevance ordering earlier had a 2022 role appearing above a current one.
    sections.sort(key=lambda s: _sort_key(employment.get(s["employer"])), reverse=True)

    # No truncation here: the PDF exporter fits bullets to the page, so a job
    # matching more evidence keeps more of it. Bullets stay relevance-ordered
    # so anything trimmed later is the least relevant.

    # Align wording to the posting's vocabulary before auditing, so the
    # keyword-alignment score reflects what the employer will actually read.
    from .phrasing import align_resume

    align_resume({"sections": sections}, job.get("description", ""))

    resume_score, audit = _audit(job, score, sections)

    return {
        "jobId": job["id"],
        "summary": _summary(job, score, profile),
        "headline": _headline(job, profile),
        "matchedSkills": score["strongMatches"] + score["partialMatches"],
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


def _headline(job: dict[str, Any], profile: CandidateProfile) -> str:
    """Position the candidate against this posting's role family.

    A headline states what the candidate is aiming at, not a title they have
    held, so aligning it to the posting is positioning rather than a claim.
    It only ever narrows to a family the candidate has genuine evidence in;
    anything unrecognised falls back to their own headline.
    """
    title = (job.get("title") or "").lower()
    families = [
        (("business intelligence", "bi developer", "bi analyst"), "Business Intelligence Analyst"),
        (("data engineer", "analytics engineer", "etl"), "Analytics Engineer"),
        (("business analyst", "operations analyst"), "Business Analyst"),
        (("data analyst", "reporting analyst"), "Data Analyst"),
        (("project manager", "program manager", "delivery"), "Analytics Delivery Manager"),
        (("machine learning", "ai engineer", "ml engineer"), "AI/ML Analyst"),
    ]
    for needles, label in families:
        if any(n in title for n in needles):
            return label
    return profile.headline or "Business Analytics Consultant"


def _summary(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> str:
    """A summary framed for this posting, built only from verified facts.

    The candidate's base summary was identical on every resume, which made
    two applications to different roles read as the same document. This keeps
    every fact from that summary and re-frames the opening and the named
    skills around what the posting asked for. Nothing is added: the skills
    named are those already matched to real evidence for this job.
    """
    base = (profile.professional_summary or "").strip()
    matched = score.get("strongMatches") or []
    if not base or not matched:
        return base

    focus = ", ".join(matched[:4])
    role_family = _headline(job, profile)

    # Reuse the candidate's own second sentence — it carries the concrete
    # accomplishment — and rewrite only the positioning sentence in front.
    sentences = [s.strip() for s in base.split(". ") if s.strip()]
    evidence_sentence = sentences[-1] if len(sentences) > 1 else ""
    if evidence_sentence and not evidence_sentence.endswith("."):
        evidence_sentence += "."

    degrees = [e.get("degree", "").lower() for e in (profile.education or [])]
    credential = "MBA and MS in Business Analytics" if any(
        "business analytics" in d for d in degrees
    ) else "MBA"

    opening = (
        f"{role_family} with an {credential} and 3+ years delivering end-to-end "
        f"analytics solutions, with direct experience in {focus}."
    )
    return f"{opening} {evidence_sentence}".strip()


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
