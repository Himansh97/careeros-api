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

It may also capture evidence, and only from the candidate's own mouth
--------------------------------------------------------------------
The coach used to be able to say "your evidence does not cover that" and nothing
else, which is the correct refusal and a dead end. A candidate who replies "I do
know Excel, I ran the regressions at Omnicals" is not asking the model to invent
anything — they are stating a fact about their own career that the vault happens
not to hold yet. Refusing that is refusing to learn.

So a turn may also return evidence drafts. Three things keep this from becoming
a fabrication channel:

* **A draft must quote the candidate.** Every draft carries the span of the
  conversation it came from, and `_evidence_drafts` checks that the span really
  appears in what the candidate typed. A model-authored claim with no such span
  is dropped before the candidate ever sees it. This is a server-side check, not
  an instruction the model is trusted to follow.
* **A draft is not a write.** It is shown for confirmation and written only by
  an explicit call to the evidence endpoint. Nothing here touches the vault.
* **A written claim is not resume-eligible.** `add_claim` stores it unapproved
  and `tailor` skips unapproved claims, so a claim cannot be added and then used
  in the same breath. Approval stays a separate, deliberate act.

It knows how the market talks, and that is all it knows
------------------------------------------------------
`market.vocabulary` reads forty live postings for this title and returns the
phrasings and tool names that recur around a named skill. That fixes a real
tell: with one posting in view the model writes "utilised Python for data
manipulation", which is how a resume announces its author has not done the work.

Market context is register, never content. Nothing in it is evidence and nothing
in it may be claimed — a posting wanting Spark on 10TB does not put 10TB in this
candidate's history. The prompt says so, and `assess_override` enforces it
regardless, because it compares the rewrite against the recorded claim and has
never cared where a word came from.

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
MAX_EVIDENCE_DRAFTS = 4
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

HOW THE MARKET TALKS is vocabulary, not evidence. Use it to pick the verb and
the register a practitioner would use, and to avoid the words that give away
someone who has not done the work — "utilised", "leveraged", "responsible for",
"exposure to". Never treat anything in it as something the candidate has done.
A scale, tool or outcome that appears there and not in the source claim may not
appear in a rewrite. Do not copy a posting's sentence; borrow how it speaks.

Write like the person who did the work. Someone who has actually used a tool
names what they built with it and the specific thing they hit, at the scale
their own claim states. Someone who has not reaches for adjectives. If the
source claim is thin, the honest rewrite is short — padding it to sound
experienced is the failure this whole system exists to prevent.

WHEN THE EVIDENCE IS MISSING BUT THE CANDIDATE STATES IT
If they tell you about work that is not in the claims list — "I ran the
regressions in Excel at Omnicals", "I have used Spark at Freyr" — do not
rewrite a bullet toward it, and do not ignore it. Return it in `evidence`
instead, so it can be recorded and approved before any resume uses it.

You do not save anything. Each draft becomes a card the candidate can accept or
ignore, and nothing is written unless they click. So never say you have logged,
added, recorded or saved something — you have not, and a reply claiming
otherwise leaves them believing their evidence contains a claim it does not.
Say what you noticed is missing, and let the cards be the record. Do not
enumerate the drafts in prose either: if you return one and describe two, the
reply and the screen disagree and the screen is the truthful one.

Every evidence draft MUST carry `quote`: the candidate's own words, copied
exactly from this conversation, that state the thing. If you cannot copy such a
span, you are inferring rather than recording — return no draft. Never draft
evidence from a job posting, from the market vocabulary, or from what would be
convenient for this application.

Classify honestly:
  PRESENT_AND_EXPLICIT    they did this at an employer or on a real project
  LEARNED_OR_ACADEMIC     coursework, certification, or self-study
  IN_PROGRESS_OR_DESIGNED designed or underway, NOT delivered
A certification is LEARNED_OR_ACADEMIC. Using the skill at an employer is
PRESENT_AND_EXPLICIT. If they said both, those are two different claims.

Every draft needs an `employer`, including a certification — use the issuing
body ("Microsoft", "AWS", the university). If the candidate did not name one,
do not guess and do not return the draft: ask them who issued it, and they can
tell you next turn.

Reply with JSON only, in this exact shape:

{"reply": "<one or two sentences to the candidate>",
 "proposals": [{"claimId": "<id from the list>",
                "text": "<the rewritten bullet>",
                "why": "<short reason this serves what they asked for>"}],
 "evidence": [{"claim": "<one sentence, in their voice, about what they did>",
               "employer": "<employer or project they named>",
               "skills": ["<skill>"],
               "classification": "<one of the three above>",
               "quote": "<their exact words from this conversation>"}]}

