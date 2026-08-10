#!/usr/bin/env python3
"""Apply hand-tailored bullet overrides for a job, from a data file.

Why this reads a file instead of holding the text inline: an override IS resume
content, derived from `career_evidence.json`. That file is deliberately
gitignored — accomplishment claims are treated as personal data — and earlier
per-job scripts embedded the same sentences directly in Python, which routed
straight around that decision and put them in git.

So the wording lives beside the evidence it came from, in
`~/careeros/overrides/<job_id>.json`, gitignored with everything else. This
script is generic and safe to commit.

    python scripts/apply_overrides.py <job_id> [--dry-run]
    python scripts/apply_overrides.py --all

File format:

    {
      "jobId": "gh_sofi_7816100003",
      "headline": "Business Analyst — Mortgage Data & Reporting",   // optional
      "summary": "...",                                            // optional
      "bullets": [
        {"claimId": "supreme-lending-01", "text": "...", "rationale": "..."}
      ]
    }

Every rewrite still passes `overrides.verify_override`, so a bullet cannot
introduce a figure, a tool or a scope its source claim does not contain.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.overrides import (  # noqa: E402
    EDITABLE_FIELDS,
    clear_overrides,
    save_document_edit,
    save_override,
)
from app.profile import load_profile  # noqa: E402

OVERRIDES_DIR = Path.home() / "careeros" / "overrides"


def apply_file(path: Path, dry_run: bool = False) -> int:
    spec = json.loads(path.read_text())
    job_id = spec.get("jobId") or path.stem
    claims = {c.claim_id: c.claim for c in load_profile().evidence}

    print(f"\n{job_id}  ({path.name})")
    failed = 0

    if not dry_run:
        # Replace this job's system layer wholesale. Any edit the candidate made
        # themselves lives in the 'user' layer and is untouched by this.
        clear_overrides(job_id)

    for bullet in spec.get("bullets", []):
        claim_id = bullet.get("claimId")
        original = claims.get(claim_id)
        if original is None:
            print(f"  SKIP {claim_id}: no such claim in career_evidence.json")
            failed += 1
            continue
        if dry_run:
            from app.overrides import verify_override

            problems = verify_override(original, bullet["text"])
            print(f"  {'WOULD FAIL' if problems else 'ok        '} {claim_id}"
                  + (f" — {'; '.join(problems)}" if problems else ""))
            failed += bool(problems)
            continue

        result = save_override(
            job_id, claim_id, bullet["text"], original,
            rationale=bullet.get("rationale", ""),
        )
        if result["ok"]:
            print(f"  OK   {claim_id}")
        else:
            failed += 1
            print(f"  FAIL {claim_id}: {'; '.join(result['problems'])}")

    for field in EDITABLE_FIELDS:
        if spec.get(field):
            if dry_run:
                print(f"  would set {field}")
            else:
                save_document_edit(job_id, field, spec[field])
                print(f"  SET  {field}")

    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?", help="job id, or omit with --all")
    ap.add_argument("--all", action="store_true", help="apply every override file")
    ap.add_argument("--dry-run", action="store_true", help="verify without writing")
    args = ap.parse_args()

    if not OVERRIDES_DIR.exists():
        print(f"No override directory at {OVERRIDES_DIR}", file=sys.stderr)
        return 1

    if args.all:
        paths = sorted(OVERRIDES_DIR.glob("*.json"))
    elif args.job_id:
        paths = [OVERRIDES_DIR / f"{args.job_id}.json"]
    else:
        ap.error("give a job id or --all")

    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(f"No override file: {p}", file=sys.stderr)
    paths = [p for p in paths if p.exists()]
    if not paths:
        return 1

    failed = sum(apply_file(p, args.dry_run) for p in paths)
    print(f"\n{len(paths)} file(s), {failed} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
