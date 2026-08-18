"""Pre-export QA — the checks a recruiter makes in the first ten seconds.

These are the things that get a resume set aside regardless of how strong the
underlying experience is: repeated text, unexplained timeline gaps, wording
that undercuts the candidate's own claim. Each finding names the fix rather
than just flagging a problem.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

# Phrases that quietly weaken a work-experience bullet. A recruiter reads
# "in coursework" under PROFESSIONAL EXPERIENCE as "this was not a job".
UNDERCUTTING_PHRASES = [
    ("in coursework", "reads as academic work under a professional-experience heading"),
    ("assisted with", "passive; states help rather than ownership"),
    ("helped to", "passive; states help rather than ownership"),
    ("responsible for", "describes a duty rather than an outcome"),
    ("familiar with", "signals awareness rather than capability"),
    ("exposure to", "signals awareness rather than capability"),
]


def _month_index(token: str) -> int | None:
    """'2023-07' -> months since year 0, for gap arithmetic."""
    token = (token or "").strip()
    if token.lower() == "present":
        today = date.today()
        return today.year * 12 + today.month
    m = re.match(r"(\d{4})-(\d{2})", token)
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2))


def check_resume(resume: dict[str, Any], profile: Any) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    # 0. A pinned summary or headline that has drifted from the generator.
    #
    # A saved edit wins over the generated field forever, which is right for a
    # deliberate rewrite and wrong once the generator improves past it. The
    # repo already carries this scar: an edit saved before the summary learned
    # to name the MS in Business Analytics silently kept dropping that
    # credential from the highest-scoring resume.
    #
    # Found again in an audit — a pinned summary shipping "end-to-end AI  data
    # engineering, solutions backed by" (note the double space and the mangled
    # clause order) while the generator produced clean text. Nothing surfaced
    # it, because a pin is invisible once saved.
    for field in resume.get("editedFields") or []:
        text = str(resume.get(field) or "")
        if not text:
            continue
        if re.search(r"\s{2,}", text):
            findings.append({
                "severity": "medium",
                "type": "pinned_field_defect",
                "detail": (
                    f"The {field} is a saved edit containing doubled whitespace. "
                    "Pinned fields override the generator permanently, so a typo "
                    "here ships on every resume for this job until it is cleared."
                ),
            })
        # The credential is the specific thing a stale pin has dropped before.
        if field == "summary" and not re.search(r"\bMS\b|Master", text):
            findings.append({
                "severity": "medium",
                "type": "pinned_field_stale",
                "detail": (
                    "The summary is a saved edit that does not mention the "
                    "master's credential the generator now includes. Clear it to "
                    "pick up the current wording, or keep it deliberately."
                ),
            })

    # 1. Repeated text between a role heading and its subtitle.
    for section in resume.get("sections", []):
        heading = section.get("heading", "")
        employer = section.get("employer", "")
        subtitle = ""
        for role in profile.employment_history or []:
            if role.get("employer") == employer:
                subtitle = role.get("subtitle", "") or ""
        if subtitle and subtitle.lower().strip() in heading.lower():
            findings.append({
                "severity": "high",
                "type": "repetition",
                "where": employer,
                "detail": (
                    f"The subtitle '{subtitle}' already appears inside the job "
                    "title, so it prints twice."
                ),
                "fix": "Remove the parenthetical from the title and keep the subtitle.",
            })

    # 2. Employment gaps. A recruiter will ask about anything over ~4 months.
    roles = sorted(
        (r for r in (profile.employment_history or []) if r.get("start_date")),
        key=lambda r: r.get("start_date", ""),
    )
    for prev, nxt in zip(roles, roles[1:]):
        end = _month_index(prev.get("end_date", ""))
        start = _month_index(nxt.get("start_date", ""))
        if end is None or start is None:
            continue
        gap = start - end
        if gap < 4:
            continue

        # A degree spanning the gap explains it, provided the resume actually
        # shows the study period rather than only a graduation date.
        covered_by = None
        for edu in profile.education or []:
            e_start = _month_index(edu.get("start_date", ""))
            e_end = _month_index(edu.get("graduation_date", ""))
            if e_start and e_end and e_start <= end + 3 and e_end >= start - 3:
                covered_by = edu
                break

        if covered_by:
            findings.append({
                "severity": "info",
                "type": "timeline_gap_explained",
                "where": f"{prev.get('employer')} → {nxt.get('employer')}",
                "detail": (
                    f"{gap} month gap is covered by {covered_by.get('degree')} "
                    f"({covered_by.get('start_date')} to "
                    f"{covered_by.get('graduation_date')}), shown in EDUCATION."
                ),
                "fix": "No action needed.",
            })
        else:
            findings.append({
                "severity": "high" if gap >= 12 else "medium",
                "type": "timeline_gap",
                "where": f"{prev.get('employer')} → {nxt.get('employer')}",
                "detail": (
                    f"{gap} month gap between {prev.get('end_date')} and "
                    f"{nxt.get('start_date')}. A recruiter will ask about it."
                ),
                "fix": (
                    "If a degree covers this period, add its start date so "
                    "EDUCATION shows the study period, not just graduation."
                ),
            })

    # 3. Wording that undercuts the bullet it sits in.
    for section in resume.get("sections", []):
        for bullet in section.get("bullets", []):
            text = (bullet.get("text") or "").lower()
            for phrase, why in UNDERCUTTING_PHRASES:
                if phrase in text:
                    findings.append({
                        "severity": "medium",
                        "type": "weak_phrasing",
                        "where": section.get("employer", ""),
                        "detail": f"Bullet contains '{phrase}' — {why}.",
                        "fix": (
                            "Rewrite only if it stays true; otherwise consider "
                            "moving the item out of professional experience."
                        ),
                    })

    # 4. A duplicated opening verb across consecutive bullets reads as padding.
    for section in resume.get("sections", []):
        verbs = [
            (b.get("text") or "").split(" ")[0].lower()
            for b in section.get("bullets", [])
        ]
        for verb in set(verbs):
            if verb and verbs.count(verb) > 1:
                findings.append({
                    "severity": "low",
                    "type": "repeated_verb",
                    "where": section.get("employer", ""),
                    "detail": f"{verbs.count(verb)} bullets open with '{verb}'.",
                    "fix": "Vary the opening verb so the bullets don't read as one block.",
                })

    # 5. Education that ends after a later role begins is worth a second look.
    for edu in profile.education or []:
        grad = _month_index(edu.get("graduation_date", ""))
        for role in profile.employment_history or []:
            start = _month_index(role.get("start_date", ""))
            if grad and start and role.get("employer") in (edu.get("institution"), ""):
                if start > grad:
                    findings.append({
                        "severity": "low",
                        "type": "post_graduation_role",
                        "where": role.get("employer", ""),
                        "detail": (
                            f"Role at {role.get('employer')} starts "
                            f"{role.get('start_date')}, after graduating "
                            f"{edu.get('graduation_date')} from the same institution."
                        ),
                        "fix": "Legitimate, but be ready to explain it in a screen.",
                    })

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return findings


# --------------------------------------------------------------- ATS checks
REQUIRED_SECTIONS = ["PROFESSIONAL SUMMARY", "SKILLS", "PROFESSIONAL EXPERIENCE", "EDUCATION"]

# A resume must read as one linear column. These validate the rendered PDF
# itself rather than the data that went into it, because layout is where ATS
# parsing actually breaks.
def check_pdf(pdf_bytes: bytes, profile: Any) -> list[dict[str, str]]:
    from pypdf import PdfReader
    import io as _io

    from .outreach import portfolio_host

    findings: list[dict[str, str]] = []
    reader = PdfReader(_io.BytesIO(pdf_bytes))
    text = "".join(p.extract_text() or "" for p in reader.pages)

    if len(text) < 800:
        findings.append({
            "severity": "high", "type": "ats_unreadable", "where": "document",
            "detail": f"Only {len(text)} characters extracted — an ATS would read almost nothing.",
            "fix": "Ensure text is real, not an image.",
        })

    # Sections must appear, and in the expected reading order.
    positions = [(s, text.find(s)) for s in REQUIRED_SECTIONS]
    for name, pos in positions:
        if pos == -1:
            findings.append({
                "severity": "high", "type": "ats_missing_section", "where": name,
                "detail": f"'{name}' heading not found in the extracted text.",
                "fix": "Section headings must be plain text an ATS can match.",
            })
    found = [p for _, p in positions if p != -1]
    if found != sorted(found):
        findings.append({
            "severity": "high", "type": "ats_section_order", "where": "document",
            "detail": "Sections extract out of order, which usually means content is overlapping.",
            "fix": "Check column widths — overlapping rows scramble parse order.",
        })

    # Contact details must survive extraction or the application is unreachable.
    for label, value in (("email", profile.email), ("phone", profile.phone)):
        token = (value or "").replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        flat = text.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        if token and token not in flat:
            findings.append({
                "severity": "high", "type": "ats_missing_contact", "where": label,
                "detail": f"{label} did not survive text extraction.",
                "fix": "Contact details must be selectable text in the header.",
            })

    # A portfolio link that does not survive extraction is worse than no link:
    # the header still spends a line on it, and the reader still cannot click.
    # Separate from the check above because a mangled portfolio is a lost
    # opportunity, not an unreachable candidate.
    site = portfolio_host(getattr(profile, "portfolio_url", ""))
    if site and site.replace(" ", "") not in text.replace(" ", ""):
        findings.append({
            "severity": "medium", "type": "portfolio_link_broken", "where": "header",
            "detail": f"{site} did not survive text extraction.",
            "fix": "The portfolio host must be selectable text, not part of an image.",
        })

    if len(reader.pages) > 2:
        findings.append({
            "severity": "medium", "type": "length", "where": "document",
            "detail": f"{len(reader.pages)} pages.",
            "fix": "Two pages is the practical ceiling at this experience level.",
        })

    return findings
