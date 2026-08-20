#!/usr/bin/env python3
"""Seed the researched shape of a strong answer, per behavioural question.

    ./.venv/bin/python scripts/seed_question_research.py [--dry-run]

This is the one part of interview practice that does **not** come from the
candidate's own evidence. It is craft knowledge — what an interviewer is
assessing, how a strong answer is structured, and which traps sink an otherwise
true story — gathered from published interview guidance and stored **with its
sources**, which `save_research` refuses to skip.

The separation matters and is the whole point. The *shape* is general and
sourced; the *substance* is the candidate's own claims. Nothing here contains a
story, an employer or a figure — filling the shape is a different step, and it
draws only on `career_evidence.json`.

Re-runnable: `save_research` upserts on question id.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.interview_practice import BEHAVIOURAL, save_research  # noqa: E402

# Applies to every behavioural answer. Kept separate from the per-question notes
# so the same finding is not restated ten times and can be corrected once.
GENERAL_SOURCES = [
    {"title": "How to Use the STAR Interview Response Technique",
     "url": "https://www.indeed.com/career-advice/interviewing/how-to-use-the-star-interview-response-technique"},
    {"title": "The STAR Method: Complete Guide to Behavioral Questions",
     "url": "https://blog.theinterviewguys.com/the-star-method/"},
    {"title": "How to Avoid Common STAR Method Mistakes in Interviews",
     "url": "https://www.linkedin.com/advice/1/how-can-you-avoid-common-star-method-mistakes-skills-interviewing"},
]

UNIVERSAL_STRUCTURE = [
    "Situation — one or two sentences. Enough context to make the problem legible, no more.",
    "Task — what specifically was yours. Say 'I', not 'we'; the interviewer is hiring one person.",
    "Action — 60-70% of the answer. What you actually did, in order, naming the systems and the decisions.",
    "Result — the outcome with a number where one exists, and what changed because of it.",
]

UNIVERSAL_TRAPS = [
    "Spending most of the answer on Situation and rushing Action — the single most common failure, and Action is the only part that shows what you can do.",
    "Saying 'we' throughout, so the interviewer cannot tell what you did.",
    "No specifics: no system names, no numbers, no decisions with alternatives.",
    "Rambling past two minutes. One to two minutes is the target.",
    "Overstating. A figure that does not survive a follow-up question or a reference call costs more than a modest true one.",
]

TIMING = "60-105 seconds. Roughly 60-70% of it on Action."

# Per-question: what is actually being assessed, and the traps specific to it.
QUESTIONS: dict[str, dict] = {
    "beh-process": {
        "assesses": "Whether you can find a problem worth solving, and whether the improvement was measured or merely felt.",
        "specificTraps": [
            "Describing the new process without describing the old one — the size of the improvement is unreadable without the baseline.",
            "A result with no number, when this is the question most likely to have one.",
        ],
    },
    "beh-data-decision": {
        "assesses": "Whether analysis actually changed anything, or was produced and ignored. The decision is the point, not the analysis.",
        "specificTraps": [
            "Stopping at 'I built the dashboard'. Nobody asked what you built; they asked what changed.",
            "Not naming who made the decision, or what they would have done otherwise.",
        ],
    },
    "beh-conflict": {
        "assesses": "Whether you stay professional under disagreement and look for a resolution rather than a winner. Hiring managers are screening out people who will turn small issues into large ones.",
        "specificTraps": [
            "Blaming or bad-mouthing the other person. This reads as a lack of professionalism and is the most common way this answer fails.",
            "Framing it as having 'taken control' or convinced them you were right — focusing on who was to blame rather than on the resolution.",
            "Choosing a conflict so trivial it shows nothing.",
        ],
        "extraSources": [
            {"title": "Interview Question: How Do You Handle Conflict With Coworkers?",
             "url": "https://www.indeed.com/career-advice/interviewing/handle-conflict-with-coworkers-question"},
            {"title": "Top Conflict Interview Questions to Spot Leadership Potential",
             "url": "https://www.dice.com/hiring/recruitment/common-conflict-with-a-coworker-interview-questions"},
        ],
    },
    "beh-failure": {
        "assesses": "Accountability and whether you learn. Answer it well and the interviewer remembers how you recovered, not that you failed.",
        "specificTraps": [
            "Shifting blame. The answer has to show you owning it.",
            "A disguised humblebrag — 'I care too much' — which reads as evasion and wastes the question.",
            "Picking a failure that is a red flag rather than a mistake: unethical conduct, recklessness, or a pattern rather than an incident.",
            "Inventing one. This question gets probed harder than any other.",
        ],
        "extraSources": [
            {"title": "How to Answer 'Tell Me About a Time You Failed' in a Job Interview",
             "url": "https://hbr.org/2023/01/how-to-answer-tell-me-about-a-time-you-failed-in-a-job-interview"},
            {"title": "How to Answer 'Tell Me About a Time You Failed'",
             "url": "https://careersidekick.com/time-when-you-failed/"},
        ],
    },
    "beh-ambiguity": {
        "assesses": "What you do when nobody can tell you what 'done' means — whether you go and find out, or wait.",
        "specificTraps": [
            "Describing the ambiguity at length and never saying how you resolved it.",
            "Implying you simply guessed, rather than narrowing it deliberately.",
        ],
    },
    "beh-deadline": {
        "assesses": "Prioritisation and judgement under pressure — specifically what you chose to drop, since a hard deadline always means dropping something.",
        "specificTraps": [
            "Heroism: working all night is not a method and does not scale.",
            "Not naming the trade-off. If nothing was cut, there was no real deadline pressure.",
        ],
    },
    "beh-influence": {
        "assesses": "Whether you can get something adopted without the authority to mandate it — which is most of the job.",
        "specificTraps": [
            "Stopping at shipping it. Built is not adopted.",
            "No evidence anyone used it: no usage, no named adopter, no change in their behaviour.",
        ],
    },
    "beh-quality": {
        "assesses": "Rigour, and what you did after finding the error — whether you fixed the instance or the cause.",
        "specificTraps": [
            "Making it about someone else's incompetence.",
            "Fixing the one error and not saying what stops it recurring.",
        ],
    },
    "beh-scale": {
        "assesses": "The real ceiling of what you have owned. This is the question where overstating is most easily caught, because scope invites follow-ups.",
        "specificTraps": [
            "Claiming ownership of something you contributed to. 'Supported' and 'led' are different, and the follow-up questions find out which.",
            "Describing size without complexity — a large simple thing is not the same as a hard one.",
        ],
    },
    "beh-learning": {
        "assesses": "How you learn under time pressure, and whether you can be trusted with something unfamiliar.",
        "specificTraps": [
            "Naming the technology and not the method — how you learned it is the answer.",
            "Choosing something trivial, which answers a smaller question than the one asked.",
        ],
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    known = {q["id"] for q in BEHAVIOURAL}
    unknown = set(QUESTIONS) - known
    missing = known - set(QUESTIONS)
    if unknown:
        print(f"ERROR: research for questions that do not exist: {sorted(unknown)}")
        return 1
    if missing:
        # A question with no researched shape renders an empty panel, so this is
        # reported rather than left for someone to notice in the UI.
        print(f"WARNING: no research for {sorted(missing)}")

    written = 0
    for question in BEHAVIOURAL:
        entry = QUESTIONS.get(question["id"])
        if not entry:
            continue
        shape = {
            "assesses": entry["assesses"],
            "structure": UNIVERSAL_STRUCTURE,
            "traps": entry["specificTraps"] + UNIVERSAL_TRAPS,
            "timing": TIMING,
        }
        sources = GENERAL_SOURCES + entry.get("extraSources", [])
        print(f"{'DRY ' if args.dry_run else ''}{question['id']:20} "
              f"{len(shape['traps'])} traps, {len(sources)} sources")
        if not args.dry_run:
            save_research(question["id"], shape, sources)
            written += 1

    if not args.dry_run:
        print(f"\nwrote research for {written} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
