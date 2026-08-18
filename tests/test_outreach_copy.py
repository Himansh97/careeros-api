"""Outreach copy has to survive real job titles.

Postings carry titles like "Sr. Relationship Manager - Global Commercial
Banking - Healthcare, Education, Not-for-Profit Group - (Denver)" — 110
characters before the message says anything. The LinkedIn note was assembled at
full length and then sliced with `[:300]`, so on those titles it ended
mid-clause: "...and if someone else is closer to this". A note that stops
talking mid-sentence reads as broken software, which is the opposite of what a
first impression is for.

The fix builds variants and picks the first that fits, and the order it drops
things is a judgement worth pinning: the pointer request ("if someone else is
closer to this one, I'd be grateful for a pointer") is the highest-value
sentence in the note, because someone who cannot help often knows who can and
that is far easier to say yes to than a call. An earlier version preferred the
full job title and spent the pointer to keep it — trading the ask for a title
the recipient already knows.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.outreach import (  # noqa: E402
    LINKEDIN_NOTE_LIMIT,
    _linkedin_note,
    _readable_list,
    _short_title,
)

LONG = (
    "Sr. Relationship Manager - Global Commercial Banking - Healthcare, "
    "Education, Not-for-Profit Group -  (Denver)"
)
LONGER = (
    "Chef de Produit Senior, Matériel (Stations de vélos en libre-service)"
    "- Solution Urbaines de Lyft (Micromobilité)"
)


def _note(title: str, company: str = "Bank of America") -> str:
    return _linkedin_note("Ashley", title, company, "Python, SQL and Tableau")


def test_the_note_never_exceeds_linkedins_limit() -> None:
    for title in ("Data Analyst", LONG, LONGER, "X" * 400):
        note = _note(title)
        assert len(note) <= LINKEDIN_NOTE_LIMIT, (
            f"{len(note)} chars for a {len(title)}-char title"
        )


def test_the_note_never_stops_mid_sentence() -> None:
    """The failure that prompted this: a hard slice at 300."""
    for title in ("Data Analyst", LONG, LONGER, "X" * 400):
        note = _note(title).rstrip()
        assert note.endswith((".", "!", "?")), f"truncated mid-sentence: ...{note[-60:]!r}"


def test_a_long_title_gives_way_before_the_pointer_ask() -> None:
    """Title length is what should be spent, not the most useful sentence."""
    for title in (LONG, LONGER):
        note = _note(title)
        assert "pointer" in note, (
            f"the pointer request was dropped to preserve a {len(title)}-char title"
        )


def test_an_ordinary_title_keeps_everything() -> None:
    note = _note("Senior Data Analyst")
    assert "pointer" in note
    assert "Python, SQL and Tableau" in note
    assert "Senior Data Analyst" in note


def test_short_title_cuts_at_a_word_not_mid_word() -> None:
    out = _short_title(LONG, 46)
    assert len(out) <= 46
    assert not out.endswith(" ")
    # The cut lands on a separator, so the surviving text is a phrase.
    assert out == out.strip()
    assert LONG.startswith(out)


def test_short_title_leaves_a_short_title_alone() -> None:
    assert _short_title("Data Analyst", 46) == "Data Analyst"


def test_lists_read_as_prose() -> None:
    """"Python, Azure" mid-sentence reads as a field that leaked into a letter."""
    assert _readable_list(["Python", "Azure"]) == "Python and Azure"
    assert _readable_list(["A", "B", "C"]) == "A, B and C"
    assert _readable_list(["Solo"]) == "Solo"
    assert _readable_list([]) == ""


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
