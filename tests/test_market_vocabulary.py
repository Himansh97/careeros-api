"""Market vocabulary is register, and must not become a source of claims.

Two separate risks live here.

The first is quality. This module exists because the coach wrote "utilised
Python for data manipulation" — the sentence of someone who has not done the
work. If it hands back the same posting padding it was meant to replace
("proficiency in", "hands-on", "advanced"), it has changed nothing and cost a
prompt's worth of tokens to do it.

The second is correctness in the boring sense: several skill names are ordinary
English words. Matching "excel" as a substring found every posting seeking a
candidate who would "excel at" the role and returned dental and parental leave
as Excel's vocabulary. Anything built on that would read as though it had been
assembled by someone who had never seen a spreadsheet.

    ./.venv/bin/python tests/test_market_vocabulary.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import market  # noqa: E402


def posting(title: str, description: str) -> dict[str, str]:
    return {"title": title, "description": description}


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # --- the substring bug, in isolation ---
    check("'excel at' does not mention Excel",
          market._mentions("candidates who excel at ambiguity", "excel"), False)
    check("'excellent' does not mention Excel",
          market._mentions("excellent communication skills", "excel"), False)
    check("'Excel' does mention Excel",
          market._mentions("Advanced Excel including pivot tables", "excel"), True)
    check("'R' does not mention every word with an r",
          market._mentions("strong performance culture", "r"), False)
    check("'R' standing alone does",
          market._mentions("modelling in R and Python", "r"), True)

    # --- padding must not survive as vocabulary ---
    padded = [
        posting("Data Analyst", f"Proficiency in Python required. {n}")
        for n in range(6)
    ]
    lines = market._lines_about(padded, "python")
    phrases = market._phrasings(lines, "python")
    check("posting padding is not returned as register",
          any("proficiency" in p for p in phrases), False)

    # --- recurrence is counted across postings, not occurrences ---
    # One verbose posting saying a thing four times must not outvote three
    # separate companies saying a different thing once each.
    loud = posting("Data Analyst", " ".join(
        ["Hands-on Spark experience with Spark tuning."] * 4
    ))
    quiet = [
        posting("Data Analyst", "Build streaming jobs in Spark and Kafka."),
        posting("Data Analyst", "Spark and Kafka for event pipelines."),
        posting("Data Analyst", "Spark and Kafka on the ingestion path."),
    ]
    tools = market._tools(market._lines_about([loud] + quiet, "spark"), "spark")
    check("a tool from three postings is kept", "Kafka" in tools, True)

    # --- a cold or missing snapshot is not an error ---
    empty = market.vocabulary("", "python")
    check("no title yields no postings", empty["sampled"], 0)
    check("no title still returns the shape", sorted(empty.keys()),
          ["expectations", "phrasings", "sampled", "skill", "titles", "tools"])

    # --- skill detection ---
    known = ["Excel", "SQL"]
    check("a named skill is found",
          market.named_skill("make the python bullet stronger", known), "python")
    check("no skill named returns empty",
          market.named_skill("this reads junior", known), "")
    check("longest match wins over a substring of it",
          market.named_skill("my power bi dashboards", known), "power bi")
    check("a skill from the candidate's own evidence is found",
          market.named_skill("the excel work", known), "excel")
    # The same boundary rule as above, applied to detection rather than lines.
    check("'excel at' does not name the skill",
          market.named_skill("candidates who excel at this", []), "")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
