"""A reply that could not be drafted must not be able to hide.

`unsent_recruiter_replies` short-circuits on `sentAt`, which is right for the
case it was written for and wrong for the one that actually occurred. A draft
row is reused across cycles, so a thread replied to in August carries a
`sentAt` forever — and when a later approval failed to produce a draft, the
stale `sentAt` from the *previous* cycle swallowed the alert. One record held
two conflicting truths at once: sent, and failed.

That is the same shape as every other bug this pipeline has had. The state that
says "handled" wins over the state that says "broken", and the failure is
reported as success.

Two properties are pinned here:

* a failed draft raises an alert whatever `sentAt` says
* an alert about a failed draft does not tell the candidate to go and send a
  draft that was never created
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import alerts  # noqa: E402


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _message(**draft_over):
    draft = {
        "status": "awaiting_approval",
        "approvedAt": None,
        "sentAt": None,
        "updatedAt": _ago(1),
        "gmailDraftId": None,
        "lastErrorCode": None,
        "lastErrorMessage": None,
    }
    draft.update(draft_over)
    return {
        "gmailMessageId": "m1",
        "senderName": "Matthew Macfarlane",
        "draft": draft,
    }


def _run(messages, fn):
    """Run one alert function against a fixed message list."""
    import app.recruiter_messages as rm

    original = rm.list_messages
    rm.list_messages = lambda: messages  # type: ignore[assignment]
    try:
        return fn()
    finally:
        rm.list_messages = original  # type: ignore[assignment]


def test_a_failed_draft_alerts_even_though_the_thread_was_replied_to_before() -> None:
    """The real record: sent in August, re-approved later, draft creation failed.

    The stale `sentAt` from the earlier cycle made this invisible for five days.
    """
    msg = _message(
        status="failed",
        approvedAt=_ago(120),
        sentAt=_ago(200),  # an earlier cycle genuinely was sent
        lastErrorCode="gmail_draft_error",
        lastErrorMessage="Draft creation failed. Please retry after reviewing the draft.",
    )
    got = _run([msg], alerts.failed_reply_drafts)
    assert got, "a failed draft raised no alert because the thread had a stale sentAt"
    assert got[0].severity == "high"


def test_a_failed_draft_alerts_when_nothing_was_ever_sent() -> None:
    msg = _message(status="failed", approvedAt=_ago(30), lastErrorCode="gmail_draft_error")
    assert _run([msg], alerts.failed_reply_drafts)


def test_the_failure_alert_does_not_say_go_and_send_the_draft() -> None:
    """There is no draft. Telling someone to open one wastes the trip and
    teaches them the alerts are wrong."""
    msg = _message(status="failed", approvedAt=_ago(30), lastErrorCode="gmail_draft_error")
    alert = _run([msg], alerts.failed_reply_drafts)[0]
    text = f"{alert.title} {alert.detail} {alert.action}".lower()
    assert "never sent" not in text, f"describes it as unsent rather than undrafted: {text}"
    assert "open the draft" not in text, f"sends them to a draft that does not exist: {text}"


def test_a_healthy_draft_raises_nothing() -> None:
    assert not _run([_message(status="created", approvedAt=_ago(30))], alerts.failed_reply_drafts)
    assert not _run([_message()], alerts.failed_reply_drafts)


def test_a_failed_draft_is_not_also_reported_as_merely_unsent() -> None:
    """One problem, one alert. Two alerts for one record is how a list becomes
    noise that gets scrolled past."""
    msg = _message(status="failed", approvedAt=_ago(30), lastErrorCode="gmail_draft_error")
    assert not _run([msg], alerts.unsent_recruiter_replies)


def test_a_re_approved_draft_is_unsent_despite_an_older_send() -> None:
    """`sentAt` from a previous cycle does not mean the current approval went
    out. Presence of a timestamp is not the question — which came last is."""
    msg = _message(status="created", approvedAt=_ago(20), sentAt=_ago(200))
    assert _run([msg], alerts.unsent_recruiter_replies), (
        "an approval made after the last send was treated as already sent"
    )


def test_a_draft_sent_after_its_approval_is_settled() -> None:
    msg = _message(status="created", approvedAt=_ago(200), sentAt=_ago(20))
    assert not _run([msg], alerts.unsent_recruiter_replies)


def test_a_created_draft_can_still_be_dismissed() -> None:
    """Deciding not to send has to stay available after the draft exists.

    Dismissal was gated on the edit statuses, so a `created` draft had no exit:
    it could not be dropped, and the "approved but never sent" alert repeated
    indefinitely unless it was sent. Two real drafts were never sendable at
    all — one to a `donotreply@` mailbox, one to a colleague covering a
    parental leave that had ended — and the app offered no way to close them.
    """
    from app.recruiter_messages import _DISMISSABLE_STATUSES, _EDITABLE_STATUSES

    assert "created" in _DISMISSABLE_STATUSES
    # Editing stays narrower: rewriting text Gmail already holds would let the
    # app's copy and the mailbox diverge.
    assert "created" not in _EDITABLE_STATUSES
    assert _EDITABLE_STATUSES <= _DISMISSABLE_STATUSES


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
            except AttributeError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
