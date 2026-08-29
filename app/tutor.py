"""Teaching a lesson, and letting the learner interrupt.

Why this is a separate module
-----------------------------
`technical_learning` calls no model anywhere. Its package docstring says
"Deterministic technical interview learning and assessment", the word appears in
its API responses, and the UI prints "deterministic feedback" and "deterministic
score / 100" to the person using it. Putting the first LLM call inside that
package would quietly falsify a claim the product makes on screen. The lessons
and their spines live there; the thing that generates prose lives here.

What bounds it
--------------
`Lesson.key_points` is the set of facts the tutor may assert. It may rephrase
them, reorder them, illustrate them, translate them into a different domain and
say them at three different levels of detail. It may not add a new one.

This is the same device used three times already in this codebase:
`compose.select_evidence` picks the claims before the prompt exists,
`resume_coach` hands the model one source claim and rejects anything reaching
past it, and `concepts.save_note` refuses a definition with no source. The
reason is sharper here than anywhere else: a tutor invents fluently, and the
person being taught is by definition the one least able to notice.

So the system prompt states the boundary, and a refusal is a valid answer. Asked
about something outside the lesson, it says the lesson does not cover it rather
than obliging — which is also better teaching, because it tells the learner the
question was a good one and belongs somewhere else.

Why the first pass is stored
----------------------------
`usage.py` caps spend at $2.00 a day, and total spend across this project's life
is around $2.18. A tutor is chatty by design. If every re-read of a lesson cost a
call, reading four lessons twice would be most of a day's budget on prose that
had already been written.

So `teach` is generated once per lesson and stored; re-reading is free and
identical. Only the interruptions — simpler, deeper, another example, stuck on a
step — cost anything, which is the right shape: the money follows the moments
where a person actually needs something they have not already been given.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .db import connect

logger = logging.getLogger(__name__)

MODES = ("teach", "simpler", "deeper", "example", "stuck")

VOICE = """You are teaching one topic to one person, out loud, the way a good
tutor does at a whiteboard. They have told you they learn best when something is
explained, then said again more simply without losing the core, then gone into
properly.

THE BOUNDARY, which matters more than anything else here:

You are given KEY POINTS. They are the complete set of facts you may assert.
You may rephrase them, reorder them, build an analogy for them, put them in a
different domain, or spend an entire answer on one of them. You may not add a
fact that is not there — not a number, not a syntax detail, not a claim about
how a system behaves, however confident you are and however small it seems.

If asked something the key points do not cover, say so plainly and briefly, and
say where it would belong. "That is a real question and this lesson does not
cover it — it belongs with indexes" is a good answer. Inventing a plausible one
is the only thing you must never do, because the person you are teaching cannot
tell the difference and is trusting you not to make them learn something false.

HOW TO TEACH:

- Start from what the thing IS, in one plain sentence, before any detail.
- Then build. Take the key points in an order that makes each one land, not the
  order you were given them in.
- Use the worked example. Refer to actual columns and numbers from it rather
  than saying "for instance".
- Name the misconception where it naturally arises, as "the thing people get
  wrong here is", not as a list at the end.
- Short paragraphs. No bullet lists unless enumerating genuinely parallel items.
- Never say "as we discussed" or "as you know". You are teaching it.
- Do not open by restating the question or naming the topic back. Begin.

