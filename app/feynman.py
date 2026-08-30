"""You explain it back, and something reads what you actually said.

The hole this fills
-------------------
`lessons.mark_explained` writes `state='explained'` and reads nothing. It is a
button you press about yourself. Every other half of the loop exists — the tutor
teaches, says it again simpler, goes deeper, re-teaches the one step you name —
and all of it flows towards you. Nothing ever flowed back.

That missing direction is the whole of the Feynman technique. The point is not
that explaining is a nice way to revise; it is that the *attempt* to explain is
the diagnostic. You do not find out you cannot explain grain until you try, and
the sentence where you go vague is the sentence that names the gap.

Why not `grade_rubric`
----------------------
`technical_learning.grading.grade_rubric` already reads free text against a
rubric, and reusing it here would be free. It cannot be used. It scores
`term.casefold() in text.casefold()` per element and passes at 70%, so an answer
that names every rubric term while negating all of them scores 1.0. On a drill
that is a bad grade. On a Feynman explanation it is actively harmful: it would
tell you that you understand a thing you just stated backwards, which is the one
error the technique exists to catch. `tests/test_feynman.py` pins exactly that
string as a case.

What bounds it
--------------
The same spine as the tutor — `Lesson.key_points` — but used in the opposite
direction, and with a stronger guarantee.

The model does not write the gaps. It returns *indices* into the key points, and
anything that is not a valid index is dropped before the caller sees it. So a
gap it hallucinates has nowhere to live: there is no field in the response where
free-form invented criticism can survive. The only prose it may write is a short
quotation of the learner's own words, and that is checked against the
explanation it was given.

This matters more here than in the tutor. A tutor that invents a fact teaches
you something false. A checker that invents a gap tells you that you failed to
understand something that was never in the lesson — and you will believe it,
because you already suspect you did not understand.

Never a score
-------------
There is no number and no pass mark. Three buckets:

    carried    the explanation got this across
    missed     absent — which is not the same as wrong
    backwards  stated inverted; the only real failure

Ranking them on a scale would invite the thing the whole design avoids: making
"did I pass" the question instead of "what did I not say".
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Enough to tell whether someone explained the idea; short enough that the
# check stays cheap and the person is not writing an essay between applications.
MAX_EXPLANATION = 2400
MIN_EXPLANATION = 40

JUDGE = """You are checking whether someone's explanation of a topic carried the
ideas it needed to. You are not marking it. You never give a score.

You are given KEY POINTS, numbered. They are the complete set of ideas that can
be judged present or absent. You may not judge the explanation against anything
else — not style, not length, not an idea you happen to know that is not in the
list, however important it seems.

For each key point, decide exactly one of:

  carried    the explanation gets this idea across, in whatever words. Accept a
             plain, non-technical or analogical phrasing as carrying it — that
             is the person doing the exercise correctly, not failing at it.
  missed     the explanation does not address this idea. Absent, not wrong.
  backwards  the explanation states this idea inverted, or asserts something
             this key point directly contradicts. Be strict here and reserve it
             for a real contradiction, because it is the only category that says
             the person believes something false.

Judge the ideas, not the vocabulary. Someone who says "you end up counting the
same loan once for every payment it has" has carried a point about join fan-out
changing the grain, even though they used none of those words. Someone who lists
the right terms in a sentence that denies them has NOT carried anything — the
terms being present is not evidence, the idea being present is.

Return ONLY a JSON object, no prose around it:

{"carried": [1, 4], "missed": [2], "backwards": [3],
 "quote": {"3": "the exact words from their explanation that state it backwards"},
 "next": 3}

Rules for the fields:
- Every key point number appears in exactly one of carried/missed/backwards.
- "quote" is optional, only for backwards points, and must be a verbatim span
  copied from their explanation. Never paraphrase into it.
