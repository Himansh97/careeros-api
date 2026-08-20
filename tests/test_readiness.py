"""One score, one meaning of "ready".

Two places decided whether an application was ready to send, and they disagreed.
`tailor_resume` gated the word on the resume score; `store.set_resume_score`
wrote status="ready" for every un-sent application regardless of the number it
had just been handed.

Nothing caught it because the audit was mostly constants and every score landed
in the 80s and 90s, so the two rules agreed by accident on every row that
existed. The moment the audit started discriminating, eight applications scoring
50-79 appeared in the list labelled "Ready" with "Review and approve" beside
them — the score saying the document is not worth sending and the status saying
it is. A score whose one job is to gate sending must actually gate it.

The committed-status guard is tested alongside it because the two rules meet in
the same function and the wrong fix to either breaks the other: a low score must
not rewind an application the candidate has already sent.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import READY_SCORE  # noqa: E402


def _fresh_db() -> None:
    """Point the store at an empty database for the duration of a test.

    The real one holds the candidate's applications; a test that writes to it
    would be editing their pipeline.
    """
    import app.store as store
    from app.db import initialize

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store.DB_PATH = pathlib.Path(tmp.name)
    initialize(path=store.DB_PATH)


def _seed(app_id: str, status: str) -> None:
    import app.store as store

    with store.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO applications "
            "(id, job_id, title, company, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (app_id, "job_1", "Analyst", "Acme", status, store.now(), store.now()),
        )


def _row(app_id: str) -> sqlite3.Row:
    import app.store as store

    with store.connect() as conn:
        return conn.execute(
            "SELECT status, next_action, resume_score FROM applications WHERE id=?",
            (app_id,),
        ).fetchone()


def test_a_score_below_the_bar_does_not_read_as_ready() -> None:
    _fresh_db()
    from app.store import set_resume_score

    _seed("a1", "draft")
    set_resume_score("a1", READY_SCORE - 1)
    row = _row("a1")
    assert row["status"] == "draft", (
        f"a resume scoring {READY_SCORE - 1} against a bar of {READY_SCORE} "
        f"was marked {row['status']!r}"
    )


def test_a_clearly_weak_resume_does_not_read_as_ready() -> None:
    _fresh_db()
    from app.store import set_resume_score

    _seed("a2", "draft")
    set_resume_score("a2", 50)
    assert _row("a2")["status"] == "draft"


def test_a_score_at_the_bar_is_ready() -> None:
    _fresh_db()
    from app.store import set_resume_score

    _seed("a3", "draft")
    set_resume_score("a3", READY_SCORE)
    row = _row("a3")
    assert row["status"] == "ready"
    assert row["next_action"] == "Review and approve"


def test_the_next_action_says_how_far_short_it_is() -> None:
    """"Review and approve" on a 50 is the wrong instruction, not just the wrong
    label — it tells the candidate to send something the audit says not to."""
    _fresh_db()
    from app.store import set_resume_score

    _seed("a4", "draft")
    set_resume_score("a4", READY_SCORE - 7)
    action = _row("a4")["next_action"]
    assert "7" in action, f"next action does not name the shortfall: {action!r}"
    assert "approve" not in action.lower(), f"still tells them to approve: {action!r}"


def test_a_sent_application_is_not_rewound_by_a_low_score() -> None:
    """A score refresh is not evidence that an application was un-sent."""
    _fresh_db()
    from app.store import set_resume_score

    _seed("a5", "applied")
    set_resume_score("a5", 40)
    row = _row("a5")
    assert row["status"] == "applied", f"a sent application became {row['status']!r}"
    assert row["resume_score"] == 40, "the score itself should still be recorded"


def test_every_status_the_ui_can_reach_past_sending_is_protected() -> None:
    """The guard has to know the words the rest of the system actually writes.

    It listed "applied" and "interviewing" — two spellings the frontend never
    produces. The pipeline the UI drives is qualified -> tailoring -> ready ->
    applying -> submitted -> recruiter_contacted -> screening -> interview ->
    offer, so the guard missed "submitted" entirely: the exact status the
    "Mark applied" button writes.

    The damage was not theoretical. Six applications the candidate had sent
    were rewound to ready by the next autopilot re-tailor and reappeared in the
    apply queue asking to be applied to a second time.
    """
    from app.store import set_resume_score

    for status in (
        "submitted",
        "recruiter_contacted",
        "screening",
        "interview",
        "offer",
        "rejected",
    ):
        _fresh_db()
        _seed("s1", status)
        set_resume_score("s1", 95)  # a high score, so "ready" is the tempting write
        got = _row("s1")["status"]
        assert got == status, (
            f"a {status!r} application was rewound to {got!r} by a re-tailor — "
            "the candidate cannot reconstruct that it was sent"
        )


def _timeline(app_id: str) -> list[str]:
    import app.store as store

    with store.connect() as conn:
        return [
            r["label"]
            for r in conn.execute(
                "SELECT label FROM timeline WHERE application_id=? ORDER BY rowid",
                (app_id,),
            )
        ]


def test_a_rescore_that_changes_nothing_writes_no_history() -> None:
    """Autopilot re-tailors on every pass, at the same score nearly every time.

    Logging each one made 85% of the timeline "Resume tailored", and 1158 of
    those 1330 entries repeated the score immediately above them. A history
    that records non-events buries the events.
    """
    _fresh_db()
    from app.store import set_resume_score

    _seed("t1", "draft")
    set_resume_score("t1", 84)
    set_resume_score("t1", 84)
    set_resume_score("t1", 84)
    entries = _timeline("t1")
    assert len(entries) == 1, f"three identical rescores wrote {len(entries)} entries"


def test_the_first_score_is_always_recorded() -> None:
    _fresh_db()
    from app.store import set_resume_score

    _seed("t2", "draft")
    set_resume_score("t2", 84)
    assert len(_timeline("t2")) == 1


def test_a_changed_score_records_the_move() -> None:
    """"Resume tailored — score 83" twice does not say the score held. Naming
    both numbers is what makes the entry worth reading."""
    _fresh_db()
    from app.store import set_resume_score

    _seed("t3", "draft")
    set_resume_score("t3", 71)
    set_resume_score("t3", 84)
    entries = _timeline("t3")
    assert len(entries) == 2, f"a real change was not recorded: {entries}"
    assert "71" in entries[1] and "84" in entries[1], (
        f"the entry does not say what moved: {entries[1]!r}"
    )


def test_a_rescore_of_a_sent_application_still_records_a_real_change() -> None:
    """The committed guard protects `status`, not the score history."""
    _fresh_db()
    from app.store import set_resume_score

    _seed("t4", "submitted")
    set_resume_score("t4", 71)
    set_resume_score("t4", 90)
    assert len(_timeline("t4")) == 2
    assert _row("t4")["status"] == "submitted"


def test_applying_is_not_treated_as_sent() -> None:
    """"Applying" means in progress, not done. It must stay re-tailorable."""
    _fresh_db()
    from app.store import set_resume_score

    _seed("s2", "applying")
    set_resume_score("s2", 95)
    assert _row("s2")["status"] == "ready"


def test_tailor_and_the_store_agree_on_the_word_ready() -> None:
    """The two rules must read the same constant, not two copies of a number.

    This is the actual defect: both said 80, in different files, and only one of
    them was applied.
    """
    import inspect

    from app import store, tailor

    for mod in (store, tailor):
        src = inspect.getsource(mod)
        assert "READY_SCORE" in src, f"{mod.__name__} does not use the shared bar"


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
