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


# LinkedIn's hard cap on a connection request note.
LINKEDIN_NOTE_LIMIT = 300


def _short_title(title: str, limit: int) -> str:
    """A job title that fits, cut at a word rather than mid-word.

    Real titles run long — "Analyst, FP&A - Medicare/Medicaid COGS & Network
    Performance" is 62 characters before anything else is said. Cutting at the
    last separator keeps the part that identifies the role.
    """
    title = title.strip()
    if len(title) <= limit:
        return title
    window = title[:limit]
    for sep in (" - ", " – ", ", ", " "):
        # `rsplit` returns the whole string when the separator is absent, so
        # without this guard a title with no separator in the first `limit`
        # characters came back cut mid-word — "Staff Product Manager, Enterpr".
        if sep not in window:
            continue
        head = window.rsplit(sep, 1)[0].rstrip(" ,-–")
        if len(head) >= 12:
            return head
    return window.rstrip(" ,-–")


def _linkedin_note(first_name: str, title: str, company: str, top_skills: str) -> str:
    """A connection note that fits in 300 characters without being cut off.

    This was built at full length and then sliced with `[:300]`, which on a long
    title ended the message mid-clause — "...and if someone else is closer to
    this" — turning a warm note into one that looks broken. A note that stops
    talking mid-sentence is worse than a shorter one.

    So variants are tried longest-first and the first one that fits is sent.
    What gets dropped is ordered by what earns least: the skills aside goes
    before the pointer request, because someone who cannot help often knows who
    can, and that ask is far easier to say yes to than a call.
    """
    name = first_name or "there"
    pointer = " And if someone else is closer to this one, I'd be grateful for a pointer."

    # Loop order is the priority order, and it is deliberate. The first draft
    # tried the full title first, so a 112-character title survived intact by
    # spending the pointer request — trading the most valuable sentence in the
    # note for a job title the recipient already knows. Title length is what
    # gives way first.
    for tail in (f" Would love to connect either way.{pointer}",
                 " Would love to connect either way."):
        for aside in (f" ({top_skills}).", "."):
            for job_title in (title, _short_title(title, 46), _short_title(title, 28)):
                note = (
                    f"Hi {name} — I just applied for the {job_title} role at "
                    f"{company}, and it's genuinely the kind of work I'm most "
                    f"drawn to" + aside + tail
                )
                if len(note) <= LINKEDIN_NOTE_LIMIT:
                    return note

    # Every variant is still too long, which means the company and title alone
    # fill the budget. Say the one thing that matters and stop.
    fallback = f"Hi {name} — I just applied for the {_short_title(title, 28)} role at {company}. Would love to connect."
    return fallback[:LINKEDIN_NOTE_LIMIT]


def _readable_list(items: list[str]) -> str:
    """Join for prose, not for a CSV.

    "Python, Azure" mid-sentence reads as a field that leaked into a letter.
    The email is trying to sound like a person wrote it, and people write "and".
    """
    items = [i for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


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


def _linkedin_handle(url: str) -> str:
    """Render LinkedIn as a handle, not a URL.

    Gmail rewrites any URL-shaped string in a plain-text body into a
    `google.com/url?q=...` tracking redirect, so a signature that read
    `https://www.linkedin.com/in/name/` arrived as a long wrapped link. The
    handle is unambiguous, survives untouched, and reads better in a signature.
    """
    handle = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return handle or (url or "")


def build_outreach(
    job: dict[str, Any],
    score: dict[str, Any],
    profile: CandidateProfile,
    contact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    company = job["company"]["name"]
    title = job["title"]
    proof = _best_proof(score, profile)
    top_skills = _readable_list(score["strongMatches"][:3]) or "my analytics background"

    # The claim is a resume bullet. Dropped into an email unannounced it reads
    # like one, so it gets a conversational lead-in — the sentence stays
    # verbatim, because it is the part that has to remain true.
    proof_line = f"For a sense of what that looks like in practice: {proof}\n\n" if proof else ""

    # Address the person by name when a provider actually returned one. The
    # draft previously opened "Hello," and carried a note saying no recruiter
    # could be identified — which stopped being true once contact lookup was
    # wired in, but the note was hardcoded so it still claimed otherwise.
    first_name = ((contact or {}).get("name") or "").split(" ")[0].strip()
    # "Hi" rather than "Hello" — the rest of the note is written as one person
    # to another, and "Hello," on its own line sets a formality the body then
    # contradicts.
    greeting = f"Hi {first_name}," if first_name else "Hi there,"

    # Subject lines that get opened are short, specific and sound like a person
    # wrote them. "Application for X — Name" reads like a form submission, and a
    # recruiter scanning fifty of those has no reason to open one.
    # Inboxes cut the subject around 60 characters on desktop and far less on a
    # phone, so the title is trimmed to leave the question visible rather than
    # letting a 62-character title push everything else out of view.
    subject_title = _short_title(title, 30)
    email_subject = (
        f"{first_name} — quick question about {subject_title}"
        if first_name
        else f"Quick question about the {subject_title} role"
    )

    # Written for a reply, not for completeness. Four things do the work, and
    # none of them are tricks — a trick that gets a reply and then disappoints
    # costs more than the reply was worth:
    #
    # * brevity — under ~130 words, because a wall of text on a phone gets
    #   archived and the previous draft opened with an unbroken paragraph
    # * one concrete number, early — credibility is specificity, not adjectives
    # * reciprocity — offering something useful before asking for anything
    # * a single, low-friction question at the end, plus an explicit easy out.
    #   Naming the graceful no is what makes the yes cheap to give, and it
    #   costs nothing because it is true either way.
    ask = "Would a short call in the next week or two make sense?"
    email_body = (
        f"{greeting}\n\n"
        f"I applied for the {title} role at {company} and wanted to reach out "
        f"directly — it lines up closely with the work I actually enjoy most, "
        f"which is {top_skills}.\n\n"
        f"{proof_line}"
        f"{_gap_line(score)}"
        f"Happy to send a short note on how I'd approach the first 90 days if "
        f"that's useful — and if the role has already moved on, no problem at "
        f"all; I'd just ask to be kept in mind for similar work.\n\n"
        f"{ask}\n\n"
        f"Thanks for reading,\n{profile.name}\n{profile.phone} | {profile.email}\n"
        f"LinkedIn: {_linkedin_handle(profile.linkedin_url)}\n"
    )

    linkedin_note = _linkedin_note(first_name, title, company, top_skills)

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
