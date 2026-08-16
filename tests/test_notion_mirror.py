"""The Notion mirror must stay a mirror.

The whole risk of this feature is that it stops being one way. A second system
that can write a status is how "applied" and "submitted" became two vocabularies
for one concept inside this repo — silently, for days, until six applications
the candidate had actually sent were rewound and offered back to them. Across a
network, with a UI on the other end that looks editable, that failure is easier
to cause and harder to see.

So these tests pin the contract rather than the payload shape:

* the module cannot read pipeline state, only its own row keys
* `Notes` is never written, so what the candidate types survives every sync
* a missing credential is a no-op, never an error
* nothing is truncated into a lie, and nothing missing is published as a zero
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import notion  # noqa: E402


def _record(**over):
    base = {
        "id": "app_gh_acme_1",
        "title": "Senior Data Analyst",
        "company": {"name": "Acme"},
        "status": "ready",
        "rawFitScore": 91,
        "resumeScore": 84,
        "applyUrl": "https://example.com/apply",
        "nextAction": "Review and approve",
        "submittedAt": None,
    }
    base.update(over)
    return base


def test_notes_is_never_written() -> None:
    """The candidate's column. Writing it would revert what they typed."""
    props = notion._properties(_record())
    assert notion.NOTES_PROPERTY not in props, (
        f"the mirror writes {notion.NOTES_PROPERTY!r}, so every sync would "
        "overwrite whatever the candidate wrote on their phone"
    )


def test_the_key_property_is_the_application_id() -> None:
    """Rows are matched on id, not on company and title.

    Those are not unique — there are four live Target applications and two at
    SoFi — so matching on them would either collapse distinct rows into one or
    duplicate a row on every run.
    """
    props = notion._properties(_record())
    key = props[notion.KEY_PROPERTY]["rich_text"][0]["text"]["content"]
    assert key == "app_gh_acme_1"


def test_a_missing_score_is_not_published_as_zero() -> None:
    """"Not scored yet" and "scored zero" are different claims."""
    props = notion._properties(_record(resumeScore=None))
    assert props["Resume"]["number"] is None, (
        "an unscored resume was published as a number, which reads as a real score"
    )


def test_a_real_score_survives() -> None:
    props = notion._properties(_record(resumeScore=84))
    assert props["Resume"]["number"] == 84


def test_long_text_is_clipped_below_the_api_limit() -> None:
    """Notion rejects an over-length value rather than trimming it, so an
    unclipped note would fail the whole page write."""
    props = notion._properties(_record(nextAction="x" * 5000))
    content = props["Next action"]["rich_text"][0]["text"]["content"]
    assert len(content) <= notion._RICH_TEXT_MAX


def test_empty_text_is_an_empty_property_not_a_blank_string() -> None:
    props = notion._properties(_record(nextAction=""))
    assert props["Next action"]["rich_text"] == []


def test_an_absent_url_clears_rather_than_sending_empty_string() -> None:
    props = notion._properties(_record(applyUrl=""))
    assert props["Apply URL"]["url"] is None


def test_no_credentials_is_a_no_op_not_a_failure() -> None:
    """CareerOS has to work identically with the mirror switched off."""
    report = asyncio.run(notion.sync([_record()]))
    if report.ok:
        # Credentials are configured in this environment; the contract under
        # test is the absent-credential path, so there is nothing to assert.
        return
    assert "NOTION_" in report.reason
    assert report.created == 0 and report.updated == 0


def test_the_client_has_no_way_to_read_pipeline_state() -> None:
    """A read path is how a mirror becomes a second source of truth.

    `existing_rows` is the one permitted read and it extracts nothing but the
    key it needs to update rather than duplicate a row. Any other method that
    returns Notion content back into the app is the thing this forbids.
    """
    methods = {
        name
        for name, _ in inspect.getmembers(notion._Client, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"existing_rows", "create", "update"}, (
        f"the Notion client grew a new public method: {methods}. If it reads "
        "pipeline state, the mirror is no longer one way."
    )


def test_the_module_never_writes_to_the_store() -> None:
    """Nothing here may reach back into the database."""
    src = inspect.getsource(notion)
    for forbidden in ("from .store", "import store", "set_resume_score", "advance("):
        assert forbidden not in src, (
            f"app/notion.py references {forbidden!r} — the mirror must not write "
            "pipeline state"
        )


def test_the_api_version_is_pinned() -> None:
    """Notion shipped five breaking versions in H1 2026. Unpinned breaks quietly."""
    from app.config import NOTION_VERSION

    assert NOTION_VERSION, "the Notion API version must be pinned"
    assert NOTION_VERSION >= "2025-09-03", (
        "versions before 2025-09-03 predate the data_sources split and cannot "
        "address a multi-source database"
    )


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
