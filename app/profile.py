"""Loads the candidate's real profile and evidence from the careeros repo.

Nothing here invents data. If a file is missing, the API says so rather than
substituting defaults — the whole system depends on claims being traceable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from .config import CAREEROS_DIR


class ProfileNotFound(Exception):
    """Raised when the candidate data files aren't present on disk."""


@dataclass
class EvidenceClaim:
    claim_id: str
    employer: str
    claim: str
    skills: list[str]
    industry: str
    date_range: str
    classification: str
    approved_for_resume: bool
    source: str
    # Named deliverable this claim belongs to. Claims carrying one are grouped
    # under PROJECTS rather than listed as employment bullets, so a major piece
    # of work reads as a shipped thing with its own scope instead of dissolving
    # into a list of duties. Empty means it stays an employment bullet.
    project: str = ""

    # ── structure the resume writer needs, added for the rewriting work ──
    #
    # A claim was a 200-character sentence, a handful of skill tags and one
    # industry string. That is enough to *select* a bullet and not enough to
    # *rewrite* one: nothing recorded which number measured what, how big the
    # work was, or what authority the candidate actually held. Every field
    # here exists to make a specific fabrication detectable.

    # Each figure bound to what it measured, so moving a number onto a
    # different noun is visible. `of` is the thing measured, not a label.
    #   [{"value": "40%", "unit": "percent", "of": "reporting cycle time"}]
    metrics: list[dict[str, str]] = field(default_factory=list)

    # Size, as facts rather than adjectives. "Senior" is a claim; "4 analysts,
    # 20 markets, 1M records" is a measurement.
    scope: dict[str, Any] = field(default_factory=dict)

    # The true authority level, from the ladder the containment check uses:
    # contributed < supported < built < led < owned. This is the ceiling a
    # rewrite may not exceed, which is the only defence against a model
    # turning "supported" into "drove" using words already in the sentence.
    seniority_verb: str = ""

    # Which role families this claim is evidence for. Lets a rewrite know
    # whether it is writing for an analytics engineer or a business analyst.
    role_family: list[str] = field(default_factory=list)

    # One line: what a hiring manager learns from this. The thing the bullet
    # is *for*, which is what a rewrite must preserve even as the words change.
    proves: str = ""

    @property
    def skill_tokens(self) -> set[str]:
        """Lowercased skill tokens, plus tokens mined from the claim text.

        The claim text matters: evidence lists "Python" as a skill, but the
        sentence itself may be the only place "PySpark" or "Airflow" appears.
        """
        tokens: set[str] = set()
        for skill in self.skills:
            tokens.add(skill.lower().strip())
        for word in self.claim.lower().replace("/", " ").replace(",", " ").split():
            cleaned = word.strip(".,;:()").strip()
            if len(cleaned) > 2:
                tokens.add(cleaned)
        return tokens


@dataclass
class CandidateProfile:
    name: str
    email: str
    phone: str
    location: str
    linkedin_url: str
    work_authorization: str
    education: list[dict[str, Any]]
    certifications: list[str]
    skills_inventory: dict[str, list[str]]
    employment_history: list[dict[str, Any]]
    headline: str = ""
    portfolio_url: str = ""
    # Where the candidate may work, keyed by ISO country code. Defaults to
    # empty, which the eligibility gate reads as "no recorded right" and
    # therefore blocks -- the safe direction. `work_authorization` above stays
    # as the US-shaped summary the resume and application answers use.
    work_rights: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Spoken languages and proficiency. Empty by default, and only ever
    # populated from a stated fact -- citizenship implies no language.
    languages: list[dict[str, str]] = field(default_factory=list)
    # "Immediate", "30 days", and so on. Gulf CVs are expected to state it.
    availability: str = ""
    professional_summary: str = ""
    credentials_line: list[str] = field(default_factory=list)
    evidence: list[EvidenceClaim] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)
    application_answers: dict[str, Any] = field(default_factory=dict)

    @property
    def all_skills(self) -> set[str]:
        out: set[str] = set()
        for group in self.skills_inventory.values():
            for skill in group:
                out.add(skill.lower().strip())
        for claim in self.evidence:
            out |= claim.skill_tokens
        return out


def _read_json(name: str) -> Any:
    path = CAREEROS_DIR / name
    if not path.exists():
        raise ProfileNotFound(f"{path} not found")
    return json.loads(path.read_text())


def _read_yaml(name: str) -> dict[str, Any]:
    path = CAREEROS_DIR / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


@lru_cache(maxsize=1)
def load_profile() -> CandidateProfile:
    raw = _read_json("candidate_master_profile.json")
    evidence_raw = _read_json("career_evidence.json")

    claims = [
        EvidenceClaim(
            claim_id=c["claim_id"],
            employer=c["employer_or_project"],
            claim=c["claim"],
            skills=c.get("skills", []),
            industry=c.get("industry", ""),
            date_range=c.get("date_range", ""),
            classification=c.get("classification", "UNKNOWN"),
            approved_for_resume=bool(c.get("approved_for_resume", False)),
            source=c.get("evidence_source", "career_evidence.json"),
            project=c.get("project", ""),
            # Absent on every claim until the backfill runs, and absent
            # forever on any claim the candidate has not confirmed. A default
            # of empty is load-bearing: a rewrite that finds no recorded
            # seniority_verb must refuse to raise the verb rather than assume
            # a ceiling.
            metrics=c.get("metrics", []),
            scope=c.get("scope", {}),
            seniority_verb=c.get("seniority_verb", ""),
            role_family=c.get("role_family", []),
            proves=c.get("proves", ""),
        )
        for c in evidence_raw.get("claims", [])
    ]

    return CandidateProfile(
        name=raw.get("name", ""),
        email=raw.get("email", ""),
        phone=raw.get("phone", ""),
        location=raw.get("location", ""),
        linkedin_url=raw.get("linkedin_url", ""),
        work_authorization=raw.get("work_authorization", ""),
        work_rights={
            k: v for k, v in (raw.get("work_rights") or {}).items()
            if not k.startswith("_") and isinstance(v, dict)
        },
        languages=list((raw.get("languages") or {}).get("entries") or []),
        availability=(raw.get("availability") or ""),
        portfolio_url=raw.get("portfolio_url", ""),
        education=raw.get("education", []),
        certifications=raw.get("certifications", []),
        skills_inventory=raw.get("current_skills_inventory", {}),
        employment_history=raw.get("employment_history", []),
        headline=raw.get("headline", ""),
        professional_summary=raw.get("professional_summary", ""),
        credentials_line=raw.get("credentials_line", []),
        evidence=claims,
        preferences=_read_yaml("job_preferences.yaml"),
        application_answers=_read_yaml("application_answers.yaml"),
    )
