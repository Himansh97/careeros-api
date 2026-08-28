"""The coach may record what the candidate said, and nothing else.

Adding evidence from a conversation is the one change to this system that could
turn a model into an author of the candidate's career. The vault is what every
resume traces back to; a claim invented here would be laundered into a bullet
that passes containment, because containment compares a rewrite to its claim and
would be comparing it to the invention.

What actually prevents that is `_evidence_drafts` requiring each draft to point
at the span of conversation it came from, and then checking that span really is
in what the candidate typed. These tests are about that check: that it accepts a
real quote, refuses a plausible invention, and cannot be walked around with a
trivially short span or a quote lifted from the model's own earlier reply.

    ./.venv/bin/python tests/test_coach_evidence.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.resume_coach import MIN_QUOTE_CHARS, _evidence_drafts, _spoken  # noqa: E402

# The Excel exchange this feature was built for, in the candidate's own words.
SAID = "i have a excel certification i know excel stuff i have used it in omnicals"


def draft(**over):
    base = {
        "claim": "Ran regression analysis in Excel for pharmaceutical forecasting",
        "employer": "Omnicals Pharma",
        "skills": ["Excel"],
        "classification": "PRESENT_AND_EXPLICIT",
        "quote": "i have used it in omnicals",
    }
    base.update(over)
    return base


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # The thing the feature is for: they stated it, so it is recordable.
    kept, unsourced, _ = _evidence_drafts([draft()], SAID, [])
    check("a quoted draft is kept", len(kept), 1)
    check("a quoted draft is not counted unsourced", unsourced, 0)
    check("the employer survives", kept[0]["employer"], "Omnicals Pharma")

    # The failure mode this exists for: a useful-sounding invention. Freyr is a
    # real employer in the vault, which is exactly what makes it plausible.
    invented = draft(
        claim="Built Spark pipelines processing 10TB daily",
        employer="Freyr Solutions",
        quote="i built spark pipelines at freyr",
    )
    drafts, unsourced, _ = _evidence_drafts([invented], SAID, [])
    check("an invented claim is dropped", drafts, [])
    check("an invented claim is counted", unsourced, 1)

    # Unlike a rejected rewrite — worth showing, because seeing the fabrication
    # is the argument for the gate — an unsourced claim has nothing to compare
    # against, so it must not come back for display.
    mixed, *_ = _evidence_drafts([draft(quote="i led the migration"), draft()], SAID, [])
    check("only sourced drafts are returned",
          [d["quote"] for d in mixed], ["i have used it in omnicals"])

    # "i" appears in almost any sentence. A bare substring check would let a
    # model source any claim it liked from a single character.
    tiny, unsourced, _ = _evidence_drafts([draft(quote="i")], SAID, [])
    check("a one-character quote cannot pass", tiny, [])
    check("a one-character quote is counted", unsourced, 1)
    check("the minimum is more than one character", MIN_QUOTE_CHARS > 1, True)

    # Only the candidate's turns count. Quoting its own earlier message would
    # let the model manufacture its own source across two turns.
    history = [
        {"role": "user", "content": "make this sound stronger"},
        {"role": "assistant",
         "content": "You could mention the Spark work at Freyr Solutions."},
    ]
    selfquote, unsourced, _ = _evidence_drafts(
        [draft(quote="the spark work at freyr solutions")], "sure do that", history
    )
    check("the model cannot quote itself", selfquote, [])
    check("a self-quote is counted", unsourced, 1)

    # But an earlier candidate turn is still the candidate speaking.
    earlier, *_ = _evidence_drafts([draft()], "yes add that",
                                   [{"role": "user", "content": SAID}])
    check("an earlier candidate turn counts", len(earlier), 1)

    # Matching must not be defeated by the model tidying the punctuation.
    loose, *_ = _evidence_drafts([draft(quote="I  Have   Used It\nIn Omnicals")], SAID, [])
    check("matching ignores case and whitespace", len(loose), 1)

    # An unparseable classification must not become the strongest one by
    # accident. It lands unapproved either way, so the fallback is safe.
    odd, *_ = _evidence_drafts([draft(classification="DEFINITELY_TRUE")], SAID, [])
    check("unknown classification falls back",
          odd[0]["classification"], "PRESENT_AND_EXPLICIT")

    # A certification with no issuing body cannot be stored — the vault requires
    # an employer or project on every claim. It must be counted, not dropped
    # quietly, or the reply promises a card the screen never shows.
    noemployer, _, incomplete = _evidence_drafts([draft(employer="")], SAID, [])
    check("a draft with no employer is not a claim", noemployer, [])
    check("a draft with no employer is counted", incomplete, 1)

    # A malformed response is a bad turn, not a crash.
    for garbage in ("not a dict", 42, None, []):
        survived, *_ = _evidence_drafts([garbage, draft()], SAID, [])
        check(f"malformed entry {garbage!r} does not crash", len(survived), 1)

    spoken = _spoken("hello", [
        {"role": "assistant", "content": "SECRET_ASSISTANT_TEXT"},
        {"role": "user", "content": "USER_TEXT"},
    ])
    check("assistant text is not 'spoken'", "secret_assistant_text" in spoken, False)
    check("candidate text is 'spoken'", "user_text" in spoken, True)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
