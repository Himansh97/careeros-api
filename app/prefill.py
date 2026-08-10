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
    "hispanic_latino",
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
    # Ahead of sponsorship_requirement, whose bare "sponsor" pattern would
    # otherwise claim the now-or-future phrasing and answer a different
    # question than the one asked.
    ("sponsorship_future", ("now or in the future require sponsorship",
                            "will you now or in the future",
                            "now or in the future")),
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
    # Must precede race_ethnicity: "Are you Hispanic/Latino?" is a separate
    # yes/no question, and the broader "hispanic"/"ethnicity" patterns below
    # would otherwise claim it and answer it with a full ethnicity string.
    ("hispanic_latino", ("hispanic/latino", "are you hispanic", "hispanic or latino",
                         "hispanic_ethnicity")),
    ("race_ethnicity", ("race", "ethnicity", "hispanic")),
    ("veteran_disclosure", ("veteran", "protected veteran")),
    ("disability_disclosure", ("disability", "disabled")),
    ("website", ("website", "portfolio", "github")),
    # Recurring questions that are facts about the candidate but are not on a
    # resume, so they live in application_answers.yaml. Left blank until the
    # candidate states them once — a legal question about agreements they have
    # signed is not something to infer from a default.
    ("employment_restrictions", ("employment agreement", "post-employment",
                                 "non-compete", "noncompete", "restrictive covenant",
                                 "post employment restriction")),
    ("interview_accommodation", ("accommodation", "accessible and inclusive",
                                 "adjustments", "accommodations")),
    ("country", ("country where you", "country of residence", "which country",
                 "country you currently reside", "country")),
    ("school", ("school", "university", "college", "institution")),
    ("degree_level", ("degree",)),
    ("discipline", ("discipline", "field of study", "major")),
    ("current_employer", ("current or previous employer", "current employer",
                          "most recent employer", "previous employer")),
    ("current_title", ("current or previous job title", "current job title",
                       "most recent title", "previous job title")),
    ("previously_employed_here", ("ever been employed by", "previously employed by",
                                  "previously worked at", "worked at or consulted",
                                  "worked at our company", "former employee")),
]

# Questions whose stored answer does not fit the question as asked, so filling
# them would put a wrong answer on a form the candidate signs.
#
# The live example: the stored sponsorship answer is "No, not required", which
# is true of today. Stripe asks whether sponsorship will be required "now OR IN
# THE FUTURE" — and on F-1 OPT the honest answer to the future half is normally
# yes, because OPT expires. Same stored value, materially different question.
# So this is surfaced for the candidate rather than answered for them.
AMBIGUOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "now or in the future",
        "Asks about sponsorship NOW OR IN THE FUTURE. Your stored answer covers "
        "today only; on OPT the future half usually needs a different answer. "
        "Answer this one yourself.",
    ),
    (
        "in the future",
        "Asks about a future requirement, which your stored answer does not "
        "cover. Answer this one yourself.",
    ),
)


def ambiguous_reason(label: str, answers: dict[str, str] | None = None) -> str | None:
    """Why a field must not be auto-answered, if it must not be.

    The block is for a MISSING answer, not a permanent refusal. Once
    `sponsorship_future` is recorded the question has a stated answer and fills
    like any other — which also makes it consistent across every application,
    the thing most worth getting right about it.
    """
    if (answers or {}).get("sponsorship_future"):
        return None
    low = (label or "").lower()
    if "sponsor" not in low and "authoriz" not in low:
        return None
    for pattern, reason in AMBIGUOUS_PATTERNS:
        if pattern in low:
            return reason
    return None


@dataclass
class PrefillReport:
    """What happened, in enough detail to be checked rather than trusted."""

    url: str = ""
    filled: list[tuple[str, str]] = field(default_factory=list)
    sensitive_filled: list[tuple[str, str]] = field(default_factory=list)
    attached: str | None = None
    unfilled: list[str] = field(default_factory=list)
    needs_you: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = [f"\n  {self.url}"]
        if self.attached:
            out.append(f"  attached  {Path(self.attached).name}")
        for key, val in self.filled:
            out.append(f"  filled    {key}: {val[:56]}")
        for key, val in self.sensitive_filled:
            out.append(f"  VERIFY →  {key}: {val[:56]}")
        for label, why in self.needs_you:
            out.append(f"  ANSWER →  {label}")
            out.append(f"            {why}")
        for label in self.unfilled:
            # Truncate the question, not the trailing marker that says why it
            # was left — cutting "(your choice)" off made a deliberate skip
            # look identical to a failure to match.
            marker = ""
            for suffix in ("(your choice)", "(dropdown — choose yourself)", "(could not fill)"):
                if label.endswith(suffix):
                    label, marker = label[: -len(suffix)].rstrip(), " " + suffix
                    break
            out.append(f"  left     {label[:64]}{marker}")
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


