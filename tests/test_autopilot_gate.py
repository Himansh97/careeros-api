"""Autopilot must not re-queue work the candidate has already dealt with.

`run_autopilot` ranked candidates on fit alone and queued the top N. Fit is not
the only thing that decides whether a job is worth putting in front of someone:
a role already applied to, a posting that has closed, and a role the candidate
is not eligible for all score exactly as well as they did the first time.

The queue this produced was 26 items of which 10 were jobs already applied to,
3 were postings the liveness check had already found closed, and 3 were
ineligible. An approval gate that is mostly noise is one you learn to skim,
which is worse than not having it.

    ./.venv/bin/python tests/test_autopilot_gate.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.automation import _SkipCounts, _skip_reason  # noqa: E402


class FakeProfile:
    """Minimal stand-in — eligibility only reads work_authorization here."""

    work_authorization = "F-1 OPT, requires sponsorship"


def job(job_id: str, *, description: str = "", location: str = "Austin, TX") -> dict:
    return {
        "id": job_id,
        "title": "Data Analyst",
        "company": {"name": "Example"},
        "location": location,
        "description": description or "Analytics role. SQL and dashboards.",
    }


def main() -> int:
    failures: list[str] = []
    profile = FakeProfile()

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # A job in neither set, eligible, is queued.
    counts = _SkipCounts()
    check(
        "clean job proceeds",
        _skip_reason(job("gh_new_1"), profile, set(), set(), counts),
        None,
    )
    check("clean job increments nothing", counts.total, 0)

    # Already applied — the case that put 10 duplicates in the real queue.
    counts = _SkipCounts()
    check(
        "already-applied job skipped",
        _skip_reason(job("gh_done_1"), profile, {"gh_done_1"}, set(), counts),
        "already applied",
    )
    check("counted as already_applied", counts.already_applied, 1)

    # Closed posting — liveness already wrote this onto the application.
    counts = _SkipCounts()
    check(
        "closed posting skipped",
        _skip_reason(job("gh_dead_1"), profile, set(), {"gh_dead_1"}, counts),
        "posting closed",
    )
    check("counted as posting_closed", counts.posting_closed, 1)

    # Ineligible — a foreign location the candidate cannot work from.
    counts = _SkipCounts()
    check(
        "ineligible job skipped",
        _skip_reason(
            job("gh_dublin_1", location="Dublin, Ireland"),
            profile,
            set(),
            set(),
            counts,
        ),
        "not eligible",
    )
    check("counted as not_eligible", counts.not_eligible, 1)

    # Applied wins over closed, so the message names the candidate's own action
    # rather than an incidental fact about the posting.
    counts = _SkipCounts()
    check(
        "already-applied takes precedence over closed",
        _skip_reason(job("gh_both_1"), profile, {"gh_both_1"}, {"gh_both_1"}, counts),
        "already applied",
    )

    # The run summary has to read as a sentence, and say nothing when nothing
    # was skipped — an empty run must not announce "0 already applied".
    check("empty describe is blank", _SkipCounts().describe(), "")
    check(
        "describe lists only non-zero reasons",
        _SkipCounts(already_applied=10, not_eligible=3).describe(),
        "10 already applied, 3 not eligible",
    )
    check(
        "describe keeps a stable order",
        _SkipCounts(already_applied=1, posting_closed=2, not_eligible=3).describe(),
        "1 already applied, 2 posting closed, 3 not eligible",
    )
    check("total sums every reason", _SkipCounts(1, 2, 3).total, 6)

    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
