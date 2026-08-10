#!/usr/bin/env python3
"""Open a job application in a real browser with the form already filled in.

    python scripts/prefill_apply.py <job_id>
    python scripts/prefill_apply.py --all --min-score 85
    python scripts/prefill_apply.py <job_id> --dry-run    # no browser, just the plan

The browser is always visible and the script never submits. It fills what it can
match, attaches the resume tailored for that specific job, outlines the fields
where a wrong answer would be a misrepresentation, and then hands you the window.

Requires: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.prefill import (  # noqa: E402
    SENSITIVE,
    PrefillReport,
    build_answers,
    match_field,
    _is_submit,
)
from app.profile import load_profile  # noqa: E402

API = "http://localhost:8000"
OUT = Path.home() / "Desktop" / "CareerOS-Applications"

# Outline sensitive fields in the page itself. The report lists them too, but a
# candidate about to press submit is looking at the form, not at a terminal.
HIGHLIGHT = (
    "el => { el.style.outline = '3px solid #d97706';"
    " el.style.outlineOffset = '2px';"
    " el.style.backgroundColor = '#fffbeb'; }"
)


def company_name(record: dict) -> str:
    """The applications API returns company as an object; older rows as a string."""
    c = record.get("company")
    return (c or {}).get("name", "?") if isinstance(c, dict) else str(c or "?")


def api(path: str, method: str = "GET") -> dict | None:
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def resume_for(job_id: str, company: str, title: str) -> Path | None:
    """The PDF tailored for this posting — never a generic one.

    Falls back to downloading it if the packet folder has not been built, so a
    freshly-scored job can still be applied to without a separate step.
    """
    slug = "".join(c if c.isalnum() else "_" for c in company).strip("_")
    for folder in sorted(OUT.glob(f"{slug}*")):
        pdfs = sorted(folder.glob("*.pdf"))
        if pdfs:
            return pdfs[0]

    dest = OUT / slug
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{slug}_resume.pdf"
    try:
        urllib.request.urlretrieve(f"{API}/api/jobs/{job_id}/resume.pdf", target)
        return target
    except Exception:
        return None


def describe(el) -> str:
    """Everything a field says about itself, for matching against."""
    parts = []
    for attr in ("aria-label", "name", "id", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v:
                parts.append(v)
        except Exception:
            pass
    # The visible <label> usually carries the real question.
    try:
        fid = el.get_attribute("id")
        if fid:
            lab = el.page.query_selector(f'label[for="{fid}"]')
            if lab:
                parts.append(lab.inner_text())
    except Exception:
        pass
    return " ".join(parts)


def prefill(page, answers: dict[str, str], resume: Path | None) -> PrefillReport:
    report = PrefillReport(url=page.url)

    if resume and resume.exists():
        for inp in page.query_selector_all('input[type="file"]'):
            try:
                inp.set_input_files(str(resume))
                report.attached = str(resume)
                break
            except Exception:
                continue
        if not report.attached:
            report.notes.append("no file input found — attach the resume manually")

    used: set[str] = set()
    for el in page.query_selector_all("input, textarea, select"):
        try:
            if (el.get_attribute("type") or "").lower() in {
                "file", "hidden", "submit", "button", "image", "checkbox", "radio"
            }:
                continue
            if not el.is_visible() or not el.is_editable():
                continue
        except Exception:
            continue

        label = describe(el)
        key = match_field(label)
        if not key or key not in answers or key in used:
            if label.strip() and not key:
                report.unfilled.append(label.strip())
            continue

        value = answers[key]
        try:
            tag = (el.evaluate("e => e.tagName") or "").lower()
            if tag == "select":
                # Only pick an option that already exists; never invent one.
                matched = False
                for opt in el.query_selector_all("option"):
                    text = (opt.inner_text() or "").strip().lower()
                    if text and text in value.lower() or value.lower() in text:
                        el.select_option(label=opt.inner_text().strip())
                        matched = True
                        break
                if not matched:
                    report.unfilled.append(f"{label.strip()} (dropdown — choose yourself)")
                    continue
            else:
                el.fill(value)
            used.add(key)
            (report.sensitive_filled if key in SENSITIVE else report.filled).append(
                (key, value)
            )
            if key in SENSITIVE:
                el.evaluate(HIGHLIGHT)
        except Exception:
            report.unfilled.append(f"{label.strip()} (could not fill)")

    # State plainly that nothing was pressed, and prove it by naming what was found.
    submits = [
        (b.inner_text() or b.get_attribute("value") or "")
        for b in page.query_selector_all('button, input[type="submit"]')
    ]
    named = [s.strip() for s in submits if _is_submit(s)]
    if named:
        report.notes.append(f"submit control(s) left untouched: {', '.join(named[:3])}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--all", action="store_true", help="every staged application")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="plan only, no browser")
    args = ap.parse_args()

    apps = (api("/api/applications") or {}).get("applications", [])
    if not apps:
        print("API not running, or nothing staged.", file=sys.stderr)
        return 1

    if args.job_id:
        targets = [a for a in apps if a.get("jobId") == args.job_id]
    elif args.all:
        targets = [a for a in apps if (a.get("rawFitScore") or 0) >= args.min_score]
    else:
        ap.error("give a job id or --all")

    if not targets:
        print("Nothing matched.", file=sys.stderr)
        return 1

    stored = load_profile().application_answers

    if args.dry_run:
        for a in targets:
            job = api(f"/api/jobs/{a['jobId']}") or {}
            answers = build_answers(stored, job)
            print(f"\n  {company_name(a)} — {a['title']}")
            print(f"  {a.get('applyUrl')}")
            for k, v in answers.items():
                mark = "VERIFY →" if k in SENSITIVE else "        "
                print(f"    {mark} {k}: {v[:58]}")
        print(f"\n{len(targets)} application(s). No browser opened, nothing submitted.")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright missing. Install:\n"
              "  ./.venv/bin/pip install playwright\n"
              "  ./.venv/bin/playwright install chromium", file=sys.stderr)
        return 1

    with sync_playwright() as pw:
        # headless=False is the point, not a default: the candidate has to be
        # able to read the form and press the button.
        browser = pw.chromium.launch(headless=False, slow_mo=120)
        for a in targets:
            url = a.get("applyUrl")
            if not url:
                print(f"  {company_name(a)}: no apply URL")
                continue
            job = api(f"/api/jobs/{a['jobId']}") or {}
            answers = build_answers(stored, job)
            resume = resume_for(a["jobId"], company_name(a), a["title"])

            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                print(f"\n=== {company_name(a)} — {a['title']} ===")
                print(prefill(page, answers, resume).render())
            except Exception as exc:
                print(f"  {company_name(a)}: could not load — {exc}")

        print("\nAll tabs are open and filled. Review, then submit each yourself.")
        input("Press Enter to close the browser… ")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
