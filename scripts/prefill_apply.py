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
import functools
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.prefill import (  # noqa: E402
    SENSITIVE,
    ambiguous_reason,
    best_option,
    is_consent_question,
    YES_NO_FALLBACK,
    PrefillReport,
    build_answers,
    apply_form_url,
    match_field,
    _is_submit,
)
from app.documents import safe_filename  # noqa: E402
from app.profile import load_profile  # noqa: E402

# The process stays alive while the browser is open, so buffered output
# would never reach the terminal. Flush every print.
print = functools.partial(__builtins__.print if not isinstance(__builtins__, dict)
                          else __builtins__['print'], flush=True)

API = "http://localhost:8000"
OUT = Path.home() / "Desktop" / "CareerOS-Applications"

# Outline sensitive fields in the page itself. The report lists them too, but a
# candidate about to press submit is looking at the form, not at a terminal.
HIGHLIGHT = (
    "el => { el.style.outline = '3px solid #d97706';"
    " el.style.outlineOffset = '2px';"
    " el.style.backgroundColor = '#fffbeb'; }"
)


def css_escape(ident: str) -> str:
    """Escape an id for use in a CSS selector (Greenhouse ids are plain, but
    bracketed names like `question_123[]` appear on multi-selects)."""
    return re.sub(r'([^a-zA-Z0-9_-])', r'\\\1', ident)


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
    # Packets are one folder per company AND role. Searching by company alone
    # attached whichever résumé happened to sort first when several GitLab or
    # Stripe roles were staged.
    dest = OUT / safe_filename(company) / safe_filename(title)[:60]
    pdfs = sorted(dest.glob("*.pdf"))
    if pdfs:
        return pdfs[0]

    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{safe_filename(company)}_{safe_filename(title)[:50]}_resume.pdf"
    try:
        urllib.request.urlretrieve(f"{API}/api/jobs/{job_id}/resume.pdf", target)
        return target
    except Exception:
        return None


