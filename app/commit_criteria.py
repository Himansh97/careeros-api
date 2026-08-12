"""Launch commit criteria for a single application.

A launch status check is not a summary. Each station owns one system, reports on
that system alone, and **any single NO-GO holds the launch** regardless of what
every other station said — a dissenting controller has genuine authority to stop
a multi-billion-dollar vehicle.

The approvals queue has always worked that way and never showed it. A card said
"Ready to apply" with a fit score, while the system separately knew whether the
candidate is even eligible, whether the resume cleared its target, whether the
posting is still open, and whether the required skills have evidence behind
them. Any one of those can be disqualifying, and none of them were on the screen
where the decision gets made.

So: the criteria are named, each reports independently, and the commit call is
the conjunction. Nothing here is new analysis — every input already existed
somewhere else in the pipeline. It was just never gathered at the point of
decision.
"""
from __future__ import annotations

from typing import Any

# What a tailored resume should clear before it is worth sending. Mirrors
# tailor.TARGET_SCORE; imported rather than duplicated at call sites.
from .tailor import TARGET_SCORE

# Below this the role is not worth the candidate's time even if everything
# else is nominal. Matches the autopilot's own reject threshold in spirit.
MIN_FIT = 60


def _c(name: str, verdict: str, readout: str, holds: bool = False) -> dict[str, Any]:
    return {"name": name, "verdict": verdict, "readout": readout, "holds": holds}


def criteria_for(
    job: dict[str, Any] | None,
    score: dict[str, Any] | None,
    resume_score: int | None,
    eligibility: dict[str, Any] | None,
    posting_closed: bool = False,
) -> list[dict[str, Any]]:
    """Each station reports on its own system, and says whether it holds."""
    out: list[dict[str, Any]] = []

    # The posting has left the source pool entirely. That is not a scoring
    # failure and must not read as one — it usually means the req was pulled.
    # Reported as a hold, because applying to something that is no longer
    # listed is the clearest waste of the candidate's time there is.
    if job is None:
        return [
            _c("POSTING", "nogo",
               "No longer in the source pool — the req has probably been pulled",
               holds=True),
            _c("ELIGIBILITY", "caution", "Cannot check without the posting"),
            _c("FIT", "caution", "Cannot score without the posting"),
            _c("RESUME", "go" if (resume_score or 0) >= TARGET_SCORE else "caution",
               f"{resume_score} on file" if resume_score else "Not tailored"),
            _c("EVIDENCE", "caution", "Cannot check without the posting"),
        ]

    # ELIGIBILITY — the true knockout. A 96-fit role in Dublin is still a
    # NO-GO, and this is the one criterion that can never be argued down by a
    # good score elsewhere.
    verdict = (eligibility or {}).get("verdict")
    if verdict == "INELIGIBLE":
        blockers = (eligibility or {}).get("blockers") or []
        detail = blockers[0].get("detail") if blockers and isinstance(blockers[0], dict) else ""
        out.append(_c("ELIGIBILITY", "nogo", detail or "You cannot take this role", holds=True))
    elif verdict == "REVIEW_REQUIRED":
        out.append(_c("ELIGIBILITY", "caution",
                      "Posting says something you may not satisfy — worth reading"))
    else:
        out.append(_c("ELIGIBILITY", "go", "No knockout found"))

    # POSTING — a closed req is a hold no matter how good the match was.
    if posting_closed:
        out.append(_c("POSTING", "nogo", "No longer accepting applications", holds=True))
    else:
        out.append(_c("POSTING", "go", "Open as of the last check"))

    # FIT — the match itself.
    fit = (score or {}).get("rawFitScore")
    if fit is None:
        out.append(_c("FIT", "caution", "Not scored"))
    elif fit < MIN_FIT:
        out.append(_c("FIT", "nogo", f"{fit} — below the {MIN_FIT} floor", holds=True))
    elif fit < 75:
        out.append(_c("FIT", "caution", f"{fit} — a stretch"))
    else:
        out.append(_c("FIT", "go", f"{fit} against your evidence"))

    # RESUME — did the tailored document clear its target?
    if resume_score is None:
        out.append(_c("RESUME", "caution", "Not tailored yet"))
    elif resume_score < TARGET_SCORE:
        # Caution, not a hold: a resume below target is worth sending to a role
        # that fits. Refusing to let the candidate apply would be the system
        # overriding a decision that is theirs.
        out.append(_c("RESUME", "caution",
                      f"{resume_score} — {TARGET_SCORE - resume_score} short of {TARGET_SCORE}"))
    else:
        out.append(_c("RESUME", "go", f"{resume_score}, at target"))

    # EVIDENCE — are the posting's *required* skills actually backed?
    reqs = (score or {}).get("requirements") or []
    required = [r for r in reqs if r.get("importance") == "required"]
    unbacked = [r["label"] for r in required if r.get("match") == "gap"]
    if not required:
        out.append(_c("EVIDENCE", "caution", "No required skills identified"))
    elif unbacked:
        out.append(_c("EVIDENCE", "caution",
                      f"{len(unbacked)} required with no evidence: {', '.join(unbacked[:3])}"))
    else:
        out.append(_c("EVIDENCE", "go", f"All {len(required)} required skills evidenced"))

    return out


def commit_call(criteria: list[dict[str, Any]]) -> dict[str, Any]:
    """The conjunction. One hold stops it; cautions are stated, not fatal."""
    holds = [c for c in criteria if c.get("holds")]
    cautions = [c for c in criteria if c["verdict"] == "caution"]

    if holds:
        return {
            "verdict": "nogo",
            "heldBy": [c["name"] for c in holds],
            "summary": (
                f"{holds[0]['name']} holds: {holds[0]['readout']}"
                if len(holds) == 1
                else f"{len(holds)} criteria hold this one"
            ),
        }
    if cautions:
        return {
            "verdict": "caution",
            "heldBy": [],
            "summary": f"Go, with {len(cautions)} noted",
        }
    return {"verdict": "go", "heldBy": [], "summary": "All criteria go"}