Both lists may be empty. `proposals` is empty when the right answer is a
question or an explanation. Never propose a rewrite for a claimId that was not
given to you."""


def _bullets(resume: dict[str, Any]) -> list[dict[str, Any]]:
    """Every bullet on the tailored resume, flattened, in document order."""
    out: list[dict[str, Any]] = []
    for section in resume.get("sections") or []:
        for bullet in section.get("bullets") or []:
            if bullet.get("id"):
                out.append({**bullet, "employer": section.get("employer", "")})
    return out


def context(resume: dict[str, Any], profile: CandidateProfile,
            score: dict[str, Any] | None = None,
            instruction: str = "", title: str = "") -> dict[str, Any]:
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

    # Skill-level context when they named one, role-level otherwise. Reading
    # the snapshot is cheap and offline; a cold snapshot yields empty lists
    # rather than an error, and the turn simply proceeds without it.
    from .market import named_skill, vocabulary

    # getattr: a claim with no skills list must not take down a coaching turn.
    known = sorted({
        s for c in profile.evidence for s in (getattr(c, "skills", None) or [])
    })
    skill = named_skill(instruction, known) if instruction else ""
    market = vocabulary(title, skill) if title else {
        "sampled": 0, "titles": [], "skill": skill,
        "phrasings": [], "tools": [], "expectations": [],
    }

    return {
        "bullets": editable,
        "requirements": ((score or {}).get("strongMatches") or [])
        + ((score or {}).get("partialMatches") or []),
        "gaps": (score or {}).get("gaps") or [],
        "market": market,
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

    market = ctx.get("market") or {}
    if market.get("sampled"):
        head = (f"HOW THE MARKET TALKS ABOUT {market['skill'].upper()}"
                if market.get("skill") else "HOW THE MARKET TALKS ABOUT THIS ROLE")
        lines.append(
            f"\n{head} — drawn from {market['sampled']} live postings for "
            "similar titles. This is vocabulary and register ONLY. Nothing here "
            "is evidence and nothing here may be claimed."
        )
        if market.get("phrasings"):
            lines.append("  phrasings that recur: "
                         + "; ".join(market["phrasings"]))
        if market.get("tools"):
            lines.append("  named alongside it: " + ", ".join(market["tools"]))
        for line in (market.get("expectations") or [])[:4]:
            lines.append(f"  posting says: {line[:200]}")

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


MIN_QUOTE_CHARS = 12


def _spoken(instruction: str, history: list[dict[str, str]]) -> str:
    """Everything the candidate themselves typed, normalised for matching."""
    said = [instruction] + [
        str(t.get("content") or "") for t in history if t.get("role") == "user"
    ]
    return " ".join(" ".join(said).lower().split())


def _evidence_drafts(raw: Any, instruction: str,
                     history: list[dict[str, str]]) -> tuple[list[dict[str, Any]], int, int]:
    """Drafts the candidate actually said, and a count of the ones they did not.

    The quote check is the load-bearing part. A model asked to record what
    someone told it will, under pressure to be useful, also record what would
    help — a certification nobody mentioned, a tool the posting wants. Requiring
    it to point at the span of conversation the claim came from, and then
    verifying that span really is in what the candidate typed, turns "please
    only record what they said" from a hope into a check.

    Rejected drafts are counted rather than returned. Unlike a rejected rewrite
    — which is worth showing, because seeing the fabrication is the argument for
    the gate — an invented claim has no source bullet to compare it against, so
    displaying it would just be putting an unsourced sentence about the
    candidate's career on their screen.
    """
    from .evidence import CLASSIFICATIONS

    spoken = _spoken(instruction, history)
    drafts: list[dict[str, Any]] = []
    unsourced = 0
    incomplete = 0

    for item in (raw or [])[:MAX_EVIDENCE_DRAFTS]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        employer = str(item.get("employer") or "").strip()
        quote = " ".join(str(item.get("quote") or "").lower().split())
        classification = str(item.get("classification") or "").strip().upper()

        if not claim:
            continue
        if not employer:
            # The vault requires an employer or project on every claim, so a
            # certification with no issuing body named cannot be stored. Counted
            # rather than dropped quietly: the candidate mentioned something and
            # is owed an explanation of why it is not on screen.
            incomplete += 1
            continue
        if classification not in CLASSIFICATIONS:
            classification = "PRESENT_AND_EXPLICIT"
        if len(quote) < MIN_QUOTE_CHARS or quote not in spoken:
            unsourced += 1
            continue

        drafts.append({
            "claim": claim,
            "employer": employer,
            "skills": [str(s).strip() for s in (item.get("skills") or []) if str(s).strip()],
            "classification": classification,
            "quote": str(item.get("quote") or "").strip(),
        })

    return drafts, unsourced, incomplete


def coach(resume: dict[str, Any], profile: CandidateProfile, instruction: str,
          score: dict[str, Any] | None = None,
          history: list[dict[str, str]] | None = None,
          job_id: str | None = None, title: str = "") -> dict[str, Any]:
    """One coaching turn: a reply, gated rewrites, and any evidence they stated."""
    from .llm import available, complete

    instruction = (instruction or "").strip()[:MAX_INSTRUCTION_CHARS]
    if not instruction:
        return {"ok": False, "reply": "", "proposals": [],
                "reason": "no instruction given"}

    history = history or []
    ctx = context(resume, profile, score, instruction=instruction, title=title)
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
        _prompt(ctx, instruction, history),
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

    drafts, unsourced, incomplete = _evidence_drafts(
        parsed.get("evidence"), instruction, history
    )
    if unsourced:
        logger.info("resume_coach: dropped %d evidence draft(s) with no "
                    "candidate quote behind them", unsourced)
    if incomplete:
        logger.info("resume_coach: dropped %d evidence draft(s) naming no "
                    "employer or project", incomplete)

    market = ctx["market"]
    return {
        "ok": True,
        "reply": str(parsed.get("reply") or "").strip(),
        "proposals": proposals,
        "blocked": sum(1 for p in proposals if not p["applicable"]),
        # Drafts, not writes. Confirming one is a separate call, and what it
        # writes is unapproved — see the module docstring.
        "evidenceDrafts": drafts,
        # Surfaced rather than swallowed. The model's prose sometimes counts
        # things it did not return; without this the screen simply shows fewer
        # cards than the reply promised and the candidate cannot tell why.
        "draftsNeedingDetail": incomplete,
        "groundedIn": {
            "postings": market["sampled"],
            "skill": market["skill"],
        },
        "costUsd": round(result.cost_usd, 6),
    }
