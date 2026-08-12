"""Who to approach at a company, and when.

Finding a recruiter's address is the easy half and `contacts.py` already does
it. The hard half is that most outreach fails for reasons that have nothing to
do with the address: it goes to the wrong person, or it asks for a referral
from someone who has no idea who the candidate is.

Two things here:

* **Ranking paths.** A hiring manager on the team, a recruiter who owns the
  req, and an engineer who happens to share an alma mater are three different
  approaches with different odds and different first messages. Ranked by how
  much reason the person has to reply.
* **Sequencing.** Asking a stranger for a referral in the first message is the
  single most common way this goes wrong. A referral is a favour that costs the
  giver real credibility, and nobody spends that on a name they met ten seconds
  ago. So the plan opens with something the recipient can answer cheaply, and
  the ask only appears if they engage.

Nothing here sends anything, and nothing here fabricates a connection. A shared
university is only claimed when it is actually in the candidate's education
history; "second-degree connection" is never asserted, because CareerOS has no
access to a connection graph and inventing one would produce a message that
falls apart on contact.
"""
from __future__ import annotations

from typing import Any

from .profile import CandidateProfile

# What a title tells you about whether this person can help.
_HIRING_MANAGER = ("head of", "director", "vp ", "vice president", "manager",
                   "lead", "principal", "chief")
_SAME_WORK = ("data", "analyt", "business intelligence", "bi ", "insight",
              "reporting", "engineer", "scientist")


def _shared_background(person: dict[str, Any], profile: CandidateProfile) -> list[str]:
    """Genuine overlaps only — never an inferred or assumed connection."""
    shared: list[str] = []
    haystack = " ".join(
        str(person.get(k) or "") for k in ("title", "bio", "summary", "company")
    ).lower()

    for entry in profile.education or []:
        school = (entry.get("institution") or "").strip()
        if school and school.lower() in haystack:
            shared.append(f"also {school}")

    for claim in profile.evidence:
        employer = claim.employer.split("(")[0].strip()
        if len(employer) > 4 and employer.lower() in haystack:
            shared.append(f"overlap at {employer}")
            break

    return shared


def rank_paths(
    contacts: list[dict[str, Any]], job: dict[str, Any], profile: CandidateProfile
) -> list[dict[str, Any]]:
    """Order the people at a company by how much reason they have to reply."""
    from .contacts import RECRUITER_TITLE_HINTS

    job_title = (job.get("title") or "").lower()
    ranked = []

    for person in contacts:
        title = (person.get("title") or "").lower()
        score = 30
        why: list[str] = []

        is_recruiter = any(h in title for h in RECRUITER_TITLE_HINTS)
        is_manager = any(h in title for h in _HIRING_MANAGER)
        same_work = any(h in title for h in _SAME_WORK)

        if is_manager and same_work:
            score += 40
            why.append("leads the function this role sits in")
        elif is_manager:
            score += 20
            why.append("senior enough to know about the opening")
        if is_recruiter:
            score += 25
            why.append("recruiting — this is literally their job to answer")
        if same_work and not is_manager and not is_recruiter:
            score += 20
            why.append("does the same work day to day")

        # Title words shared with the posting: the closer the overlap, the more
        # likely this person is on the team actually hiring.
        overlap = {w for w in title.split() if len(w) > 3} & {
            w for w in job_title.split() if len(w) > 3
        }
        if overlap:
            score += 10
            why.append(f"title overlaps the role ({', '.join(sorted(overlap))})")

        shared = _shared_background(person, profile)
        if shared:
            score += 15
            why.extend(shared)

        if person.get("emailVerified"):
            score += 10
            why.append("address verified")

        ranked.append({
            "contactId": person.get("id"),
            "name": person.get("name"),
            "title": person.get("title"),
            "email": person.get("email"),
            "score": min(100, score),
            "why": why,
            "role": (
                "recruiter" if is_recruiter
                else "hiring manager" if is_manager
                else "peer"
            ),
            "shared": shared,
        })

    ranked.sort(key=lambda p: -p["score"])
    return ranked


def approach_plan(person: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """How to open with this person, and when the ask is appropriate.

    A recruiter can be asked directly — screening candidates is the job, and
    treating them coyly wastes everyone's time. Anyone else gets a question
    they can answer in one line before anything is requested of them.
    """
    role = person["role"]
    company = job["company"]["name"]
    title = job.get("title", "the role")

    if role == "recruiter":
        return {
            "openWith": "direct",
            "steps": [
                {
                    "day": 0,
                    "action": f"Write directly about {title}",
                    "why": (
                        "Screening candidates is their job. A short note naming the "
                        "role and two relevant specifics is the whole message."
                    ),
                },
                {
                    "day": 6,
                    "action": "One follow-up if there's no reply, then stop",
                    "why": "Recruiters are buried. Twice is persistence; three times is noise.",
                },
            ],
            "askForReferral": False,
            "note": "No referral to ask for — they own the process.",
        }

    return {
        "openWith": "question",
        "steps": [
            {
                "day": 0,
                "action": (
                    f"Introduce yourself and ask one specific question about the team's work"
                    + (f" — lead with {person['shared'][0]}" if person.get("shared") else "")
                ),
                "why": (
                    "Cheap to answer and asks for nothing. A first message that "
                    "requests a favour from a stranger is the most common way this fails."
                ),
            },
            {
                "day": 2,
                "action": f"If they reply, mention you've applied for {title} at {company}",
                "why": "Context, not a request. Let them offer.",
            },
            {
                "day": 4,
                "action": "Only if the exchange is warm: ask whether they'd be comfortable referring you",
                "why": (
                    "A referral spends the giver's credibility. The word "
                    "'comfortable' gives them a graceful way to decline, which is "
                    "what makes it askable at all."
                ),
            },
        ],
        "askForReferral": True,
        "note": (
            "If there is no reply to the first message, stop. An unanswered "
            "introduction followed by a referral request is how people get blocked."
        ),
    }


def referral_strategy(
    contacts: list[dict[str, Any]], job: dict[str, Any], profile: CandidateProfile
) -> dict[str, Any]:
    ranked = rank_paths(contacts, job, profile)
    best = ranked[0] if ranked else None
    return {
        "paths": ranked,
        "best": best,
        "plan": approach_plan(best, job) if best else None,
        "note": (
            "Ranked on title, overlap with the posting, and background you "
            "actually share. CareerOS has no connection graph, so no "
            "second-degree connection is claimed — a message built on an "
            "invented link falls apart on contact."
        ),
    }
