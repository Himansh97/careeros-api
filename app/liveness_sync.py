"""Write liveness verdicts to applications — both directions.

The closure flag used to be write-only. `check_liveness.py` and the daily fetch
both recorded "Posting closed — no longer accepting" when a posting went
missing, and neither ever cleared it when the posting came back. Because a
posting is judged missing by its absence from the fetched pool, any single bad
fetch — a cold cache, a source hiccup, a slow board that timed out — marked
every application at that company permanently dead.

It was not hypothetical. An audit found **16 of 22** API-sourced applications
carrying a closed flag while their postings were sitting in the live pool:
Target, Truist, Lyft, Datadog, OpenAI among them. Those applications were being
reported as dead work, and the commit criteria were holding them at NO-GO on
the strength of it.

So the verdict is applied symmetrically. `closed` writes the flag, and `live`
clears it if one is set. Clearing is deliberately narrow: it only removes notes
this system wrote about closure, so a "Security code required" note the reply
checker left, or anything the candidate typed, survives untouched.
"""
from __future__ import annotations

from typing import Any

from .store import add_timeline, connect, now

CLOSED_NOTE = "Posting closed — no longer accepting"

# Only notes matching these are ours to clear. Anything else on `next_action`
# was put there by a different check and is not this module's to overwrite.
_OURS = ("posting closed", "no longer accepting")


def is_closure_note(note: str | None) -> bool:
    """Does this `next_action` mean the posting is gone?

    Public because three places need the answer and two of them were deciding
    it for themselves. `alerts.blocked_applications` re-derived it with its own
    string test, and `alerts.aging_applications` carried a comment saying closed
    postings were skipped while doing no such thing — so a closed posting still
    produced "ready for 10 days, go submit it". One definition, imported.
    """
    low = (note or "").lower()
    return any(marker in low for marker in _OURS)


# Kept so nothing importing the private name breaks.
_is_closure_note = is_closure_note


def apply_verdicts(checks: list[dict[str, Any]], apps: list[dict[str, Any]]) -> dict[str, int]:
    """Record closures and, just as importantly, retractions.

    Returns counts so callers can log what actually changed rather than what
    was merely looked at.
    """
    note_by_job = {
        a.get("jobId"): (a.get("nextAction") or "") for a in apps
    }
    marked = 0
    cleared = 0

    with connect() as conn:
        for c in checks:
            job_id = c.get("jobId")
            app_id = f"app_{job_id}"
            existing = note_by_job.get(job_id, "")

            if c.get("verdict") == "closed":
                if _is_closure_note(existing):
                    continue  # already recorded; do not spam the timeline
                conn.execute(
                    "UPDATE applications SET next_action=?, updated_at=? WHERE id=?",
                    (CLOSED_NOTE, now(), app_id),
                )
                add_timeline(conn, app_id, f"Posting no longer live: {c.get('why')}")
                marked += 1

            elif c.get("verdict") == "live" and _is_closure_note(existing):
                # The posting is demonstrably back. Retract, and say so on the
                # timeline — a flag that appears and vanishes with no record is
                # worse than one that is merely wrong.
                conn.execute(
                    "UPDATE applications SET next_action=?, updated_at=? WHERE id=?",
                    ("", now(), app_id),
                )
                add_timeline(
                    conn,
                    app_id,
                    "Posting is live again — clearing the earlier closed flag, "
                    "which was recorded while it was missing from the fetched pool.",
                )
                cleared += 1

    return {"marked": marked, "cleared": cleared}
