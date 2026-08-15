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

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store.DB_PATH = pathlib.Path(tmp.name)
    # `connect` applies the schema itself, so opening it once is the setup.
    with store.connect():
        pass


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
