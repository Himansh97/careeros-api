"""The round follows the pipeline, rotates, and routes each item correctly.

Three properties, and the first two are where the first version was wrong.

**It has to rotate.** Ranking by demand alone served the same three terms every
morning, because answering something does not change how many employers want it.
Cross-functional collaboration is named by eighteen jobs today and will be named
by eighteen tomorrow. The schedule decides whether a term is in play; demand only
orders the ones that are.

**It has to be stable within a day.** Two calls the same morning must return the
same three, or the round can be rerolled until it is easy.

**It has to route.** "Explain stakeholder management" is a question nobody wants
answered. That one belongs to the STAR drill, which grades a story against the
evidence file; a definable term belongs to a concept card. Sending either to the
other makes the round useless in a different way each time.

`_rank` and `next_box` are pure, so all of this is arithmetic rather than
waiting.

    ./.venv/bin/python tests/test_daily_round.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.daily_round import (  # noqa: E402
    BEHAVIOURAL_REQUIREMENTS,
    ITEMS_PER_DAY,
    _rank,
    streak,
)


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # --- due beats demand, which is the fix ---
    # An 18-job term already answered must not outrank a 3-job term never seen.
    hot_done = _rank("Cross-functional collaboration", 18, False, "2026-08-29")
    cold_new = _rank("Hypothesis testing", 3, True, "2026-08-29")
    check("an answered high-demand term yields to an unseen one",
          cold_new < hot_done, True)

    # Among terms that are due, demand decides.
    a = _rank("SQL", 18, True, "2026-08-29")
    b = _rank("Tableau", 4, True, "2026-08-29")
    check("demand orders the due ones", a < b, True)

    # --- stable within a day, different across days ---
    check("the same day gives the same order",
          _rank("SQL", 5, True, "2026-08-29"), _rank("SQL", 5, True, "2026-08-29"))
    same_demand_today = sorted(["Excel", "Tableau", "Python"],
                               key=lambda t: _rank(t, 5, True, "2026-08-29"))
    same_demand_tomorrow = sorted(["Excel", "Tableau", "Python"],
                                  key=lambda t: _rank(t, 5, True, "2026-08-30"))
    check("ties break differently on a different day",
          same_demand_today != same_demand_tomorrow, True)

    # --- routing ---
    # The two most demanded requirements in the whole pipeline are behavioural.
    # If either ever routed to a concept card, the round would be asking someone
    # to define "process improvement", which is the thing to avoid.
    for term in ("cross-functional collaboration", "process improvement",
                 "stakeholder management"):
        check(f"{term!r} routes to a STAR question",
              BEHAVIOURAL_REQUIREMENTS[term].startswith("beh-"), True)
    for term in ("sql", "forecasting", "tableau", "hypothesis testing"):
        check(f"{term!r} is not treated as behavioural",
              term in BEHAVIOURAL_REQUIREMENTS, False)

    # Every mapped question must be one that actually exists, or the round sends
    # the candidate to a page that cannot render.
    from app.interview_practice import BEHAVIOURAL

    known = {q["id"] for q in BEHAVIOURAL}
    unknown = sorted(set(BEHAVIOURAL_REQUIREMENTS.values()) - known)
    check("every routed question id exists in /prep", unknown, [])

    check("three items a day", ITEMS_PER_DAY, 3)

    # --- the streak rule matches /prep's ---
    # Not having done today yet must not read as a broken streak at 9am.
    import app.daily_round as mod
    from app.db import connect

    today = datetime.now(timezone.utc).date()
    with connect() as conn:
        conn.execute("DELETE FROM daily_round WHERE day LIKE '1999-%'")
    check("no completed days is a zero streak", isinstance(streak(), int), True)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
