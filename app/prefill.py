"""Pre-fill a job application form in a real browser, and stop before submitting.

The boundary this module keeps is the same one the rest of CareerOS keeps: it
does the tedious part and leaves the consequential part to a human. Concretely
that means it never clicks submit — not as a matter of configuration but
structurally. `SUBMIT_PATTERNS` names the controls that send an application and
`_is_submit` is consulted before any click; there is no flag that turns this off.

Three reasons it works this way rather than as an unattended bot:

* A submitted application cannot be unsent. It carries the candidate's legal
  name, and the fields most likely to be auto-filled wrong — work authorization,
  sponsorship, demographics — are the ones where a wrong value is a
  misrepresentation rather than a typo.
* The forms are defended against exactly that. Greenhouse boards carry invisible
  reCAPTCHA that scores mouse movement and typing rhythm, Indeed sits behind
  Cloudflare, and Dice's robots.txt disallows its own apply path.
* The terms say so. Indeed prohibits "any automation, scripting, or bots to
  automate the Indeed Apply process"; Greenhouse prohibits automated means to
  "access or use the Services". Dice, usefully, permits ordinary browsers as
  "Approved Devices" — which is what a headed window the candidate drives is.

So the browser is always visible, the candidate watches it fill, reads the
highlighted sensitive fields, and presses the button themselves.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Controls that submit an application. Never clicked. Matched against a
# control's text, value, id, name and aria-label.
SUBMIT_PATTERNS = (
    "submit", "apply now", "send application", "finish", "complete application",
    "submit application", "apply for this job", "send", "confirm",
)

# Fields where a wrong value is a misrepresentation rather than a typo. Filled,
# then outlined in the page so the candidate's eye lands on them before they
# press anything.
SENSITIVE = {
    "work_authorization", "visa_status", "sponsorship_requirement",
    "gender", "race_ethnicity", "veteran_disclosure", "disability_disclosure",
    "current_base_salary", "salary_expectation",
}

# Answer key -> patterns that identify the field. Ordered: the first match wins,
# so put narrower patterns first ("preferred name" before "name").
FIELD_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "mobile", "telephone", "contact number")),
    ("linkedin_url", ("linkedin",)),
    ("preferred_name", ("preferred name", "preferred first")),
    ("first_name", ("first name", "given name", "forename")),
    ("last_name", ("last name", "family name", "surname")),
    ("legal_name", ("full name", "legal name", "your name", "name")),
    ("address", ("address", "city", "location", "where are you based")),
    ("sponsorship_requirement", ("sponsor", "visa sponsor", "require sponsorship")),
    ("work_authorization", ("authorized to work", "work authorization",
                            "legally authorized", "right to work")),
    ("visa_status", ("visa status", "immigration status")),
    ("relocation", ("relocate", "relocation", "willing to move")),
    ("remote_preference", ("remote", "onsite", "hybrid", "work arrangement")),
    ("start_date", ("start date", "available to start", "availability",
                    "earliest start", "notice period")),
    ("salary_expectation", ("salary expectation", "expected salary",
                            "desired salary", "compensation expectation",
                            "salary requirement")),
    ("current_base_salary", ("current salary", "current base", "current compensation")),
    ("gender", ("gender",)),
    ("race_ethnicity", ("race", "ethnicity", "hispanic")),
    ("veteran_disclosure", ("veteran", "protected veteran")),
    ("disability_disclosure", ("disability", "disabled")),
    ("website", ("website", "portfolio", "github")),
]


@dataclass
class PrefillReport:
    """What happened, in enough detail to be checked rather than trusted."""

    url: str = ""
    filled: list[tuple[str, str]] = field(default_factory=list)
    sensitive_filled: list[tuple[str, str]] = field(default_factory=list)
    attached: str | None = None
    unfilled: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = [f"\n  {self.url}"]
        if self.attached:
            out.append(f"  attached  {Path(self.attached).name}")
        for key, val in self.filled:
            out.append(f"  filled    {key}: {val[:56]}")
        for key, val in self.sensitive_filled:
            out.append(f"  VERIFY →  {key}: {val[:56]}")
        for label in self.unfilled:
            out.append(f"  left     {label[:70]}")
        for note in self.notes:
            out.append(f"  note      {note}")
        out.append("  NOT SUBMITTED — review the highlighted fields, then submit yourself.")
        return "\n".join(out)


def _is_submit(text: str) -> bool:
    """True when a control looks like it sends the application."""
    low = (text or "").strip().lower()
    return any(p in low for p in SUBMIT_PATTERNS)


def start_date(today: _dt.date | None = None) -> str:
    """One week out, computed per application — the candidate's stated policy."""
    return ((today or _dt.date.today()) + _dt.timedelta(days=7)).isoformat()


