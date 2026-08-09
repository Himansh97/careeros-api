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
