"""Practising an answer, checked against the evidence that backs it.

An interview answer is the one piece of writing in this system the candidate
composes themselves, out loud, under time pressure. That changes what the
containment gate is for.

Everywhere else it stops the *assistant* inventing a figure, and a rejection
means the whole generation is discarded. Here the author is the candidate, and
`overrides.py` already settled what that implies: **their own history is theirs,
and a figure this system cannot find is a gap in the evidence file, not a lie.**
So nothing here rejects. Every finding is a note, and the one it cannot verify
comes with an offer to record it — which turns practice into a way of
discovering evidence nobody had written down yet.

What is checked, all deterministic and with no model involved:

* **Figures.** Every number in the answer is looked for in the approved claims.
  A match names the claim and employer that back it; a miss is flagged as
  unverified. This is the check that matters, because a number is what an
  interviewer writes down and what a reference call can contradict.
* **Named things.** Employers, tools and systems mentioned mid-sentence are
  checked against the claims, the skills inventory and the candidate's own
  profile — so naming your own employer is not "invention", but naming a system
  you never touched is worth knowing before you say it to a hiring manager.
* **Length.** Measured in speaking seconds, because the failure mode of a
  behavioural answer is almost never being too short.

The coaching layer that sits on top of this lives in `critique()` and is the
only part that calls a model. It is given the evidence findings and told it may
not contradict them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any

from .profile import CandidateProfile
from .store import connect, now

# Behavioural questions are the interviewer's script, not a claim about the
# candidate, and this set is genuinely standard across every competency guide.
# Writing them down is not the same as inventing evidence — and deriving them
# from a posting, the way `interview._questions_from_requirements` does, only
# works for technical requirements. A posting does not list "tell me about a
# conflict".
BEHAVIOURAL: tuple[dict[str, str], ...] = (
    {"id": "beh-process", "competency": "Process improvement",
     "prompt": "Tell me about a time you improved a process."},
    {"id": "beh-data-decision", "competency": "Analytical judgement",
     "prompt": "Describe a time your analysis changed a decision."},
    {"id": "beh-conflict", "competency": "Working with others",
     "prompt": "Tell me about a disagreement with a stakeholder and how it ended."},
    {"id": "beh-failure", "competency": "Ownership",
     "prompt": "Tell me about something you got wrong. What happened next?"},
    {"id": "beh-ambiguity", "competency": "Dealing with ambiguity",
     "prompt": "Describe a project where the requirements were unclear."},
    {"id": "beh-deadline", "competency": "Delivery under pressure",
     "prompt": "Tell me about a time you had to deliver against a hard deadline."},
    {"id": "beh-influence", "competency": "Influence without authority",
     "prompt": "Describe a time you got people to adopt something you built."},
    {"id": "beh-quality", "competency": "Rigour",
     "prompt": "Tell me about a time you found an error others had missed."},
    {"id": "beh-scale", "competency": "Scope and scale",
     "prompt": "What is the largest or most complex thing you have owned end to end?"},
    {"id": "beh-learning", "competency": "Learning",
     "prompt": "Tell me about something technical you had to learn quickly."},
)

# Spoken pace. Interview coaching converges on 130-150 words a minute; 140 is
# the middle of that and only used to turn a word count into seconds, so the
# exact figure does not carry much weight.
WORDS_PER_MINUTE = 140

# A behavioural answer that runs past two minutes stops being an answer.
TARGET_SECONDS = (60, 105)

_FILLERS = ("um", "uh", "like", "basically", "actually", "literally", "sort of",
            "kind of", "you know", "i mean", "right", "so yeah")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_attempts (
    id            TEXT PRIMARY KEY,
    question_id   TEXT NOT NULL,
    question_text TEXT NOT NULL,
    kind          TEXT NOT NULL,
    job_id        TEXT,
    answer_text   TEXT NOT NULL,
    spoken        INTEGER NOT NULL DEFAULT 0,
    duration_s    REAL,
    findings      TEXT NOT NULL,
    critique      TEXT,
    scores        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts_question ON practice_attempts(question_id);
CREATE INDEX IF NOT EXISTS ix_attempts_created  ON practice_attempts(created_at);

CREATE TABLE IF NOT EXISTS question_research (
    question_id  TEXT PRIMARY KEY,
    shape        TEXT NOT NULL,
    sources      TEXT NOT NULL,
    researched_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = connect()
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------- the checks


def _figures(text: str) -> list[str]:
    from .compose import _FIGURE

    return [f.rstrip(".,") for f in _FIGURE.findall(text or "")]


def _claim_figures(claim) -> set[str]:
    """Every figure a claim legitimately supports, including its date range."""
    from .compose import _FIGURE, _bare_figures

    found = set(_FIGURE.findall(claim.claim))
    found |= set(_FIGURE.findall(claim.date_range or ""))
    found |= {n.split("-")[0] for n in _FIGURE.findall(claim.date_range or "")}
    return _bare_figures(found)


def _speaking_seconds(text: str, duration_s: float | None) -> float:
    """Measured when spoken, estimated when typed."""
    if duration_s:
        return round(float(duration_s), 1)
    words = len((text or "").split())
    return round(words / WORDS_PER_MINUTE * 60, 1)


def check_answer(
    answer: str,
    profile: CandidateProfile,
    *,
    duration_s: float | None = None,
    allowed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Which parts of this answer the evidence file can and cannot back.

    Nothing here is a verdict on the candidate. An unbacked figure is reported
    as unverified, never as false — the evidence file is an incomplete record of
    a career, and treating a gap in it as a fabrication would be both wrong and
    the fastest way to make someone stop using this.
    """
    from .compose import _CAPITALISED, _COMMON_CAPITALS, _bare, _word_tokens

    claims = [c for c in profile.evidence if c.approved_for_resume]

    # ---- figures -----------------------------------------------------------
    backed: list[dict[str, Any]] = []
    unverified: list[str] = []
    for figure in dict.fromkeys(_figures(answer)):   # de-duped, order kept
        holder = next((c for c in claims if figure in _claim_figures(c)), None)
        if holder is None:
            unverified.append(figure)
        else:
            backed.append({
                "figure": figure,
                "claimId": holder.claim_id,
                "employer": holder.employer,
            })

    # ---- named things ------------------------------------------------------
    known: set[str] = set()
    for claim in claims:
        known |= {_bare(w) for w in _word_tokens(claim.claim)}
        known |= {_bare(w) for w in _word_tokens(claim.employer)}
        for skill in claim.skills:
            known |= {_bare(w) for w in _word_tokens(skill)}
    for skill in getattr(profile, "all_skills", set()) or set():
        known |= {_bare(w) for w in _word_tokens(str(skill))}
    known |= {_bare(w) for w in _word_tokens(getattr(profile, "name", ""))}
    known |= {_bare(w) for w in _word_tokens(getattr(profile, "location", ""))}
    known |= {_bare(w) for w in allowed}

    unsourced = sorted({
        noun for noun in _CAPITALISED.findall(answer or "")
        if _bare(noun) not in known and _bare(noun) not in _COMMON_CAPITALS
    })

    # ---- delivery ----------------------------------------------------------
    words = len((answer or "").split())
    seconds = _speaking_seconds(answer, duration_s)
    # Word boundaries, not padded substrings: "So, um, at Freyr" has a comma
    # after "um", so " um " never matched and the count read 0 on an answer
    # visibly full of them.
    fillers = {}
    for filler in _FILLERS:
        hits = len(re.findall(rf"\b{re.escape(filler)}\b", (answer or "").lower()))
        if hits:
            fillers[filler] = hits

    if seconds < TARGET_SECONDS[0]:
        length = "short"
    elif seconds > TARGET_SECONDS[1]:
        length = "long"
    else:
        length = "good"

    return {
        "backedFigures": backed,
        "unverifiedFigures": unverified,
        "unsourcedNames": unsourced,
        "words": words,
        "seconds": seconds,
        "length": length,
        "targetSeconds": list(TARGET_SECONDS),
        "fillerWords": fillers,
        "fillerCount": sum(fillers.values()),
    }


