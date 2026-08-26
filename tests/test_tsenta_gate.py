"""Nothing reaches Tsenta that should not have been sent.

`tsenta.submit` is the only function in this codebase that puts an application
in front of an employer, and there is no recall. Every refusal it can make must
therefore happen *before* the network is touched — a gate that fails after the
POST is not a gate.

These tests never let httpx run. The URL is deliberately unroutable and no key
is set, so any case that reaches the request would fail loudly rather than pass
quietly. What is being proved is that the refusals come first.

    ./.venv/bin/python tests/test_tsenta_gate.py
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.tsenta import submit  # noqa: E402


class FakeProfile:
    """Eligibility only reads work_authorization; this is the real situation."""

    work_authorization = "F-1 OPT, requires sponsorship"


def job(
    job_id: str,
    *,
    description: str = "Analytics role. SQL and dashboards.",
    location: str = "Austin, TX",
    url: str = "https://example.invalid/apply",
) -> dict:
    return {
        "id": job_id,
        "title": "Data Analyst",
        "company": {"name": "Example"},
        "location": location,
        "description": description,
        "applyUrl": url,
    }


def main() -> int:
    failures: list[str] = []
    profile = FakeProfile()
    # Absent on purpose: a refusal must not depend on the key being absent,
    # but a case that slips through the gate will stop here rather than post.
    os.environ.pop("TSENTA_API_KEY", None)

    def check(label: str, got, want):
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"PASS {label}")

    # A posting abroad. This is the Stripe Dublin / Exadel São Paulo case, and
    # the one that already cost three real applications.
    abroad = submit(
        job("j1", location="Dublin, Ireland"),
        profile,
        profile_id="p_1",
    )
    check("abroad refused", abroad.ok, False)
    check("abroad not sent", abroad.sent, False)
    check("abroad reason names eligibility", "eligibility says" in abroad.reason, True)

    # Explicitly refuses sponsorship. The Friedkin case.
    no_sponsor = submit(
        job("j2", description="We do not sponsor employment visas for this role."),
        profile,
        profile_id="p_1",
    )
    check("no-sponsorship refused", no_sponsor.ok, False)

    # Already sent by any means — including the outreach this pipeline sent by
    # hand, which Tsenta's own duplicate check cannot see.
    dupe = submit(job("j3"), profile, profile_id="p_1", already_submitted=True)
    check("duplicate refused", dupe.ok, False)
    check("duplicate reason names twice", "twice" in dupe.reason, True)

    # No URL to apply at.
    nowhere = submit(job("j4", url=""), profile, profile_id="p_1")
    check("missing url refused", nowhere.ok, False)

    # The candidate overriding their own gate by hand gets past eligibility and
    # is stopped only by the absent key — proving the override reaches the
    # network path rather than being silently ignored.
    forced = submit(
        job("j5", location="Dublin, Ireland"),
        profile,
        profile_id="p_1",
        force=True,
    )
    check("force clears eligibility", "eligibility says" in forced.reason, False)
    check("force still needs a key", forced.reason, "no TSENTA_API_KEY configured")

    # An eligible domestic role, with no key configured, must report the key —
    # never fall through to an attempt.
    fine = submit(job("j6"), profile, profile_id="p_1")
    check("eligible role reaches key check", fine.reason, "no TSENTA_API_KEY configured")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
