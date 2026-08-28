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

from app.tsenta import canonical_role_key, submit  # noqa: E402

# Enough of a PDF to pass the shape check. The point of these tests is the
# refusals, and every one of them must fire before the bytes matter.
PDF = b"%PDF-1.4 tailored"


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
        resume_pdf=PDF,
        applied_role_keys=set(),
    )
    check("abroad refused", abroad.ok, False)
    check("abroad not sent", abroad.sent, False)
    check("abroad reason names eligibility", "eligibility says" in abroad.reason, True)

    # Explicitly refuses sponsorship. The Friedkin case.
    no_sponsor = submit(
        job("j2", description="We do not sponsor employment visas for this role."),
        profile,
        profile_id="p_1",
        resume_pdf=PDF,
        applied_role_keys=set(),
    )
    check("no-sponsorship refused", no_sponsor.ok, False)

    # Already sent by any means — including the outreach this pipeline sent by
    # hand, which Tsenta's own duplicate check cannot see.
    dupe = submit(job("j3"), profile, profile_id="p_1", resume_pdf=PDF,
                  applied_role_keys=set(), already_submitted=True)
    check("duplicate refused", dupe.ok, False)
    check("duplicate reason names twice", "twice" in dupe.reason, True)

    # No URL to apply at.
    nowhere = submit(job("j4", url=""), profile, profile_id="p_1", resume_pdf=PDF,
                     applied_role_keys=set())
    check("missing url refused", nowhere.ok, False)

    # The candidate overriding their own gate by hand gets past eligibility and
    # is stopped only by the absent key — proving the override reaches the
    # network path rather than being silently ignored.
    forced = submit(
        job("j5", location="Dublin, Ireland"),
        profile,
        profile_id="p_1",
        resume_pdf=PDF,
        applied_role_keys=set(),
        force=True,
    )
    check("force clears eligibility", "eligibility says" in forced.reason, False)
    check("force still needs a key", forced.reason, "no TSENTA_API_KEY configured")

    # An eligible domestic role, with no key configured, must report the key —
    # never fall through to an attempt.
    fine = submit(job("j6"), profile, profile_id="p_1", resume_pdf=PDF,
                  applied_role_keys=set())
    check("eligible role reaches key check", fine.reason, "no TSENTA_API_KEY configured")

    # Tsenta attaches its own stored resume when none is supplied, which is a
    # successful-looking application carrying a document nobody approved. This
    # must refuse instead, and refuse before the network like everything else.
    naked = submit(job("j7"), profile, profile_id="p_1", resume_pdf=b"",
                   applied_role_keys=set())
    check("no resume is a refusal", naked.ok, False)
    check("no resume says why", "not staged" in naked.reason, True)

    notpdf = submit(job("j8"), profile, profile_id="p_1", resume_pdf=b"<html>oops",
                    applied_role_keys=set())
    check("non-PDF bytes are refused", "not a PDF" in notpdf.reason, True)

    # By role, not by row. Fivetran posted one req twice under two Greenhouse
    # ids; the row-level guard sees two applications and waves the second in.
    same_req = dict(job("j9"), company={"name": "Fivetran"},
                    title="Senior Product Manager, Data & Integrations")
    other_id = submit(
        same_req, profile, profile_id="p_1", resume_pdf=PDF,
        applied_role_keys={canonical_role_key("Fivetran",
                                              "Product Manager, Data and Integrations")},
    )
    check("the same role under another id is refused", other_id.ok, False)
    check("and says so", "already applied" in other_id.reason, True)

    # Not knowing is not permission. A send cannot be undone, so a failed
    # lookup refuses rather than proceeding.
    def explode() -> set[str]:
        raise RuntimeError("database is gone")

    import app.tsenta as tsenta_mod
    original = tsenta_mod._applied_roles
    tsenta_mod._applied_roles = explode
    try:
        blind = submit(job("j10"), profile, profile_id="p_1", resume_pdf=PDF)
    finally:
        tsenta_mod._applied_roles = original
    check("a failed duplicate lookup refuses", blind.ok, False)
    check("and does not guess", "could not check" in blind.reason, True)

    check("distinct Target roles do not collapse",
          canonical_role_key("Target", "Sr Logistics Analyst - Last Mile Operations")
          == canonical_role_key("Target", "Sr Financial Analyst FP&A, Supply Chain"),
          False)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
