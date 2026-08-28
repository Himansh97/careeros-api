"""The submit route must hand Tsenta our document, not just our intent.

`app/tsenta.py` refuses to send without a resume, which protects the module but
not the caller: a route that renders nothing and passes nothing gets a clean
refusal and the candidate gets no application. The guarantee that matters
end-to-end is that the route *produces* the tailored PDF and that it *arrives*
at `submit()`.

That glue is exactly the kind of thing this repo has been bitten by before —
`AGENTS.md` records that the command palette, the tailor buttons and the empty
approvals page all "type-checked and linted perfectly while being completely
broken at runtime". Writing this test caught a real one: `add_timeline` was used
in the route and never imported, which would have raised `NameError` on the
first posting that asked for a cover letter.

No network. `submit` is replaced with a recorder, so nothing reaches Tsenta and
nothing reaches an employer.

    ./.venv/bin/python tests/test_submit_carries_our_work.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.main as main  # noqa: E402
import app.tsenta as tsenta  # noqa: E402
from app.tsenta import Submission  # noqa: E402


JOB = {
    "id": "test_job_1",
    "title": "Data Analyst",
    "company": {"name": "Example"},
    "location": "Austin, TX",
    "description": "Analytics role. SQL and dashboards. Please include a cover letter.",
    "applyUrl": "https://example.invalid/apply",
}

NO_LETTER_JOB = {**JOB, "id": "test_job_2", "description": "Analytics role. SQL."}


def main_() -> int:
    failures: list[str] = []
    seen: dict[str, object] = {}

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    def recorder(job, profile, **kwargs):
        seen.clear()
        seen.update(kwargs)
        return Submission(False, reason="recorded, not sent")

    async def fake_job_or_404(job_id: str):
        return JOB if job_id == "test_job_1" else NO_LETTER_JOB

    real_submit = tsenta.submit
    real_job = main._job_or_404
    real_compose = main.__dict__.get("compose_cover_letter")
    tsenta.submit = recorder
    main._job_or_404 = fake_job_or_404

    # Deterministic stand-in for the model. The generator itself is covered by
    # its own containment checks in compose.py; what is under test here is
    # whether the route asks for a letter and passes it on.
    import app.compose as compose
    real_letter = compose.compose_cover_letter
    compose.compose_cover_letter = lambda job, score, profile, **kw: "A written letter."

    try:
        asyncio.run(main.submit_application("test_job_1", {}))

        pdf = seen.get("resume_pdf")
        check("a resume reached submit", isinstance(pdf, bytes) and len(pdf) > 0, True)
        # The bytes must be a real rendered document, not a placeholder — this
        # is the whole point of the change.
        check("and it is a PDF", isinstance(pdf, bytes) and pdf.startswith(b"%PDF"), True)
        check("the posting asked, so a letter travelled too",
              seen.get("cover_letter"), "A written letter.")
        check("force is not set by the route by default", seen.get("force"), False)

        # A posting that does not ask for one must not pay for one.
        asked = {"n": 0}

        def counting(job, score, profile, **kw):
            asked["n"] += 1
            return "unwanted"

        compose.compose_cover_letter = counting
        asyncio.run(main.submit_application("test_job_2", {}))
        check("no letter is generated when none was asked for", asked["n"], 0)
        check("and none is sent", seen.get("cover_letter"), "")

        # Containment refusing to write a letter must not be silent: Tsenta
        # writes its own when none is supplied, and that document would go to an
        # employer under the candidate's name with nothing here having checked it.
        compose.compose_cover_letter = lambda job, score, profile, **kw: None
        recorded: list[tuple] = []
        real_timeline = main.add_timeline
        main.add_timeline = lambda app_id, kind, detail: recorded.append((kind, detail))
        try:
            tsenta.submit = lambda job, profile, **kw: Submission(
                True, status="queued", application_id="a1"
            )
            asyncio.run(main.submit_application("test_job_1", {}))
        finally:
            main.add_timeline = real_timeline
        check("a missing letter is recorded",
              any(k == "cover_letter_absent" for k, _ in recorded), True)
        check("and says Tsenta supplied its own",
              any("Tsenta supplied its own" in d for _, d in recorded), True)
    finally:
        tsenta.submit = real_submit
        main._job_or_404 = real_job
        compose.compose_cover_letter = real_letter
        if real_compose is not None:
            main.compose_cover_letter = real_compose

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