# ---------------------------------------------------------- researched shape


def save_research(question_id: str, shape: dict[str, Any], sources: list[dict]) -> None:
    """What a strong answer to this question looks like, and where that came from.

    **Sources are mandatory**, exactly as in `interview_intel.save_intel`. This is
    the one part of the practice surface that is not derived from the candidate's
    own evidence — it is craft knowledge taken from outside — so it has to carry
    its provenance and be shown with it. Advice with no source is indistinguishable
    from advice a model invented, and the whole design here rests on being able to
    tell those apart.
    """
    if not sources:
        raise ValueError(
            f"refusing to store research for {question_id!r} with no sources"
        )
    with _connect() as conn:
        conn.execute(
            """INSERT INTO question_research (question_id, shape, sources, researched_at)
               VALUES (?,?,?,?)
               ON CONFLICT(question_id) DO UPDATE SET
                 shape=excluded.shape, sources=excluded.sources,
                 researched_at=excluded.researched_at""",
            (question_id, json.dumps(shape), json.dumps(sources), now()),
        )


def get_research(question_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM question_research WHERE question_id=?", (question_id,)
        ).fetchone()
    if not row:
        return None
    return {
        "questionId": row["question_id"],
        "researchedAt": row["researched_at"],
        "sources": json.loads(row["sources"]),
        **json.loads(row["shape"]),
    }


