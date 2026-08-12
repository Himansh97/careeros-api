"""Outcome capture, and the refusal to compute rates without data.

`submitted_at` was a hardcoded `None` for the life of the applications table.
Nothing recorded when an application went out, so conversion and timing were
unanswerable in principle — which is why the interview-probability and
expected-value features could not honestly be built.

Two properties are load-bearing and tested here:

* **A stage timestamp is written once.** Re-entering a stage must keep the date
  it was first reached; the first response is what timing means.
* **No rates below a threshold.** One reply from four applications is 25% and
  means nothing. `funnel()` returns counts and says how many more are needed,
  rather than dividing small numbers and presenting the result as a finding.

    ./.venv/bin/python tests/test_outcomes.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> int:
    failures: list[str] = []
    tmp = pathlib.Path(tempfile.mkdtemp()) / "test.db"

    with patch("app.config.DB_PATH", tmp), patch("app.store.DB_PATH", tmp):
        from app import store
        from app.alerts import MIN_FOR_RATES

        def check(label: str, got, want):
            if got != want:
                failures.append(f"{label}: expected {want!r}, got {got!r}")
            else:
                print(f"PASS {label}")

        job = {
            "id": "gh_test_1",
            "title": "Data Analyst",
            "company": {"name": "Example"},
            "location": "Austin, TX",
            "source": "Greenhouse",
            "applyUrl": "https://example.com/job",
        }
        record = store.upsert_application(job, {"rawFitScore": 88})
        app_id = record["id"]

        # A fresh application has no submit date. Inventing one would
        # manufacture a funnel that never happened.
        apps = {a["id"]: a for a in store.list_applications()}
        check("new application has no submittedAt", apps[app_id]["submittedAt"], None)
        check("new application has no outcome", apps[app_id]["outcome"], None)

        store.advance(app_id, "submitted", "Application submitted by candidate")
        apps = {a["id"]: a for a in store.list_applications()}
        first_submit = apps[app_id]["submittedAt"]
        if not first_submit:
            failures.append("advancing to submitted did not stamp submittedAt")
        else:
            print("PASS advancing to submitted stamps submittedAt")

        # Re-entering the stage must not move the date.
        store.advance(app_id, "submitted", "Re-submitted after a fix")
        apps = {a["id"]: a for a in store.list_applications()}
        check("re-entering a stage keeps the first date",
              apps[app_id]["submittedAt"], first_submit)

        store.advance(app_id, "screening", "Recruiter reached out")
        apps = {a["id"]: a for a in store.list_applications()}
        first_response = apps[app_id]["firstResponseAt"]
        if not first_response:
            failures.append("a response status did not stamp firstResponseAt")
        else:
            print("PASS a response status stamps firstResponseAt")

        store.advance(app_id, "interview", "Interview scheduled")
        apps = {a["id"]: a for a in store.list_applications()}
        check("a later response keeps the first response date",
              apps[app_id]["firstResponseAt"], first_response)

        # A rejection is a response. Excluding it would flatter the rate.
        store.advance(app_id, "rejected", "Rejected after interview")
        apps = {a["id"]: a for a in store.list_applications()}
        check("terminal status records an outcome", apps[app_id]["outcome"], "rejected")
        check("outcome survives in the record", apps[app_id]["status"], "rejected")

        # Rates must stay unavailable on a handful of applications.
        from app.alerts import funnel

        f = funnel()
        check("rates unavailable on a tiny sample", f["ratesAvailable"], False)
        check("says how many more are needed",
              f["needForRates"], MIN_FOR_RATES - f["submitted"])
        if any(isinstance(v, float) for v in f.values()):
            failures.append("funnel() returned a computed rate before it had data")
        else:
            print("PASS funnel() returns counts, never a rate, below threshold")

        # An outcome is not a pipeline step: it arrives at any stage and must
        # survive a later status change.
        store.record_outcome(app_id, "rejected", "Position filled internally", "interview")
        apps = {a["id"]: a for a in store.list_applications()}
        check("outcome reason is stored verbatim",
              apps[app_id]["outcomeReason"], "Position filled internally")
        check("furthest stage reached is stored",
              apps[app_id]["outcomeStage"], "interview")
        outcome_at = apps[app_id]["outcomeAt"]

        store.advance(app_id, "screening", "Reopened by mistake")
        apps = {a["id"]: a for a in store.list_applications()}
        check("a later status change does not erase the outcome",
              apps[app_id]["outcome"], "rejected")
        check("the outcome timestamp is not moved",
              apps[app_id]["outcomeAt"], outcome_at)

        # A blank reason must stay blank rather than becoming an empty string
        # that later reads as "they told us nothing" versus "we never asked".
        store.record_outcome(app_id, "withdrawn", "", "")
        apps = {a["id"]: a for a in store.list_applications()}
        check("an unstated reason stays null", apps[app_id]["outcomeReason"], None)

        # An inferred timestamp must stay distinguishable from a measured one.
        with sqlite3.connect(tmp) as conn:
            conn.execute(
                "UPDATE applications SET timestamps_inferred=1 WHERE id=?", (app_id,)
            )
        apps = {a["id"]: a for a in store.list_applications()}
        check("reconstructed timestamps are flagged",
              apps[app_id]["timestampsInferred"], True)

    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