def _education(profile: Any) -> tuple[str, str, str]:
    """Most recent degree, as (school, degree, discipline)."""
    entries = list(getattr(profile, "education", None) or [])
    if not entries:
        return "", "", ""
    top = entries[0]
    degree = str(top.get("degree", ""))
    discipline = ""
    if " in " in degree:
        discipline = degree.split(" in ", 1)[1]
    return str(top.get("institution", "")), degree, discipline


def _current_role(profile: Any) -> tuple[str, str]:
    """Current or most recent (employer, title)."""
    roles = list(getattr(profile, "employment_history", None) or [])
    if not roles:
        return "", ""
    current = next((r for r in roles if str(r.get("end_date", "")).lower() == "present"), roles[0])
    return str(current.get("employer", "")), str(current.get("title", ""))


def build_answers(
    profile_answers: dict[str, Any],
    job: dict[str, Any],
    profile: Any = None,
) -> dict[str, str]:
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
        # Stated once by the candidate so every application answers them the
        # same way. Inconsistent sponsorship answers across applications from
        # the same person are exactly what gets noticed.
        "sponsorship_future": str(a.get("sponsorship_future", "")),
        "employment_restrictions": str(a.get("employment_restrictions", "")),
        "interview_accommodation": str(a.get("interview_accommodation", "")),
    }

    # Asked as its own yes/no on most EEO forms, while the stored answer is a
    # single ethnicity string.
    eth = str(demo.get("race_ethnicity", "")).lower()
    if eth:
        if any(d in eth for d in _DECLINE_VALUES):
            answers["hispanic_latino"] = "Decline To Self Identify"
        elif "not hispanic" in eth or "non-hispanic" in eth:
            answers["hispanic_latino"] = "No"
        elif "hispanic" in eth or "latino" in eth:
            answers["hispanic_latino"] = "Yes"

    # Country is asked constantly and is derivable from the stored address
    # rather than being a separate answer to maintain.
    addr = str(a.get("address", ""))
    if re.search(r",\s*[A-Z]{2}\b", addr) or "united states" in addr.lower():
        answers["country"] = "United States"

    if profile is not None:
        school, degree, discipline = _education(profile)
        # Greenhouse's Degree control is an enum of levels, not free text, so
        # the full degree name never matches one of its options.
        answers["degree_level"] = (
            "Master's Degree" if degree.lower().startswith(("master", "mba", "m.s", "ms "))
            else "Bachelor's Degree" if degree.lower().startswith(("bachelor", "b.s", "bs "))
            else ""
        )
        employer, title = _current_role(profile)
        answers.update(
            {k: v for k, v in {
                "school": school,
                "degree": degree,
                "discipline": discipline,
                "current_employer": employer,
                "current_title": title,
            }.items() if v}
        )
        # "Have you ever been employed by <this company>?" is checkable against
        # the employment history rather than guessed.
        company = str((job.get("company") or {}).get("name", "")).lower()
        if company:
            employers = " ".join(
                str(r.get("employer", "")) for r in (getattr(profile, "employment_history", None) or [])
            ).lower()
            answers["previously_employed_here"] = "Yes" if company in employers else "No"

    salary = salary_answer(job)
    if salary:
        answers["salary_expectation"] = salary
    # current_base_salary is deliberately NOT mapped in. It is stored for the
    # candidate's reference, and a form asking "current salary" is a question
    # they should answer themselves — in several states an employer may not ask.

    return {k: v for k, v in answers.items() if v}


# Consent and marketing questions. The candidate's own preference, not a fact
# about them, and nothing in the profile answers them — so they are left alone
# rather than defaulted either way.
CONSENT_PATTERNS = (
    "opt-in", "opt in", "receive whatsapp", "receive text", "receive sms",
    "marketing", "newsletter", "record and transcribe", "recording",
    "consent to", "agree to receive", "brighthire",
)


def is_consent_question(label: str) -> bool:
    return any(p in (label or "").lower() for p in CONSENT_PATTERNS)


# When a question is a plain Yes/No but the stored answer is a description
# ("OPT (F-1 Optional Practical Training)"), the description cannot match an
# option. These are the only keys where the yes/no answer follows directly from
# the stored fact, so it can be resolved without guessing.
YES_NO_FALLBACK: dict[str, str] = {
    # Holding OPT authorization IS being authorized to work. Note this is
    # deliberately absent for sponsorship: "will you require sponsorship" does
    # not follow from current authorization, and on OPT the honest answer
    # changes with the timeframe the question asks about.
    "work_authorization": "Yes",
    "relocation": "Yes",
}


# EEO dropdowns phrase their options as sentences ("I am not a protected
# veteran", "Decline To Self Identify") which no stored answer matches by
# substring. These map the candidate's stated INTENT onto whatever wording a
# given employer uses, without ever picking a different disclosure than the
# one they chose.
_DECLINE_VALUES = ("prefer not", "decline", "do not wish", "don't wish",
                   "do not want", "not disclose", "rather not")
