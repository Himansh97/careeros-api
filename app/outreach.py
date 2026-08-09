"""Outreach drafting from real evidence.

Two deliberate limits, both surfaced to the client rather than hidden:

1. No recruiter identity is invented. Greenhouse's public API exposes no
   recruiter contact, so `contact` is null and confidence is 0 unless a real
   contact is supplied. Guessing a name or an email pattern would be
   fabrication.
2. Nothing sends. Drafts are returned for review, and the send path is a
   mailto: link the candidate triggers themselves.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .profile import CandidateProfile


def _best_proof(score: dict[str, Any], profile: CandidateProfile) -> str | None:
    """Pick the strongest verified accomplishment relevant to this job."""
    wanted = [s.lower() for s in score["strongMatches"]]
    best: tuple[int, str] | None = None
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        text = (claim.claim + " " + " ".join(claim.skills)).lower()
        hits = sum(1 for w in wanted if w in text)
        # Prefer claims carrying a quantified outcome — they read as credible.
        quantified = any(ch.isdigit() for ch in claim.claim)
        rank = hits * 2 + (1 if quantified else 0)
        if hits and (best is None or rank > best[0]):
            best = (rank, claim.claim)
    return best[1] if best else None


def build_outreach(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> dict[str, Any]:
    company = job["company"]["name"]
    title = job["title"]
    proof = _best_proof(score, profile)
    top_skills = ", ".join(score["strongMatches"][:3]) or "my analytics background"

    proof_line = (
        f"{proof}\n\n" if proof else ""
    )

    email_subject = f"Application for {title} — {profile.name}"
    email_body = (
        f"Hello,\n\n"
        f"I'm applying for the {title} role at {company}. The overlap with my "
        f"background in {top_skills} is what stood out.\n\n"
        f"{proof_line}"
        f"Resume attached. Happy to jump on a quick call whenever useful.\n\n"
        f"Best,\n{profile.name}\n{profile.phone} | {profile.email}\n"
        f"{profile.linkedin_url}\n"
    )

    linkedin_note = (
        f"Hi — I just applied for the {title} role at {company}. "
        f"My background is {top_skills}. Would love to connect."
    )[:280]

    mailto = (
        f"mailto:?subject={quote(email_subject)}&body={quote(email_body)}"
    )

    return {
        "jobId": job["id"],
        "company": company,
        "jobTitle": title,
        "contact": None,
        "contactConfidence": 0,
        "contactNote": (
            "No recruiter identified. Greenhouse's public API exposes no contact "
            "for this posting, and inferring a name or email pattern would be "
            "fabrication. Add a contact manually to enable targeted outreach."
        ),
        "emailSubject": email_subject,
        "emailDraft": email_body,
        "linkedinDraft": linkedin_note,
        "mailtoUrl": mailto,
        "sendPolicy": (
            "Nothing is sent automatically. Use the mailto link to open this in "
            "your own mail client, review it, and send it yourself."
        ),
    }
