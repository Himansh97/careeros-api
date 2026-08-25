"""Conversational resume editing, with the containment gate in front of it.

What this is
------------
The candidate types what they want in plain language — "lead with the
governance work", "this reads junior", "align it to the JD's stakeholder
requirement" — and gets back concrete rewrites of specific bullets, each with a
verdict already attached. It is the chat experience, inside the app, against
their own evidence.

What makes it safe to exist here
--------------------------------
Nothing in this module decides whether a rewrite is acceptable. Every proposal
goes through `overrides.assess_override`, which is the same function
`save_override` calls — so what the candidate is shown and what would actually
be written cannot disagree. A model that invents a figure, a tool or a scope is
caught by the machinery that already existed for exactly that.

Proposals are stored under `author="llm"`, a tier `overrides._POLICY` has
carried since before anything used it:

    pass    -> active           applied to the resume
    review  -> pending_review   queued, NOT applied
    reject  -> rejected         refused outright

That sits deliberately between `system` (any doubt is fatal) and `user` (their
own history, warnings only). A model may not vouch for a claim about someone
else's career, so it never gets the candidate's benefit of the doubt — but a
borderline rewrite is worth a human look rather than a silent discard.

The rejected proposals are shown, not hidden
--------------------------------------------
A caught fabrication is the most informative thing this feature produces. It
tells the candidate the model tried to inflate the claim and exactly how, which
is the whole argument for running a resume through a gate at all. Hiding it
would leave them believing the model simply had no ideas.

Failure is always honest
------------------------
No key, spent budget, unparseable response: the reply says so and proposes
nothing. There is no rule-based fallback here, because unlike a resume — which
must always be producible — a coaching turn that cannot happen is allowed to
not happen. Inventing a chat reply to fill the silence would be the one thing
this module must never do.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .profile import CandidateProfile

logger = logging.getLogger(__name__)

MAX_PROPOSALS = 6
MAX_HISTORY_TURNS = 8
MAX_INSTRUCTION_CHARS = 2000

VOICE = """You are helping a candidate tighten their own resume. You are editing
sentences, not writing a career.

The hard rule, and the only one that matters: every figure, employer, tool,
system and outcome in a rewrite must already appear in the source claim you were
given. You may reorder, re-emphasise, re-lead, tighten and cut. You may not add.
If the candidate asks for something the evidence does not support, say so
plainly in your reply and rewrite what you honestly can instead — do not quietly
invent it and do not pad the sentence to reach for it.

Do not escalate authority. If the claim says "supported", the rewrite may not
say "led" or "owned". The candidate's recorded verb is a ceiling.

Prefer shorter. A rewrite that is much longer than its claim is almost always
carrying something the claim does not say.

Reply with JSON only, in this exact shape:

{"reply": "<one or two sentences to the candidate>",
 "proposals": [{"claimId": "<id from the list>",
                "text": "<the rewritten bullet>",
                "why": "<short reason this serves what they asked for>"}]}

`proposals` may be empty when the right answer is a question or an explanation.
Never propose a rewrite for a claimId that was not given to you."""


def _bullets(resume: dict[str, Any]) -> list[dict[str, Any]]:
    """Every bullet on the tailored resume, flattened, in document order."""
    out: list[dict[str, Any]] = []
    for section in resume.get("sections") or []:
        for bullet in section.get("bullets") or []:
            if bullet.get("id"):
                out.append({**bullet, "employer": section.get("employer", "")})
    return out


def context(resume: dict[str, Any], profile: CandidateProfile,
            score: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the model is allowed to see: the bullets, and their source claims.

    The source claim travels with every bullet because it is the boundary the
    rewrite must stay inside. Sending the bullets alone would be asking the
    model to respect a limit it cannot see, and then blaming it for the misses.
    """
    claims = {c.claim_id: c for c in profile.evidence}
    editable: list[dict[str, Any]] = []
    for bullet in _bullets(resume):
        claim = claims.get(bullet["id"])
        if claim is None:
            # A bullet with no claim behind it cannot be contained, so it is not
            # offered for rewriting rather than rewritten unchecked.
            continue
        editable.append(
            {
                "claimId": claim.claim_id,
                "employer": bullet.get("employer", ""),
                "current": bullet.get("text", ""),
                "sourceClaim": claim.claim,
                "seniorityCeiling": claim.seniority_verb,
                "supports": bullet.get("hits") or [],
            }
        )

    return {
        "bullets": editable,
        "requirements": ((score or {}).get("strongMatches") or [])
        + ((score or {}).get("partialMatches") or []),
        "gaps": (score or {}).get("gaps") or [],
    }


