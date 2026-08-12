"""The vault write path must never corrupt the file or overstate a claim.

Everything a resume is allowed to assert traces back to `career_evidence.json`.
Until now nothing could write to it, so the failure modes had never been
exercised. Two matter more than the rest:

* **A truncated file loses every claim.** The write is atomic — temp file in
  the same directory, then `os.replace` — so an interruption leaves the
  original intact. Tested by making the serialisation fail mid-write.
* **A claim must not quietly become delivered work.** Moving
  `IN_PROGRESS_OR_DESIGNED` to `PRESENT_AND_EXPLICIT` changes a design into a
  shipped accomplishment. `career_evidence.json` keeps designed work precisely
  so it stays available for interview conversation without ever being written
  as delivered, and a field edit must not be able to promote it.

Runs entirely against a temp copy — the real vault is personal data and is
never touched.

    ./.venv/bin/python tests/test_evidence.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = {
    "claims": [
        {
            "claim_id": "seed-one",
            "employer_or_project": "Example Corp",
            "claim": "Built a reconciliation pipeline.",
            "skills": ["Python", "SQL"],
            "industry": "fintech",
            "date_range": "2024",
            "classification": "PRESENT_AND_EXPLICIT",
            "approved_for_resume": True,
            "evidence_source": "project notes",
        },
        {
            "claim_id": "seed-designed",
            "employer_or_project": "Example Corp",
            "claim": "Designed a target architecture, not yet delivered.",
            "skills": ["Architecture"],
            "industry": "fintech",
            "date_range": "2025",
            "classification": "IN_PROGRESS_OR_DESIGNED",
            "approved_for_resume": False,
            "evidence_source": "design doc",
        },
    ]
}


def main() -> int:
    failures: list[str] = []
    tmpdir = pathlib.Path(tempfile.mkdtemp())
    vault = tmpdir / "career_evidence.json"
    vault.write_text(json.dumps(SEED, indent=2))

    with patch("app.evidence.CAREEROS_DIR", tmpdir):
        from app import evidence
        from app.evidence import EvidenceError

        def check(label: str, got, want):
            if got != want:
                failures.append(f"{label}: expected {want!r}, got {got!r}")
            else:
                print(f"PASS {label}")

        def expect_error(label: str, fn, fragment: str):
            try:
                fn()
            except EvidenceError as exc:
                if fragment.lower() in str(exc).lower():
                    print(f"PASS {label}")
                else:
                    failures.append(f"{label}: wrong error — {exc}")
            else:
                failures.append(f"{label}: no error raised")

        # --- creation ---------------------------------------------------
        added = evidence.add_claim({
            "employer_or_project": "Example Corp",
            "claim": "Defined and tracked delivery KPIs for the servicing team.",
            "skills": ["KPI", "reporting"],
            "classification": "PRESENT_AND_EXPLICIT",
        })
        check("a new claim is unapproved until reviewed",
              added["approved_for_resume"], False)
        check("a new claim records its own source",
              bool(added["evidence_source"]), True)
        check("claim count grew", len(evidence.list_claims()), 3)

        expect_error("a claim needs text",
                     lambda: evidence.add_claim({"employer_or_project": "X",
                                                 "classification": "PRESENT_AND_EXPLICIT"}),
                     "needs a claim" if False else "text")
        expect_error("a claim needs a classification",
                     lambda: evidence.add_claim({"employer_or_project": "X",
                                                 "claim": "did a thing"}),
                     "classification")

        # Designed work must never be resume-eligible on creation, even when
        # the caller asks for it.
        designed = evidence.add_claim({
            "employer_or_project": "Example Corp",
            "claim": "Designed a migration that has not shipped.",
            "classification": "IN_PROGRESS_OR_DESIGNED",
            "approved_for_resume": True,
        })
        check("designed work cannot be approved on creation",
              designed["approved_for_resume"], False)

        # --- promotion guard --------------------------------------------
        expect_error(
            "designed cannot be promoted to delivered silently",
            lambda: evidence.update_claim("seed-designed",
                                          {"classification": "PRESENT_AND_EXPLICIT"}),
            "delivered",
        )
        promoted = evidence.update_claim(
            "seed-designed",
            {"classification": "PRESENT_AND_EXPLICIT", "confirmDelivered": True},
        )
        check("promotion works when confirmed explicitly",
              promoted["classification"], "PRESENT_AND_EXPLICIT")

        # --- retire, not delete -----------------------------------------
        retired = evidence.retire_claim("seed-one")
        check("retiring makes a claim resume-ineligible",
              retired["approved_for_resume"], False)
        check("retiring does not delete the record",
              any(c["claim_id"] == "seed-one" for c in evidence.list_claims()), True)

        # --- atomicity ---------------------------------------------------
        before = vault.read_text()
        count_before = len(evidence.list_claims())
        try:
            # json.dump fails part-way through; the original must survive.
            with patch("app.evidence.json.dump", side_effect=RuntimeError("disk full")):
                evidence.add_claim({
                    "employer_or_project": "Example Corp",
                    "claim": "This write is going to fail.",
                    "classification": "PRESENT_AND_EXPLICIT",
                })
        except RuntimeError:
            pass
        check("a failed write leaves the vault byte-identical",
              vault.read_text(), before)
        check("a failed write adds no claim",
              len(evidence.list_claims()), count_before)
        leftovers = list(tmpdir.glob(".evidence-*"))
        check("a failed write leaves no temp file behind", leftovers, [])

        # --- backups ------------------------------------------------------
        backups = list(tmpdir.glob("career_evidence.backup-*.json"))
        if not backups:
            failures.append("no backup was taken before writing")
        else:
            print(f"PASS a backup is taken before each write ({len(backups)} present)")
            restored = json.loads(backups[0].read_text())
            check("the backup is valid JSON with claims",
                  isinstance(restored.get("claims"), list), True)

    for f in failures:
        print(f"FAIL {f}")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
