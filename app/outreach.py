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
    """Pick the strongest verified accomplishment relevant to this job.

    Relevance is delegated to the resume tailorer rather than reimplemented.
    The local copy matched requirement labels as plain substrings with no
    aliases and no industry, so it ranked a Power BI dashboard bullet as the
    lead proof for a *mortgage compliance* role while the MISMO schema
    validation — the one piece of genuine domain evidence — scored zero.
    """
    from .tailor import _relevance

    wanted = score["strongMatches"] + score["partialMatches"]
    best: tuple[int, str] | None = None
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        hits, _ = _relevance(claim, wanted)
        # Prefer claims carrying a quantified outcome — they read as credible.
        rank = hits * 2 + (1 if any(ch.isdigit() for ch in claim.claim) else 0)
        if hits and (best is None or rank > best[0]):
            best = (rank, claim.claim)
    return best[1] if best else None


def _gap_line(score: dict[str, Any]) -> str:
    """Name the required things the candidate cannot evidence.

    A stretch application that stays silent about its gap invites the reader to
    find it themselves and discard the application. Naming it costs little and
    is the only honest option — the resume never implies the experience, so the
    email must not either.

    Only requirements from the *recognised* vocabulary are ever named. The
    open-vocabulary layer exists so an unknown term still counts as a gap on
    the scorecard, and for that it can afford to be noisy — but its output is
    not fit to print. It produced "I haven't worked directly with Snowflake,
    WEB, MIS" and "HP125, ERP, SSRS", where WEB, MIS and HP125 are fragments
    scraped out of the posting rather than skills. Conceding a fake skill to a
    recruiter is worse than conceding a real one.
    """
    from .skills import SKILL_ALIASES

    missing = [
        r["label"] for r in score.get("requirements", [])
        if r.get("match") == "gap"
        and r.get("importance") == "required"
        and r["label"] in SKILL_ALIASES
    ]
    if not missing:
        return ""
    named = ", ".join(missing[:3])
    return (
        f"To be straightforward about fit: I haven't worked directly with {named}. "
        f"The closest I've come is the work above, and I'd rather flag that now "
        f"than have it surface later.\n\n"
    )


def pick_contact(contacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best person to address, or None.

    Prefers an actual recruiter with a verified address, then confidence. A
    contact is only ever chosen from what a provider returned — no name or
    address pattern is ever guessed.
    """
    usable = [c for c in contacts if c.get("email")]
    if not usable:
        return None
    return sorted(
        usable,
        key=lambda c: (
            bool(c.get("isRecruiter")),
            bool(c.get("emailVerified")),
            c.get("confidence") or 0,
        ),
        reverse=True,
    )[0]


def build_outreach(
    job: dict[str, Any],
    score: dict[str, Any],
    profile: CandidateProfile,
    contact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    company = job["company"]["name"]
    title = job["title"]
    proof = _best_proof(score, profile)
    top_skills = ", ".join(score["strongMatches"][:3]) or "my analytics background"

    proof_line = (
        f"{proof}\n\n" if proof else ""
    )

    # Address the person by name when a provider actually returned one. The
    # draft previously opened "Hello," and carried a note saying no recruiter
    # could be identified — which stopped being true once contact lookup was
    # wired in, but the note was hardcoded so it still claimed otherwise.
    first_name = ((contact or {}).get("name") or "").split(" ")[0].strip()
    greeting = f"Hello {first_name}," if first_name else "Hello,"

    email_subject = f"Application for {title} — {profile.name}"
    email_body = (
        f"{greeting}\n\n"
        f"I'm applying for the {title} role at {company}. The overlap with my "
        f"background in {top_skills} is what stood out.\n\n"
        f"{proof_line}"
        f"{_gap_line(score)}"
        f"Resume attached. Happy to jump on a quick call whenever useful.\n\n"
        f"Best,\n{profile.name}\n{profile.phone} | {profile.email}\n"
        f"{profile.linkedin_url}\n"
    )

    linkedin_note = (
        f"Hi — I just applied for the {title} role at {company}. "
        f"My background is {top_skills}. Would love to connect."
    )[:280]

    to = quote((contact or {}).get("email") or "")
    mailto = (
        f"mailto:{to}?subject={quote(email_subject)}&body={quote(email_body)}"
    )

    if contact:
        note = (
            f"{contact.get('name') or 'Contact'}"
            f"{' — ' + contact['title'] if contact.get('title') else ''}, found via "
            f"{contact.get('provider') or 'contact lookup'}"
            f"{' (address verified)' if contact.get('emailVerified') else ' (address unverified — check before sending)'}."
        )
    else:
        note = (
            "No recruiter identified. No contact provider returned an address for "
            "this employer's domain, and inferring a name or email pattern would "
            "be fabrication. Add a contact manually to enable targeted outreach."
        )

    return {
        "jobId": job["id"],
        "company": company,
        "jobTitle": title,
        "contact": contact,
        "contactConfidence": (contact or {}).get("confidence") or 0,
        "contactNote": note,
        "emailSubject": email_subject,
        "emailDraft": email_body,
        "linkedinDraft": linkedin_note,
        "mailtoUrl": mailto,
        "sendPolicy": (
            "Nothing is sent automatically. Use the mailto link to open this in "
            "your own mail client, review it, and send it yourself."
        ),
    }