- "next" is the single point most worth teaching again — a backwards one if
  there is any, otherwise the missed one the rest depends on. Omit if the
  explanation carried everything."""


def _spine(points: list[str], title: str) -> str:
    lines = [f"TOPIC: {title}", "", "KEY POINTS:"]
    lines += [f"  {i}. {p}" for i, p in enumerate(points, 1)]
    return "\n".join(lines)


def _parse(raw: str, count: int, explanation: str) -> dict[str, Any] | None:
    """Read the model's answer, and throw away anything it made up.

    Every guarantee this module offers is enforced here rather than asked for in
    the prompt, because a prompt is a request and this is a boundary.
    """
    text = (raw or "").strip()
    # Models fence JSON often enough that failing on it would be a bug in this
    # function rather than a real refusal.
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if not fence:
        return None
    try:
        data = json.loads(fence.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    valid = range(1, count + 1)
    seen: set[int] = set()
    buckets: dict[str, list[int]] = {"carried": [], "missed": [], "backwards": []}
    for name in buckets:
        for item in data.get(name) or []:
            try:
                n = int(item)
            except (TypeError, ValueError):
                continue
            # An index outside the key points is an invented gap. There is
            # nowhere for it to go.
            if n in valid and n not in seen:
                seen.add(n)
                buckets[name].append(n)

    # A point the model forgot to classify is missing information, not a pass.
    # Calling it "carried" would be the failure mode this whole module exists to
    # prevent, so silence counts as missed.
    for n in valid:
        if n not in seen:
            buckets["missed"].append(n)
    for name in buckets:
        buckets[name].sort()

    quotes: dict[int, str] = {}
    haystack = " ".join(explanation.split()).casefold()
    for key, value in (data.get("quote") or {}).items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            continue
        if n not in buckets["backwards"] or not isinstance(value, str):
            continue
        # It must be the learner's own words. A paraphrase here would read as a
        # quotation and be trusted as one.
        if " ".join(value.split()).casefold() in haystack:
            quotes[n] = value.strip()

    nxt = data.get("next")
    try:
        nxt = int(nxt)
    except (TypeError, ValueError):
        nxt = None
    if nxt not in valid or nxt in buckets["carried"]:
        nxt = (buckets["backwards"] or buckets["missed"] or [None])[0]

    buckets["quotes"] = quotes  # type: ignore[assignment]
    buckets["next"] = nxt  # type: ignore[assignment]
    return buckets


def check(*, points: list[str], title: str, explanation: str) -> dict[str, Any]:
    """Read an explanation against a spine. Returns buckets, never a score."""
    from .llm import available, complete

    said = (explanation or "").strip()
    if len(said) < MIN_EXPLANATION:
        return {
            "ok": False,
            "reason": "say a bit more — a sentence or two is not enough to check",
        }
    if not points:
        return {"ok": False, "reason": "nothing is written to check this against"}

    ready, why = available()
    if not ready:
        return {"ok": False, "reason": why}

    prompt = "\n".join([
        _spine(points, title),
        "",
        "THEIR EXPLANATION:",
        said[:MAX_EXPLANATION],
    ])
    result = complete(
        prompt,
        purpose="feynman",
        system=JUDGE,
        # Indices and a short quotation per inverted point. 600 was not enough:
        # an explanation that got most of a seven-point lesson backwards hit the
        # ceiling exactly, the JSON came back unterminated, and `_parse` refused
        # it. That refusal is the right behaviour and the truncation was the bug
        # — the worst answers are the longest ones to describe, so the ceiling
        # has to clear the worst case, not the typical one.
        max_tokens=1200,
    )
    if result is None:
        return {"ok": False, "reason": "the request to the model did not complete"}

    parsed = _parse(result.text or "", len(points), said)
    if parsed is None:
        logger.warning("feynman: unparseable response for %r", title)
        return {"ok": False, "reason": "the check did not come back readable"}

    def described(numbers: list[int]) -> list[dict[str, Any]]:
        return [
            {"n": n, "point": points[n - 1], "quote": parsed["quotes"].get(n)}
            for n in numbers
        ]

    backwards = described(parsed["backwards"])
    missed = described(parsed["missed"])
    nxt = parsed["next"]

    return {
        "ok": True,
        "carried": described(parsed["carried"]),
        "missed": missed,
        "backwards": backwards,
        # The one thing worth being taught again, which the caller hands
        # straight to the tutor's `stuck` mode.
        "next": (
            {"n": nxt, "point": points[nxt - 1]} if nxt else None
        ),
        "settled": not backwards and not missed,
        "costUsd": round(result.cost_usd, 6),
    }