Write plain prose. Markdown headings are noise at this length; a code block for
actual code is fine."""

MODE_INSTRUCTION = {
    "teach": "Teach this lesson from the beginning. Six to ten short paragraphs.",
    "simpler": (
        "Say the same thing again, simpler — and simpler does NOT mean shorter or "
        "vaguer. Keep every load-bearing idea; replace the jargon, slow the steps "
        "down, and use a concrete everyday comparison. If you drop a core idea to "
        "make it read easily you have failed at the only thing being asked for."
    ),
    "deeper": (
        "Go one level under what you just said — the mechanism, why it works that "
        "way, what it costs. Still only from the key points."
    ),
    "example": (
        "Give a different concrete example of the same idea. If the learner names "
        "a domain, use it. Walk through it step by step rather than presenting it "
        "finished."
    ),
    "stuck": (
        "The learner has named the exact place they lost the thread. Re-teach only "
        "that, from a different angle, in detail. Do not re-teach the whole lesson."
    ),
}


def _spine(lesson: Any) -> str:
    """Everything the tutor is allowed to know, laid out."""
    parts = [
        f"LESSON: {lesson.title}",
        f"LEVEL: {lesson.level}",
        f"WHY IT MATTERS: {lesson.hook}",
        "",
        "KEY POINTS — the complete set of facts you may assert:",
    ]
    parts += [f"  {i}. {point}" for i, point in enumerate(lesson.key_points, 1)]

    if lesson.objectives:
        parts += ["", "BY THE END THEY SHOULD BE ABLE TO:"]
        parts += [f"  - {o}" for o in lesson.objectives]

    if lesson.example:
        parts += ["", "WORKED EXAMPLE — refer to it concretely:",
                  f"  {lesson.example.caption}"]
        if lesson.example.sql:
            parts.append(f"  query: {lesson.example.sql}")
        if lesson.example.body:
            parts.append(f"  note: {lesson.example.body}")

    if lesson.misconceptions:
        parts += ["", "MISCONCEPTIONS — raise these where they naturally arise:"]
        for m in lesson.misconceptions:
            parts.append(f'  believed: "{m.claim}" — actually: {m.correction}')

    if lesson.interview_angle:
        parts += ["", f"HOW IT GETS ASKED: {lesson.interview_angle}"]
    return "\n".join(parts)


def _personal(profile: Any, lesson: Any) -> str:
    """The learner's own work, when the lesson has something to attach to.

    Claims are quoted from the evidence file, never characterised. The tutor may
    say "you built this — here is how the idea applies to it", and may not say
    anything about their experience that is not in front of it.
    """
    if profile is None:
        return ""
    wanted = {lesson.skill.lower(), lesson.concept.lower().replace("-", " ")}
    hits: list[str] = []
    for claim in getattr(profile, "evidence", []) or []:
        if not getattr(claim, "approved_for_resume", False):
            continue
        skills = {str(s).lower() for s in (getattr(claim, "skills", None) or [])}
        if skills & wanted:
            hits.append(f"  ({claim.employer}) {claim.claim}")
        if len(hits) >= 2:
            break
    if not hits:
        return ""
    return (
        "\n\nTHE LEARNER'S OWN WORK, quoted from their evidence file. You may "
        "connect the idea to it — that is the version that sticks. Quote it "
        "accurately and claim nothing about their experience beyond these lines:\n"
        + "\n".join(hits)
    )


def _cached(lesson_id: str) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT body FROM lesson_pass WHERE lesson_id=? AND mode='teach'",
            (lesson_id,),
        ).fetchone()
    return row["body"] if row else ""


def _store(lesson_id: str, body: str) -> None:
    from datetime import datetime, timezone

    with connect() as conn:
        conn.execute(
            "INSERT INTO lesson_pass (lesson_id, mode, body, created_at) "
            "VALUES (?,'teach',?,?) ON CONFLICT(lesson_id, mode) DO UPDATE SET "
            "body=excluded.body, created_at=excluded.created_at",
            (lesson_id, body, datetime.now(timezone.utc).isoformat()),
        )


def teach(lesson: Any, mode: str = "teach", *, message: str = "",
          history: list[dict[str, str]] | None = None,
          profile: Any = None, force: bool = False) -> dict[str, Any]:
    """One teaching turn. `teach` is served from storage after the first time."""
    from .llm import available, complete

    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")

    if mode == "teach" and not force:
        cached = _cached(lesson.id)
        if cached:
            # Free, and identical to what was read last time — a lesson that
            # rewords itself on every visit is a lesson you cannot revise from.
            return {"ok": True, "mode": mode, "body": cached,
                    "cached": True, "costUsd": 0.0}

    ready, why = available()
    if not ready:
        return {"ok": False, "mode": mode, "body": "", "reason": why}

    prompt = [_spine(lesson) + _personal(profile, lesson), ""]
    for turn in (history or [])[-6:]:
        who = "learner" if turn.get("role") == "user" else "you"
        prompt.append(f"{who}: {str(turn.get('content', ''))[:600]}")
    prompt += ["", MODE_INSTRUCTION[mode]]
    if message.strip():
        prompt.append(f"THE LEARNER SAYS: {message.strip()[:600]}")

    result = complete(
        "\n".join(prompt),
        purpose="tutor",
        system=VOICE,
        max_tokens=2000,
    )
    if result is None:
        return {"ok": False, "mode": mode, "body": "",
                "reason": "the request to the model did not complete"}

    body = (result.text or "").strip()
    if not body:
        return {"ok": False, "mode": mode, "body": "",
                "reason": "the model returned nothing"}

    if mode == "teach":
        _store(lesson.id, body)

    return {"ok": True, "mode": mode, "body": body, "cached": False,
            "costUsd": round(result.cost_usd, 6)}
