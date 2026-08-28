"""Writing to the career evidence vault.

`profile.py` only ever read. Adding a claim meant an agent session editing a
gitignored JSON file by hand, which makes the one asset that compounds — the
record of what the candidate has actually done — the hardest thing in the
system to add to.

Everything a resume may assert traces back to this file, so the write path is
deliberately careful:

* **Atomic.** Written to a temp file in the same directory and moved into
  place, so an interrupted write cannot leave a truncated vault. Losing this
  file loses every claim the resumes are built from.
* **Backed up first.** A timestamped copy before each write. `careeros`
  gitignores `career_evidence*` by stem, so backups are covered by the same
  rule — that rule exists because a `.backup-*.json` full of legal name, phone
  and visa status once sat untracked, one `git add -A` from being published.
* **Cache-invalidating.** `profile.load_profile` is `lru_cache`d and
  `scoring.score_job_cached` keys on the returned instance, so a write clears
  both and every stale score is dropped.
* **Never silently promoting.** A claim cannot move from designed to shipped
  through an ordinary update. `IN_PROGRESS_OR_DESIGNED` work is kept so it is
  available in conversation, and writing it as delivered is the exact
  fabrication this system exists to prevent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CAREEROS_DIR
from .profile import load_profile

EVIDENCE_FILE = "career_evidence.json"

CLASSIFICATIONS = (
    "PRESENT_AND_EXPLICIT",     # done, and stated plainly in a source
    "LEARNED_OR_ACADEMIC",      # studied or coursework, not shipped at work
    "IN_PROGRESS_OR_DESIGNED",  # designed or underway, NOT delivered
)

# Only this direction is a promotion. Designed work becoming "explicit" is the
# claim changing from something planned into something delivered, which needs
# the candidate to say so deliberately rather than arriving via a field edit.
_PROMOTIONS = {
    ("IN_PROGRESS_OR_DESIGNED", "PRESENT_AND_EXPLICIT"),
    ("LEARNED_OR_ACADEMIC", "PRESENT_AND_EXPLICIT"),
}

_SLUG = re.compile(r"[^a-z0-9]+")


class EvidenceError(ValueError):
    """A write that would corrupt the vault or overstate a claim."""


def _path() -> Path:
    return CAREEROS_DIR / EVIDENCE_FILE


def _read() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        raise EvidenceError(f"{path} not found")
    data = json.loads(path.read_text())
    if "claims" not in data or not isinstance(data["claims"], list):
        raise EvidenceError("evidence file has no claims list")
    return data


def _write(data: dict[str, Any]) -> None:
    """Back up, write to a temp file, then move it into place."""
    path = _path()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, path.with_name(f"career_evidence.backup-{stamp}.json"))

    # Same directory, so the replace is atomic rather than a cross-device copy.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".evidence-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    # The loaded profile is now stale, and every memoised score with it.
    load_profile.cache_clear()


def _claim_id(employer: str, claim: str, taken: set[str]) -> str:
    stem = _SLUG.sub("-", f"{employer} {claim[:40]}".lower()).strip("-")[:54]
    candidate = stem or "claim"
    n = 2
    while candidate in taken:
        candidate = f"{stem}-{n}"
        n += 1
    return candidate


def list_claims() -> list[dict[str, Any]]:
    return _read()["claims"]


def add_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """Add a claim. Unapproved by default — nothing reaches a resume unreviewed."""
    claim = (payload.get("claim") or "").strip()
    employer = (payload.get("employer_or_project") or "").strip()
    classification = (payload.get("classification") or "").strip().upper()

    if not claim:
        raise EvidenceError("a claim needs text")
    if not employer:
        raise EvidenceError("a claim needs an employer or project")
    if classification not in CLASSIFICATIONS:
        raise EvidenceError(
            "classification must be one of " + ", ".join(CLASSIFICATIONS)
        )

    data = _read()
    taken = {c.get("claim_id") for c in data["claims"]}
    record = {
        "claim_id": _claim_id(employer, claim, taken),
        "employer_or_project": employer,
        "claim": claim,
        "skills": [s.strip() for s in (payload.get("skills") or []) if s.strip()],
        "industry": (payload.get("industry") or "").strip(),
        "date_range": (payload.get("date_range") or "").strip(),
        "classification": classification,
        # Designed work is never resume-eligible on creation, whatever was
        # asked for. Everything else still waits for an explicit approval.
        "approved_for_resume": bool(payload.get("approved_for_resume"))
        and classification != "IN_PROGRESS_OR_DESIGNED",
        "evidence_source": (payload.get("evidence_source") or "").strip()
        or "Added by candidate in CareerOS",
        "project": (payload.get("project") or "").strip(),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    data["claims"].append(record)
    _write(data)
    return record


def add_unapproved_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """Add a claim that cannot be resume-eligible, whatever the caller asked.

    `add_claim` honours `approved_for_resume`, which is right for a person
    typing into the Evidence Vault — they are approving their own history. It is
    wrong for anything a model had a hand in drafting, and "the route remembers
    to pass False" is a guarantee that lasts exactly until someone adds a second
    route. Written here so the property belongs to the vault rather than to a
    literal in a caller.

    Recording a fact and deciding a resume may assert it stay two decisions.
    """
    return add_claim({**payload, "approved_for_resume": False})


def update_claim(claim_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Edit a claim, refusing a silent promotion to delivered work."""
    data = _read()
    for record in data["claims"]:
        if record.get("claim_id") == claim_id:
            break
    else:
        raise EvidenceError(f"no claim {claim_id!r}")

    new_class = (patch.get("classification") or record["classification"]).upper()
    if new_class not in CLASSIFICATIONS:
        raise EvidenceError("classification must be one of " + ", ".join(CLASSIFICATIONS))
    if (record["classification"], new_class) in _PROMOTIONS and not patch.get("confirmDelivered"):
        raise EvidenceError(
            f"moving a claim from {record['classification']} to {new_class} says the "
            "work was delivered. Confirm that explicitly — it changes what a "
            "resume is allowed to assert."
        )

    for field in ("claim", "employer_or_project", "industry", "date_range",
                  "evidence_source", "project"):
        if field in patch and patch[field] is not None:
            record[field] = str(patch[field]).strip()
    if "skills" in patch and patch["skills"] is not None:
        record["skills"] = [s.strip() for s in patch["skills"] if s.strip()]

    record["classification"] = new_class
    if "approved_for_resume" in patch:
        record["approved_for_resume"] = (
            bool(patch["approved_for_resume"]) and new_class != "IN_PROGRESS_OR_DESIGNED"
        )
    record["updated_at"] = datetime.now(timezone.utc).isoformat()

    _write(data)
    return record


def retire_claim(claim_id: str) -> dict[str, Any]:
    """Withdraw a claim from resumes without deleting it.

    Deleting would lose the record of something the candidate did. Retiring
    only makes it resume-ineligible, so it stays available for interview
    conversation and can be restored.
    """
    data = _read()
    for record in data["claims"]:
        if record.get("claim_id") == claim_id:
            record["approved_for_resume"] = False
            record["retired_at"] = datetime.now(timezone.utc).isoformat()
            _write(data)
            return record
    raise EvidenceError(f"no claim {claim_id!r}")
