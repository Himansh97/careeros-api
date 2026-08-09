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
        if gap >= 4:
            findings.append({
                "severity": "high" if gap >= 12 else "medium",
                "type": "timeline_gap",
                "where": f"{prev.get('employer')} → {nxt.get('employer')}",
                "detail": (
                    f"{gap} month gap between {prev.get('end_date')} and "
                    f"{nxt.get('start_date')}. A recruiter will ask about it."
                ),
                "fix": (
                    "If a degree covers this period, show the study dates in "
                    "EDUCATION rather than only a graduation date."
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

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return findings