def describe(el) -> str:
    """Everything a field says about itself, for matching against.

    Greenhouse labels its custom questions with `<label for="question_123">`
    while the input carries that value as its NAME, not its id. Looking the
    label up by id alone found nothing, so every employer question arrived as
    an opaque "question_68123366" and was skipped — the fields most worth
    surfacing were the ones that went missing.
    """
    parts = []
    for attr in ("aria-label", "name", "id", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v:
                parts.append(v)
        except Exception:
            pass
    # ElementHandle has no `.page`; it has `owner_frame()`. The old code read
    # `el.page`, which raised AttributeError straight into a bare except — so
    # this lookup never ran once, and every employer question arrived as an
    # opaque "question_68123369". Silent failure inside a broad except is why
    # it looked like a matching problem for so long.
    frame = el.owner_frame()
    try:
        for key in (el.get_attribute("id"), el.get_attribute("name")):
            if not key or frame is None:
                continue
            lab = frame.query_selector(f'label[for="{key}"]')
            if lab:
                parts.append(lab.inner_text())
                break
        else:
            # Custom dropdowns (react-select and friends) render a visible
            # combobox carrying no name at all, keeping the real question on a
            # sibling label. Walk up until an ancestor contains one, so the
            # field reads as its question rather than "question_68123366" —
            # which is exactly the set of fields most worth surfacing.
            txt = el.evaluate(
                "e => { let n = e;"
                " for (let i = 0; i < 6 && n; i++) { n = n.parentElement; if (!n) break;"
                "  const l = n.querySelector('label');"
                "  if (l && l.innerText && l.innerText.trim().length > 6)"
                "    return l.innerText.trim();"
                "  const t = (n.innerText || '').trim();"
                "  if (t && t.length > 6 && t.length < 220) return t.split('\n')[0];"
                " } return ''; }"
            )
            if txt:
                parts.append(txt)
    except Exception:
        pass
    return " ".join(parts)


def choose_option(page, el, value: str, tag: str, key: str = "") -> bool:
    """Select `value` in a dropdown, native or React-style.

    Greenhouse renders its custom questions as react-select: a text input with
    role="combobox" whose options only exist in the DOM once the control is
    opened. `select_option` does nothing there, so the control is clicked, the
    rendered options are read, and the best match is clicked. Nothing is typed
    into it — an option that does not exist is left for the candidate rather
    than invented.
    """
    if tag == "select":
        options = [(o.inner_text() or "").strip() for o in el.query_selector_all("option")]
        live = [o for o in options if o]
        pick = best_option(live, value)
        if not pick and key in YES_NO_FALLBACK and {o.lower() for o in live} <= {"yes", "no"}:
            pick = best_option(live, YES_NO_FALLBACK[key])
        if not pick:
            return False
        el.select_option(label=pick)
        return True

    # react-select ids every option as `react-select-<input id>-option-N`.
    # Querying options page-wide instead returned whichever menus happened to
    # be in the DOM — on this form, the phone country-code list — so opening
    # "Country" produced 246 options reading "Afghanistan +93". Scope to the
    # control that was actually clicked.
    own_id = ""
    try:
        own_id = el.get_attribute("id") or ""
    except Exception:
        pass
    scoped = f'[id^="react-select-{own_id}-option"]' if own_id else ""

    def options():
        hs = page.query_selector_all(scoped) if scoped else []
        if not hs:
            container = el.evaluate_handle(
                "e => e.closest('.select__container, .select-shell') || e.parentElement"
            ).as_element()
            if container:
                hs = container.query_selector_all('[role="option"], .select__option')
        return hs, [(h.inner_text() or "").strip() for h in hs]

    try:
        # Close anything a previous control left open: an open react-select menu
        # is an overlay, and the next click lands on it instead of the field
        # being targeted — which silently produced "no options" for whichever
        # dropdown happened to follow a successful one.
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)
        except Exception:
            pass

        el.scroll_into_view_if_needed(timeout=3000)
        el.click(timeout=3000)
        page.wait_for_timeout(400)

        # One retry: the first click sometimes only dismisses a stale overlay.
        if not options()[0]:
            el.click(timeout=3000)
            page.wait_for_timeout(400)

        # Read the list BEFORE typing. An enumerated dropdown (Yes/No, Country)
        # renders every option on open, and typing a long stored value into it
        # filters the list to nothing — which is why "OPT (F-1 Optional
        # Practical Training)" produced zero options on a plain Yes/No control.
        handles, texts = options()
        live = [t for t in texts if t]

        if not live:
            # No options on open means a typeahead, which renders matches only
            # once the query narrows them.
            try:
                el.type(value[:40], delay=25)
                page.wait_for_timeout(700)
            except Exception:
                pass
            handles, texts = options()
            live = [t for t in texts if t]

        pick = best_option(live, value)

        if not pick and key in YES_NO_FALLBACK and {t.lower() for t in live} <= {"yes", "no"} and live:
            pick = best_option(live, YES_NO_FALLBACK[key])

        if not pick and live and len(live) > 8:
            # Long enumerated list (countries): narrow it by typing, then retry.
            try:
                el.type(value[:24], delay=25)
                page.wait_for_timeout(600)
                handles, texts = options()
                live = [t for t in texts if t]
                pick = best_option(live, value)
            except Exception:
                pass

        if not pick and len(live) == 1:
            pick = live[0]

        if not pick:
            page.keyboard.press("Escape")
            return False
        for h, t in zip(handles, texts):
            if t == pick:
                h.click(timeout=3000)
                page.wait_for_timeout(200)
                return True
        page.keyboard.press("Escape")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return False


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

    # Two passes. react-select re-renders the form each time an option is
    # chosen, which detaches every ElementHandle collected beforehand — so a
    # dropdown late in the list failed with "no options" purely because its
    # handle was stale, while working perfectly when clicked on its own. Plan
    # first against a snapshot, then re-resolve each field immediately before
    # touching it.
    plan: list[tuple[str, str, str]] = []   # (selector, label, key)
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
        ident = el.get_attribute("id") or ""
        name = el.get_attribute("name") or ""
        if ident:
            selector = f'#{css_escape(ident)}'
        elif name:
            selector = f'[name="{name}"]'
        else:
            selector = ""
        plan.append((selector, label, ""))

    used: set[str] = set()
    for selector, label, _ in plan:
        if is_consent_question(label):
            report.unfilled.append(f"{label.strip()[:70]} (your choice)")
            continue

        reason = ambiguous_reason(label, answers)
        if reason:
            report.needs_you.append((label.strip()[:80], reason))
            continue

        key = match_field(label)
        if not key or key not in answers or key in used:
            if label.strip() and not key:
                report.unfilled.append(label.strip())
            continue

        el = page.query_selector(selector) if selector else None
        if el is None:
            report.unfilled.append(f"{label.strip()[:70]} (could not re-locate)")
            continue

        value = answers[key]
        try:
            tag = (el.evaluate("e => e.tagName") or "").lower()
            role = (el.get_attribute("role") or "").lower()
            if tag == "select" or role == "combobox":
                if not choose_option(page, el, value, tag, key):
                    report.unfilled.append(f"{label.strip()[:70]} (dropdown — choose yourself)")
                    continue
            else:
                el.fill(value)
            used.add(key)
            (report.sensitive_filled if key in SENSITIVE else report.filled).append(
                (key, value)
            )
            if key in SENSITIVE:
                try:
                    el.evaluate(HIGHLIGHT)
                except Exception:
                    pass
        except Exception:
            report.unfilled.append(f"{label.strip()[:70]} (could not fill)")

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

    # Drop anything not worth opening a form for. Selection previously filtered
    # on fit score alone, so `--all` staged the ITAR and citizenship/clearance
    # roles the eligibility gate had already ruled out, plus postings that had
    # since been taken down. Walking a candidate through an application they
    # cannot legally accept wastes their time at best.
    kept: list[dict] = []
    for a in targets:
        job = api(f"/api/jobs/{a['jobId']}")
        if job is None:
            print(f"  SKIP {company_name(a)} — {a['title']}: posting no longer live")
            continue
        verdict = (job.get("eligibility") or {}).get("verdict")
        if verdict == "INELIGIBLE":
            why = "; ".join(
                b.get("detail", b.get("type", ""))
                for b in (job.get("eligibility") or {}).get("blockers", [])
            )[:90]
            print(f"  SKIP {company_name(a)} — {a['title']}: INELIGIBLE — {why}")
            continue
        kept.append(a)

    if not kept:
        print("Nothing eligible to apply to.", file=sys.stderr)
        return 1
    targets = kept

    profile = load_profile()
    stored = profile.application_answers

    if args.dry_run:
        for a in targets:
            job = api(f"/api/jobs/{a['jobId']}") or {}
            answers = build_answers(stored, job, profile)
            print(f"\n  {company_name(a)} — {a['title']}")
            print(f"  {apply_form_url(a['jobId'], a.get('applyUrl') or '')}")
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
        browser = pw.chromium.launch(headless=False, slow_mo=20)
        for a in targets:
            url = apply_form_url(a["jobId"], a.get("applyUrl") or "")
            if not url:
                print(f"  {company_name(a)}: no apply URL")
                continue
            job = api(f"/api/jobs/{a['jobId']}") or {}
            answers = build_answers(stored, job, profile)
            resume = resume_for(a["jobId"], company_name(a), a["title"])

            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(800)
                print(f"\n=== {company_name(a)} — {a['title']} ===")
                print(prefill(page, answers, resume).render())
            except Exception as exc:
                print(f"  {company_name(a)}: could not load — {exc}")

        print("\nAll tabs are open and filled. Review, then submit each yourself.")
        # Hold the window open until the candidate is finished with it. When
        # launched from a terminal that is a keypress; when launched detached
        # (no TTY) input() would raise EOFError and close the browser out from
        # under them mid-application, so wait on the tabs instead.
        if sys.stdin.isatty():
            input("Press Enter to close the browser… ")
        else:
            print("Close the browser window when you're done.")
            try:
                while browser.is_connected() and any(
                    not p.is_closed() for c in browser.contexts for p in c.pages
                ):
                    time.sleep(2)
            except (KeyboardInterrupt, Exception):
                pass
        if browser.is_connected():
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
