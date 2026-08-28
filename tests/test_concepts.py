"""The deck comes from the evidence file, and a definition needs a source.

Two properties matter here and neither is about the UI.

The deck is derived, not stored. A written-down card list drifts the moment a
claim is added or retired, and every other part of this system treats
`career_evidence.json` as the only thing that knows what the candidate has done.
So these tests build a deck from claims directly and check the terms follow.

And a definition without a source is a guess wearing a citation's clothes. This
one would be recited in an interview, so `save_note` refuses it exactly as
`save_research` refuses unsourced question research.

The schedule is tested arithmetically rather than by waiting: `next_box` is a
pure function, which is most of why it was written as one.

    ./.venv/bin/python tests/test_concepts.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.concepts import (  # noqa: E402
    BOX_DAYS,
    MAX_BOX,
    Card,
    _terms_from_evidence,
    next_box,
    save_note,
)


class FakeClaim:
    def __init__(self, claim_id, employer, claim, skills, approved=True):
        self.claim_id = claim_id
        self.employer = employer
        self.claim = claim
        self.skills = skills
        self.approved_for_resume = approved


class FakeProfile:
    def __init__(self, claims):
        self.evidence = claims


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # --- the deck follows the evidence ---
    claims = [
        FakeClaim("c1", "Supreme Lending", "Built a merge engine over ULDD XML.",
                  ["ULDD", "XML", "Python"]),
        FakeClaim("c2", "Syracuse", "Reproduced SimCLR on a new dataset.",
                  ["SimCLR", "contrastive learning", "Python"]),
    ]
    terms = _terms_from_evidence(FakeProfile(claims))
    check("every declared term becomes a card", "ULDD" in terms, True)
    check("a term on two claims is one card", len(terms["Python"]), 2)
    check("and remembers both employers",
          sorted(c["employer"] for c in terms["Python"]), ["Supreme Lending", "Syracuse"])
    check("the claim travels with the term", terms["SimCLR"][0]["claim"],
          "Reproduced SimCLR on a new dataset.")

    # A retired claim is not on the resume, so nobody will be asked about its
    # terms — and drilling them would be studying for the wrong exam.
    retired = FakeClaim("c3", "Old", "Did something with Fortran.", ["Fortran"],
                        approved=False)
    terms = _terms_from_evidence(FakeProfile(claims + [retired]))
    check("a retired claim contributes no terms", "Fortran" in terms, False)

    # --- canonicalisation, via the table skills.py already maintains ---
    cased = [FakeClaim("c4", "X", "y", ["statistical modeling"]),
             FakeClaim("c5", "Y", "z", ["Statistical modeling"])]
    terms = _terms_from_evidence(FakeProfile(cased))
    check("case variants collapse to one card", len(terms), 1)
    check("under the canonical spelling", list(terms)[0], "Statistical modeling")

    # A term the alias table has never heard of must survive untouched rather
    # than be dropped — the tail is the whole point of this feature.
    odd = _terms_from_evidence(FakeProfile([FakeClaim("c6", "X", "y", ["ULDD"])]))
    check("an unknown term is kept verbatim", list(odd), ["ULDD"])

    # --- the schedule ---
    # An unseen card counts as box 1 — never learned — so a first clean recall
    # advances out of it. Sending it to box 1 instead would mean every one of
    # 158 cold terms comes back tomorrow however well you did, which is the
    # shape of deck people abandon on day three.
    check("a first good recall advances past the start", next_box(0, "good"), 2)
    check("good advances one box", next_box(2, "good"), 3)
    check("easy advances two", next_box(2, "easy"), 4)
    check("hard holds position", next_box(3, "hard"), 3)
    check("hard on an unseen card still starts it", next_box(0, "hard"), 1)
    # A term you could not explain is not one you half-know.
    check("again returns to the start from anywhere", next_box(5, "again"), 1)
    check("boxes are capped", next_box(5, "good"), MAX_BOX)
    check("easy cannot overshoot the cap", next_box(5, "easy"), MAX_BOX)

    try:
        next_box(1, "brilliant")
        failures.append("an unknown rating should raise")
    except ValueError:
        print("PASS an unknown rating is refused")

    # Four clean recalls from cold should reach the top box, or the ladder is
    # too long to ever finish.
    box = 0
    for _ in range(4):
        box = next_box(box, "good")
    check("four good recalls reach the top box", box, MAX_BOX)
    check("and the top box is two months out", BOX_DAYS[box - 1], 60)

    # --- an unsourced definition is refused ---
    # Same refusal save_research makes. These raise on validation, before the
    # function opens a connection, so nothing here writes to the database.
    for label, args in {
        "no sources": ("ULDD", "A loan delivery data format.", []),
        "blank sources": ("ULDD", "A loan delivery data format.", ["", "  "]),
        "no definition": ("ULDD", "", ["https://example.invalid"]),
        "no term": ("", "Something.", ["https://example.invalid"]),
    }.items():
        try:
            save_note(*args)
            failures.append(f"save_note with {label} should raise")
        except ValueError:
            print(f"PASS save_note refuses: {label}")

    # --- a card with no definition is still a card ---
    bare = Card(term="ULDD", claims=[{"claimId": "c1", "employer": "X", "claim": "y"}])
    check("an unseen card is due", bare.due, True)
    check("and reports it has no definition", bare.as_dict()["hasDefinition"], False)
    check("but still carries the claim", len(bare.as_dict()["claims"]), 1)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