# ------------------------------------------------------------- the coaching

RUBRIC = """You are an interview coach reviewing one spoken answer to one
behavioural question. You are blunt, specific and short. You are not encouraging
for its own sake.

Judge only what is in front of you. You are given a list of figures the
candidate's evidence file verifies and a list it does not.

Hard rules:
- Never credit the candidate with an achievement that is not in their answer.
- Never call an unverified figure false. It means the evidence file has no
  record of it, not that it did not happen. If you mention one at all, say it
  is unverified and suggest recording it.
- Do not invent employers, tools, dates, metrics or job titles.
- Do not praise generically. "Good structure" without saying which part is
  worthless.
- An answer that is mostly true but has no specifics is a weak answer. Say so.

Return only JSON, no prose around it:

{"verdict": "<one sentence, the single most useful thing to fix>",
 "strengths": ["<what specifically worked, quoting the answer>"],
 "fixes": ["<what to change, concretely>"],
 "followUps": ["<what an interviewer would ask next, given this answer>"],
 "scores": {"structure": 0-10, "specificity": 0-10, "evidence": 0-10, "length": 0-10}}"""


def critique(
    question: str,
    answer: str,
    findings: dict[str, Any],
    *,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """Coaching on one answer. `None` when no model is available or affordable.

    The caller always has the deterministic `findings` to show, so a missing
    critique degrades to "here is what your evidence backs" rather than to an
    error — the same contract `compose_email` has with its template fallback.
    """
    from .compose import _parse
    from .llm import complete

    backed = ", ".join(
        f"{b['figure']} (from {b['employer']})" for b in findings["backedFigures"]
    ) or "none"
    unverified = ", ".join(findings["unverifiedFigures"]) or "none"

    prompt = "\n".join([
        "QUESTION:",
        question,
        "",
        "THE CANDIDATE'S ANSWER:",
        answer,
        "",
        "FIGURES THEIR EVIDENCE FILE VERIFIES:",
        backed,
        "",
        "FIGURES IT HAS NO RECORD OF (unverified, NOT false):",
        unverified,
        "",
        "DELIVERY:",
        f"{findings['words']} words, about {findings['seconds']} seconds spoken "
        f"(target {findings['targetSeconds'][0]}-{findings['targetSeconds'][1]}s), "
        f"{findings['fillerCount']} filler words.",
    ])

    result = complete(
        prompt, purpose="interview_critique", system=RUBRIC,
        job_id=job_id, max_tokens=1200,
    )
    if result is None:
        return None
    return _parse(result.text)


# ------------------------------------------------------------------- storage


def save_attempt(
    *,
    question_id: str,
    question_text: str,
    kind: str,
    answer: str,
    findings: dict[str, Any],
    critique_payload: dict[str, Any] | None,
    job_id: str | None = None,
    spoken: bool = False,
    duration_s: float | None = None,
) -> dict[str, Any]:
    attempt_id = f"att_{uuid.uuid4().hex[:16]}"
    scores = (critique_payload or {}).get("scores") or {}
    with _connect() as conn:
        conn.execute(
            """INSERT INTO practice_attempts
               (id, question_id, question_text, kind, job_id, answer_text, spoken,
                duration_s, findings, critique, scores, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_id, question_id, question_text, kind, job_id, answer,
             1 if spoken else 0, duration_s, json.dumps(findings),
             json.dumps(critique_payload) if critique_payload else None,
             json.dumps(scores), now()),
        )
    return {"id": attempt_id, "createdAt": now()}


def _row_to_attempt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "questionId": row["question_id"],
        "questionText": row["question_text"],
        "kind": row["kind"],
        "jobId": row["job_id"],
        "answer": row["answer_text"],
        "spoken": bool(row["spoken"]),
        "durationSeconds": row["duration_s"],
        "findings": json.loads(row["findings"]),
        "critique": json.loads(row["critique"]) if row["critique"] else None,
        "scores": json.loads(row["scores"]) if row["scores"] else {},
        "createdAt": row["created_at"],
    }


def list_attempts(question_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        if question_id:
            rows = conn.execute(
                "SELECT * FROM practice_attempts WHERE question_id=? "
                "ORDER BY created_at DESC LIMIT ?", (question_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM practice_attempts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_attempt(r) for r in rows]


def overview() -> dict[str, Any]:
    """Readiness per question, and the streak. GO / NO-GO, per the launch poll."""
    attempts = list_attempts(limit=500)
    by_question: dict[str, list[dict[str, Any]]] = {}
    for a in attempts:
        by_question.setdefault(a["questionId"], []).append(a)

    systems = []
    for q in BEHAVIOURAL:
        tries = by_question.get(q["id"], [])
        best = max((sum(t["scores"].values()) / 4 for t in tries if t["scores"]),
                   default=None)
        systems.append({
            "id": q["id"],
            "competency": q["competency"],
            "prompt": q["prompt"],
            "attempts": len(tries),
            "best": round(best, 1) if best is not None else None,
            # A question never attempted is NO-GO. Not knowing is not the same
            # as being ready, and the launch poll does not let it pass as one.
            "status": "GO" if (best or 0) >= 7 else ("HOLD" if tries else "NO-GO"),
            "lastAttempt": tries[0]["createdAt"] if tries else None,
        })

    days = sorted({str(a["createdAt"])[:10] for a in attempts}, reverse=True)
    streak = 0
    if days:
        from datetime import date, timedelta

        cursor = date.fromisoformat(days[0])
        # A streak counts back from the most recent day practised, so finishing
        # the day does not silently reset it before midnight.
        for day in days:
            if date.fromisoformat(day) == cursor:
                streak += 1
                cursor -= timedelta(days=1)
            else:
                break

    return {
        "systems": systems,
        "go": sum(1 for s in systems if s["status"] == "GO"),
        "total": len(systems),
        "attempts": len(attempts),
        "streakDays": streak,
        "daysPractised": len(days),
    }


# ------------------------------------------------- the answer in your own facts

DRAFT_VOICE = """You are drafting one spoken interview answer for a candidate,
using only accomplishments they have supplied. This is a script they will say out
loud, so write it plainly: short sentences, first person, no adjectives that carry
no information.

Structure it as Situation, Task, Action, Result, without labelling those parts out
loud. Roughly 60-70% of it must be Action — what they actually did, in order.
Target 130 to 200 words, which is 60 to 90 seconds spoken.

Hard rules:
- Use ONLY the accomplishments supplied. Do not invent an employer, a tool, a
  date, a metric or a job title.
- Every figure you write must appear verbatim in the accomplishment you cite.
  Reproduce it EXACTLY as written, including any "+", "%" or decimal — "100+" is
  not "100", and dropping the plus is treated as a different figure.
- A figure may only appear in a sentence that cites the accomplishment containing
  it. Do not carry a number from one accomplishment into a sentence citing
  another.
- Do not upgrade their role. If an accomplishment says "supported", you may not
  write "led" or "owned".
- Every sentence containing a number or a proper noun must cite the accomplishment
  it came from.

Return only JSON, no prose around it:

{"answer": "<the full answer as one string>",
 "sentences": [{"text": "<each sentence of the answer>", "claim_id": "<id, or empty if it asserts nothing checkable>"}]}"""


def _claims_for(profile: CandidateProfile, question: dict[str, str], limit: int = 3):
    """Claims most likely to answer this competency, best first.

    Overlap between the question's own words and the claim, which is crude but
    honest — and crucially it never *invents* relevance. A question with no
    matching evidence returns nothing, and the caller says so rather than
    drafting an answer out of whatever was nearest.
    """
    from .compose import _bare, _word_tokens

    wanted = {_bare(w) for w in _word_tokens(
        f"{question['competency']} {question['prompt']}")} - _STOPWORDS
    scored = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        if claim.classification != "PRESENT_AND_EXPLICIT":
            continue
        tokens = {_bare(w) for w in _word_tokens(claim.claim)}
        tokens |= {_bare(w) for s in claim.skills for w in _word_tokens(s)}
        overlap = len(wanted & tokens)
        # A claim carrying a metric is worth more here: the commonest failure of
        # a behavioural answer is having no number in the Result.
        scored.append((overlap + (2 if claim.metrics else 0), claim))
    scored.sort(key=lambda t: -t[0])
    return [c for score, c in scored[:limit] if score > 0]


_STOPWORDS = frozenset({
    "tell", "me", "about", "a", "time", "you", "your", "the", "and", "or", "of",
    "to", "in", "on", "with", "what", "how", "describe", "is", "was", "were",
    "have", "has", "had", "it", "that", "this", "for", "from", "at", "by",
})


def draft_answer(
    question: dict[str, str], profile: CandidateProfile
) -> dict[str, Any] | None:
    """A model answer built from this candidate's own claims, or `None`.

    The gate runs at full strength here, unlike `check_answer`. The author is the
    assistant, not the candidate, so an invented figure is exactly the failure
    `compose.verify_sentences` exists to stop and the whole draft is discarded
    rather than the offending sentence — a script with one sentence cut out is
    not a script. There is no fallback template, because a generic STAR answer
    with no facts in it would be worse than showing the claims and letting the
    candidate assemble them.
    """
    from .compose import _parse, verify_sentences
    from .llm import complete

    claims = _claims_for(profile, question)
    if not claims:
        return None

    supplied = {c.claim_id: c for c in claims}
    allowed = frozenset(
        w.lower() for c in claims for w in (c.employer or "").split()
    )

    body = "\n".join(
        [f"QUESTION:\n{question['prompt']}", "",
         "ACCOMPLISHMENTS YOU MAY WRITE ABOUT (verbatim; do not embellish):"]
        + [f"[{c.claim_id}] ({c.employer}) {c.claim}" for c in claims]
    )

    # Three attempts: the gate is strict about exact figure reproduction and a
    # single dropped "+" costs a whole draft.
    for _ in range(3):
        result = complete(
            body, purpose="interview_draft", system=DRAFT_VOICE, max_tokens=1500
        )
        if result is None:
            return None
        parsed = _parse(result.text)
        if not parsed or not parsed.get("answer"):
            continue
        rejections, warnings = verify_sentences(
            parsed.get("sentences") or [], supplied, allowed
        )
        if rejections:
            continue
        return {
            "answer": parsed["answer"],
            "claims": [
                {"claimId": c.claim_id, "employer": c.employer, "claim": c.claim}
                for c in claims
            ],
            "reviewNotes": warnings,
        }
    return None
