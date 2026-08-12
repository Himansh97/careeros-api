"""Everything known about one application, assembled for the interview.

By the time a recruiter replies, the system already holds more about that
application than the candidate can hold in their head: which requirements the
posting screens on, which bullets went out and why those, which requirements
were weakly evidenced, and what was said in the thread. Today all of it is
scattered across four screens and the candidate walks into the call having
re-read the JD.

What this deliberately does **not** do:

* **No company research.** There is no source for "recent developments" here,
  and inventing plausible-sounding company facts before an interview is worse
  than saying nothing — the candidate would repeat them to someone who knows
  better. Only what the posting itself states.
* **No generic question bank.** "Tell me about a time you showed leadership"
  is not intelligence. Every question here is derived from a requirement this
  specific posting states.
* **No invented stories.** STAR material comes from `career_evidence.json` and
  nowhere else, and each carries the claim it came from.

The most valuable section is the one about weakness. The audit already knows
which requirements are unevidenced or only partially matched — an interviewer
probing the role's actual requirements will land on exactly those, and knowing
which they are beforehand is the difference between being caught out and
having an honest answer ready.
"""
from __future__ import annotations

from typing import Any

from .profile import CandidateProfile

# How a requirement's match state translates into what an interviewer is
# likely to do with it.
_PROBE = {
    "gap": (
        "high",
        "Nothing in your evidence supports this. Expect it to come up, and "
        "have an honest answer about how you'd close it.",
    ),
    "partial": (
        "medium",
        "You have adjacent evidence but nothing that names this directly. "
        "Expect a follow-up question testing how deep it goes.",
    ),
}


def _star_stories(
    profile: CandidateProfile, requirements: list[dict[str, Any]], limit: int = 6
) -> list[dict[str, Any]]:
    """Real accomplishments, matched to what this posting asks about.

    Ordered by how many of the posting's requirements each claim speaks to, so
    the candidate rehearses the stories that answer the most ground rather than
    their personal favourites.
    """
    from .tailor import _relevance

    wanted = [r["label"] for r in requirements if r["match"] in ("exact", "partial")]
    scored: list[tuple[int, list[str], Any]] = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        n, hits = _relevance(claim, wanted)
        if n:
            scored.append((n, hits, claim))
    scored.sort(key=lambda t: -t[0])

    return [
        {
            "claimId": claim.claim_id,
            "employer": claim.employer,
            "situation": claim.claim,
            "answers": hits,
            "source": claim.source,
            # Not a script. The candidate lived this; what they need is the
            # reminder of which story covers which requirement.
            "useWhen": f"Asked about {', '.join(hits[:3])}.",
        }
        for _, hits, claim in scored[:limit]
    ]


def _questions_from_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions this posting's own requirements imply.

    Derived, not drawn from a bank. A posting that names Snowflake three times
    will be asked about Snowflake; one that never mentions it will not.
    """
    out = []
    for req in requirements:
        if req["importance"] != "required":
            continue
        label = req["label"]
        if req["match"] == "exact":
            out.append({
                "question": f"Walk me through your experience with {label}.",
                "difficulty": "expected",
                "youHave": req.get("evidence"),
            })
        else:
            severity, guidance = _PROBE.get(req["match"], ("medium", ""))
            out.append({
                "question": f"How much have you worked with {label}?",
                "difficulty": severity,
                "youHave": req.get("evidence"),
                "guidance": guidance,
            })
    # Hard ones first: those are the ones worth preparing.
    out.sort(key=lambda q: {"high": 0, "medium": 1, "expected": 2}[q["difficulty"]])
    return out[:10]


def _questions_to_ask(job: dict[str, Any], posting: dict[str, Any]) -> list[str]:
    """Questions grounded in this posting, not a generic list.

    Each one is only included when the posting gives a reason to ask it.
    """
    asks = []
    if posting.get("yearsRequested"):
        asks.append(
            f"The posting asks for {posting['yearsRequested']}+ years — how firm is "
            "that, and what does the team actually need someone to do in month one?"
        )
    if posting.get("preferred"):
        asks.append(
            "Which of the nice-to-haves — "
            + ", ".join(posting["preferred"][:3])
            + " — matter most in practice?"
        )
    if posting.get("required"):
        asks.append(
            f"Where does {posting['required'][0]} sit in the team's current stack, "
            "and what's changing about it?"
        )
    asks.append("What does the first six months look like for whoever takes this?")
    asks.append("How is success measured for this role at the six-month mark?")
    return asks


def build_pack(
    job: dict[str, Any],
    score: dict[str, Any],
    resume: dict[str, Any],
    profile: CandidateProfile,
    application: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the interview brief for one application."""
    from .interview_intel import get_intel
    from .skills import classify_posting

    requirements = score.get("requirements", [])
    posting = classify_posting(job.get("description", ""), job.get("title", ""))

    weak = [
        {
            "requirement": r["label"],
            "match": r["match"],
            "importance": r["importance"],
            "severity": _PROBE[r["match"]][0],
            "guidance": _PROBE[r["match"]][1],
            "closest": r.get("evidence"),
        }
        for r in requirements
        if r["match"] in _PROBE
    ]
    weak.sort(key=lambda w: (w["importance"] != "required", w["severity"] != "high"))

    bullets = [b["text"] for s in resume.get("sections", []) for b in s["bullets"]]

    return {
        "jobId": job["id"],
        # What people report about this employer's actual process. Absent
        # entirely when nobody has researched it — see interview_intel.py for
        # why nothing is generated to fill the space.
        "intel": get_intel(job["company"]["name"], job["title"]),
        "role": {
            "title": job["title"],
            "company": job["company"]["name"],
            "location": job.get("location"),
            "applyUrl": job.get("applyUrl"),
            "screensOn": posting["required"],
            "wishlist": posting["preferred"],
            "yearsRequested": posting.get("yearsRequested"),
        },
        # What the employer actually received. Reconstructing this from memory
        # is where candidates contradict their own application.
        "whatTheySaw": {
            "resumeScore": resume.get("resumeScore"),
            "bullets": bullets,
            "summary": resume.get("summary"),
            "submittedAt": (application or {}).get("submittedAt"),
            "status": (application or {}).get("status"),
        },
        # The section worth reading twice.
        "expectToBeProbed": weak,
        "likelyQuestions": _questions_from_requirements(requirements),
        "stories": _star_stories(profile, requirements),
        "questionsToAsk": _questions_to_ask(job, posting),
        "recruiterThread": [
            {
                "from": m.get("senderName") or m.get("senderEmail"),
                "subject": m.get("subject"),
                "receivedAt": m.get("receivedAt"),
                "synopsis": m.get("synopsis"),
                "replySentAt": (m.get("draft") or {}).get("sentAt"),
            }
            for m in (messages or [])
        ],
        "notIncluded": (
            "No company news or financials — there is no source for those here, "
            "and inventing them before an interview is worse than omitting them. "
            "Process intel appears only where it has actually been researched, "
            "and carries the URLs it came from."
        ),
    }
