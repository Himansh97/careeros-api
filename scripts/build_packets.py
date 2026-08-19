"""Regenerate every application packet on the Desktop, one folder per ROLE.

Packets were previously keyed by company alone, so three Stripe applications
wrote Resume.pdf to the same directory and the last one silently overwrote the
other two — meaning two of those applications would have gone out carrying a
resume tailored for a different job. Folders are keyed by company *and* role.

Run:  ./.venv/bin/python scripts/build_packets.py
"""
from __future__ import annotations

import io
import json
import pathlib
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.documents import build_docx, build_pdf, safe_filename  # noqa: E402
from app.profile import load_profile  # noqa: E402
from app.resume_qa import check_pdf, check_resume  # noqa: E402
from app.scoring import score_job  # noqa: E402
from app.tailor import tailor_resume  # noqa: E402

API = "http://localhost:8000"
ROOT = pathlib.Path.home() / "Desktop" / "CareerOS-Applications"


def fetch(job_id: str) -> dict | None:
    """Fetch one job, tolerating whatever an importer put in the id.

    Ids are not all url-safe: a manually imported row carries
    "imported_ZipRecruiter (imported)_6", and dropping that straight into a
    path raised InvalidURL from http.client — an uncaught exception class, so
    one such row aborted the whole packet rebuild partway through and left the
    Desktop folder half old and half new.
    """
    url = f"{API}/api/jobs/{urllib.parse.quote(job_id, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def main() -> int:
    profile = load_profile()
    db = sqlite3.connect(pathlib.Path(__file__).resolve().parent.parent / "careeros.db")
    rows = list(db.execute("select job_id, company, title from applications"))

    ROOT.mkdir(parents=True, exist_ok=True)
    written: dict[pathlib.Path, str] = {}
    listing: list[tuple[int, str, str, str, pathlib.Path]] = []
    problems: list[str] = []
    unreachable = 0

    for job_id, company, title in rows:
        job = fetch(job_id)
        if job is None:
            unreachable += 1
            problems.append(f"{company} — {title}: job could not be fetched, packet NOT written")
            continue

        # Prefer the live posting's company name over the copy stored when the
        # application row was created — brand casing (SoFi, GitLab) is fixed at
        # the source, and the stored copy would keep the old spelling forever.
        company = job.get("company", {}).get("name") or company

        score = score_job(job, profile)
        resume = tailor_resume(job, score, profile)
        pdf = build_pdf(resume, profile)
        docx = build_docx(resume, profile)

        findings = [
            f for f in check_resume(resume, profile) + check_pdf(pdf, profile)
            if f["severity"] in ("high", "medium")
        ]
        for f in findings:
            problems.append(f"{company} — {title}: [{f['severity']}] {f['type']} — {f['detail']}")

        # One folder per role, never per company.
        folder = ROOT / safe_filename(company) / safe_filename(title)[:60]
        if folder in written:
            problems.append(f"COLLISION: {folder} already written for {written[folder]}")
        written[folder] = f"{company} — {title}"

        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(f"Himanshu_Srivastava_{company}_{title}")[:80]
        (folder / f"{stem}.pdf").write_bytes(pdf)
        (folder / f"{stem}.docx").write_bytes(docx)

        listing.append(
            (resume["resumeScore"], company, title, job.get("applyUrl", ""), folder)
        )

    listing.sort(key=lambda t: -t[0])
    lines = ["# CareerOS — Eligible Applications", ""]
    for sc, company, title, url, folder in listing:
        lines += [f"## {sc} — {company}", f"**{title}**",
                  f"Apply: {url}", f"Files: `{folder}`", ""]
    (ROOT / "APPLY_LIST.md").write_text("\n".join(lines))

    print(f"{len(listing)} packet(s) written to {ROOT}")
    print(f"{len(written)} distinct folders — {'no collisions' if len(written) == len(listing) else 'COLLISIONS PRESENT'}")
    if unreachable:
        print(f"{unreachable} job(s) unreachable — those packets were left untouched")
    if problems:
        print("\nissues:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("no high/medium QA findings")
    return 1 if any(p.startswith("COLLISION") for p in problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