_DECLINE_OPTIONS = ("decline", "don't wish", "do not wish", "do not want",
                    "prefer not", "not to answer", "not specified")


def _eeo_score(opt: str, val: str) -> int | None:
    """Score EEO options by intent. None when this is not an EEO question."""
    if any(d in val for d in _DECLINE_VALUES):
        return 95 if any(d in opt for d in _DECLINE_OPTIONS) else 0

    if "veteran" in val or "veteran" in opt:
        declines = any(d in opt for d in _DECLINE_OPTIONS)
        if val.startswith("no") or "not a veteran" in val:
            return 95 if ("not a protected veteran" in opt or opt == "no") else 0
        if val.startswith("yes") or "identify as" in val:
            return 95 if ("identify as" in opt or opt == "yes") else 0
        return 0 if declines else None

    if "disab" in val and "disab" in opt:
        if val.startswith("no"):
            return 95 if opt.startswith("no") else 0
        if val.startswith("yes"):
            return 95 if opt.startswith("yes") else 0
    return None


def option_score(option_text: str, value: str) -> int:
    """How well a dropdown option answers a stored value. Higher is better, 0 = no.

    Yes/No dropdowns are the common case and the stored answers are sentences —
    "Yes, willing to relocate" must select the option "Yes" without also
    matching "No" on a substring. So a leading yes/no is resolved first and
    only against an option that is exactly that word.
    """
    opt = (option_text or "").strip().lower()
    val = (value or "").strip().lower()
    if not opt or not val:
        return 0

    # An exact match always wins, before any shortcut. Without this the yes/no
    # shortcut below claimed the FIRST option beginning "Yes," — so an answer of
    # "Yes, F-1 Visa OPT (USA)" selected "Yes, EU Blue Card" on GitLab's visa
    # question. A near-miss there is a false immigration claim on a signed form,
    # not a cosmetic mismatch.
    if opt == val:
        return 120

    eeo = _eeo_score(opt, val)
    if eeo is not None:
        return eeo

    for word in ("yes", "no"):
        if not val.startswith(word):
            continue
        if len(val) <= len(word) + 2:
            # A bare "Yes"/"No" answers only the bare option.
            return 100 if opt == word else 0
        # A detailed answer ("Yes, F-1 Visa OPT (USA)") still answers a plain
        # Yes/No control, but must NOT claim a different detailed option — the
        # earlier shortcut scored every option beginning "Yes," as a perfect
        # match, and the shorter-string tiebreak then picked "Yes, EU Blue
        # Card" for an F-1 OPT answer.
        if opt == word:
            return 80
        break

    if opt == val:
        return 90
    if val.startswith(opt) or opt.startswith(val):
        return 70
    if opt in val or val in opt:
        return 50
    return 0


def best_option(options: list[str], value: str) -> str | None:
    """Pick the option that answers `value`, or None rather than guessing."""
    scored = [(option_score(o, value), o) for o in options]
    scored = [(s, o) for s, o in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    return scored[0][1]


def apply_form_url(job_id: str, stored_url: str) -> str:
    """The URL of the actual application FORM, not the posting.

    A Greenhouse board's `absolute_url` is whatever the employer set, and large
    employers set it to their own careers site. Stripe's points at
    `stripe.com/jobs/search?gh_jid=…`, which redirects to a role-search page
    carrying one search box and no form at all — so a filler lands somewhere
    with nothing to fill and reports, accurately but uselessly, that it found
    no file input.

    Greenhouse always exposes the real form at a canonical address derived from
    the job's numeric token, so for Greenhouse-sourced jobs that is used
    directly. Everything else keeps the stored URL.
    """
    m = re.fullmatch(r"gh_[a-z0-9\-]+_(\d+)", job_id or "")
    if m:
        return f"https://boards.greenhouse.io/embed/job_app?token={m.group(1)}"
    return stored_url


def match_field(label: str) -> str | None:
    """Which stored answer, if any, a form field is asking for.

    Patterns match on word boundaries, not as bare substrings. "city" is inside
    "ethni-city", so an EEO question — "Are you Hispanic/Latino?", label
    `hispanic_ethnicity` — resolved to `address` and got filled with a home
    town. The same failure mode as short skill aliases matching inside longer
    words; it is worth being strict about by default.
    """
    low = (label or "").strip().lower()
    if not low:
        return None
    for key, patterns in FIELD_MAP:
        for p in patterns:
            # Multi-word patterns are already specific enough to be safe as
            # substrings; single words are the ones that collide.
            if " " in p or "/" in p or "_" in p:
                if p in low:
                    return key
            elif re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", low):
                return key
    return None