def salary_answer(job: dict[str, Any]) -> str | None:
    """Answer salary from the posting, never from the candidate's current pay.

    Their stated policy: use the range the posting itself gives or implies.
    Volunteering a current or desired number when the posting states one is how
    candidates anchor themselves below the band. With nothing stated, this
    returns None and the field is left for a human rather than guessed.
    """
    text = f"{job.get('salaryText') or ''} {job.get('description') or ''}"
    m = re.search(
        r"\$\s?(\d{2,3}(?:,\d{3})?)(?:\s?[-–—to]+\s?\$?\s?(\d{2,3}(?:,\d{3})?))?",
        text,
    )
    if not m:
        return None
    return f"${m.group(1)}" + (f" - ${m.group(2)}" if m.group(2) else "")


def _clean(value: Any) -> str:
    """Strip bookkeeping annotations that were never meant to be typed.

    The stored answers carry notes for the reader — "No, not required (per
    user)" — which are provenance, not the answer. Left in, they get typed
    verbatim into an employer's form.
    """
    text = str(value or "").strip()
    text = re.sub(r"\s*\((?:per user|per candidate|confirmed|assumed)[^)]*\)", "", text, flags=re.I)
    return text.strip(" ,;")


def build_answers(profile_answers: dict[str, Any], job: dict[str, Any]) -> dict[str, str]:
    """Flatten stored answers into the values a form actually asks for."""
    a = {k: (_clean(v) if isinstance(v, str) else v) for k, v in (profile_answers or {}).items()}
    demo = {k: _clean(v) for k, v in (a.get("demographic_preferences") or {}).items()}

    full = str(a.get("legal_name", "")).strip()
    first, _, last = full.partition(" ")

    answers: dict[str, str] = {
        "legal_name": full,
        "preferred_name": str(a.get("preferred_name", full)),
        "first_name": first,
        "last_name": last,
        "email": str(a.get("email", "")),
        "phone": str(a.get("phone", "")),
        "address": str(a.get("address", "")),
        "linkedin_url": str(a.get("linkedin_url", "")),
        "website": str(a.get("linkedin_url", "")),
        "work_authorization": str(a.get("work_authorization", "")),
        "visa_status": str(a.get("visa_status", "")),
        "sponsorship_requirement": str(a.get("sponsorship_requirement", "")),
        "relocation": str(a.get("relocation", "")),
        "remote_preference": str(a.get("remote_preference", "")),
        "start_date": start_date(),
        "gender": str(demo.get("gender", "")),
        "race_ethnicity": str(demo.get("race_ethnicity", "")),
        "veteran_disclosure": str(demo.get("veteran_disclosure", "")),
        "disability_disclosure": str(demo.get("disability_disclosure", "")),
    }

    salary = salary_answer(job)
    if salary:
        answers["salary_expectation"] = salary
    # current_base_salary is deliberately NOT mapped in. It is stored for the
    # candidate's reference, and a form asking "current salary" is a question
    # they should answer themselves — in several states an employer may not ask.

    return {k: v for k, v in answers.items() if v}


def match_field(label: str) -> str | None:
    """Which stored answer, if any, a form field is asking for."""
    low = (label or "").strip().lower()
    if not low:
        return None
    for key, patterns in FIELD_MAP:
        if any(p in low for p in patterns):
            return key
    return None