def _prompt(ctx: dict[str, Any], instruction: str,
            history: list[dict[str, str]]) -> str:
    lines: list[str] = []
    if ctx["requirements"]:
        lines.append("WHAT THIS POSTING ASKS FOR: " + ", ".join(
            str(r) for r in ctx["requirements"][:14]
        ))
    if ctx["gaps"]:
        # Named so the model can say "the evidence does not cover this" instead
        # of writing a sentence that pretends it does.
        lines.append("REQUIREMENTS THE EVIDENCE DOES NOT COVER — never write "
                     "toward these: " + ", ".join(str(g) for g in ctx["gaps"][:10]))

    lines.append("\nBULLETS YOU MAY REWRITE. `source` is the recorded claim and "
                 "is the boundary — nothing outside it may appear in a rewrite.")
    for bullet in ctx["bullets"]:
        lines.append(
            f"\n[{bullet['claimId']}] ({bullet['employer']})"
            f"\n  current: {bullet['current']}"
            f"\n  source : {bullet['sourceClaim']}"
            + (f"\n  ceiling: {bullet['seniorityCeiling']}"
               if bullet["seniorityCeiling"] else "")
        )

    if history:
        lines.append("\nEARLIER IN THIS CONVERSATION:")
        for turn in history[-MAX_HISTORY_TURNS:]:
            role = "candidate" if turn.get("role") == "user" else "you"
            lines.append(f"  {role}: {str(turn.get('content', ''))[:600]}")

    lines.append(f"\nTHE CANDIDATE ASKS: {instruction}")
    return "\n".join(lines)


def _parse(text: str) -> dict[str, Any] | None:
    """The JSON object from a response, tolerating a code fence around it."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _assess(proposal: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """One proposal with its verdict attached, or None if it is not usable."""
    from .overrides import assess_override

    claim_id = str(proposal.get("claimId") or "").strip()
    text = str(proposal.get("text") or "").strip()
    bullet = by_id.get(claim_id)
    # A proposal against a claim the model was never given is not a containment
    # failure to report, it is a malformed response. Dropped rather than shown.
    if not bullet or not text:
        return None

    assessment = assess_override(
        text,
        bullet["sourceClaim"],
        author="llm",
        seniority_ceiling=bullet["seniorityCeiling"],
    )
    return {
        "claimId": claim_id,
        "employer": bullet["employer"],
        "current": bullet["current"],
        "sourceClaim": bullet["sourceClaim"],
        "proposed": text,
        "why": str(proposal.get("why") or "").strip(),
        "verdict": assessment["verdict"],
        "outcome": assessment["outcome"],
        "findings": assessment["findings"],
        # Whether Apply will do anything. A rejected proposal stays visible —
        # a caught fabrication is the most informative thing here — but it
        # cannot be written, and the button must not imply otherwise.
        "applicable": assessment["outcome"] != "rejected",
        "queued": assessment["outcome"] == "pending_review",
    }


def coach(resume: dict[str, Any], profile: CandidateProfile, instruction: str,
          score: dict[str, Any] | None = None,
          history: list[dict[str, str]] | None = None,
          job_id: str | None = None) -> dict[str, Any]:
    """One coaching turn: a reply, and gated rewrites of named bullets."""
    from .llm import available, complete

    instruction = (instruction or "").strip()[:MAX_INSTRUCTION_CHARS]
    if not instruction:
        return {"ok": False, "reply": "", "proposals": [],
                "reason": "no instruction given"}

    ctx = context(resume, profile, score)
    if not ctx["bullets"]:
        return {"ok": False, "reply": "", "proposals": [],
                "reason": "this resume has no bullets traceable to an evidence "
                          "claim, so there is nothing that can be safely rewritten"}

    ready, why = available()
    if not ready:
        # Said plainly rather than dressed up as a reply. The candidate needs to
        # know the model did not answer, not to wonder why it was unhelpful.
        return {"ok": False, "reply": "", "proposals": [], "reason": why}

    result = complete(
        _prompt(ctx, instruction, history or []),
        purpose="resume_coach",
        system=VOICE,
        job_id=job_id,
        max_tokens=3000,
    )
    if result is None:
        return {"ok": False, "reply": "", "proposals": [],
                "reason": "the request to the model did not complete"}

    parsed = _parse(result.text)
    if parsed is None:
        logger.info("resume_coach: unparseable response — %r", result.text[:300])
        return {"ok": False, "reply": "", "proposals": [],
                "reason": "the model's response was not readable"}

    by_id = {b["claimId"]: b for b in ctx["bullets"]}
    proposals: list[dict[str, Any]] = []
    for raw in (parsed.get("proposals") or [])[:MAX_PROPOSALS]:
        if not isinstance(raw, dict):
            continue
        assessed = _assess(raw, by_id)
        if assessed:
            proposals.append(assessed)

    return {
        "ok": True,
        "reply": str(parsed.get("reply") or "").strip(),
        "proposals": proposals,
        "blocked": sum(1 for p in proposals if not p["applicable"]),
        "costUsd": round(result.cost_usd, 6),
    }
