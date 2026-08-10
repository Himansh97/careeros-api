"""Resume tailoring by selecting and ranking real evidence.

Tailoring here means: choose which verified accomplishments to lead with, and
in what order, based on what a specific job asks for. It never rewrites a
claim into something stronger than the source, and never adds a claim that
isn't in career_evidence.json. Every bullet carries its source.
"""
from __future__ import annotations

import re
from typing import Any

from .profile import CandidateProfile, EvidenceClaim
from .scoring import _contains
from .skills import SKILL_ALIASES


def _requirement_weights(
    wanted: list[str], score: dict[str, Any], profile: CandidateProfile
) -> dict[str, float]:
    """Weight each wanted requirement by how much it discriminates.

    Two factors, both about how much a match is worth telling an employer:

    * Importance — a required requirement outranks a preferred one.
    * Rarity in the candidate's own evidence — a requirement a dozen claims
      could satisfy says little about which bullet to lead with, whereas one
      only two claims can satisfy is exactly the bullet worth the space.
    """
    importance = {
        r["label"]: (2.0 if r["importance"] == "required" else 1.0)
        for r in score.get("requirements", [])
    }

    # Rarity within the candidate's own evidence was tried here and is wrong:
    # it made a requirement *less* valuable the more evidence backed it, so
    # importing fifteen genuine LOS claims demoted every LOS bullet. What a
    # bullet is worth depends on the posting, not on how much the candidate
    # has written down. Importance alone; breadth is handled by selection.
    return {want: importance.get(want, 1.0) for want in wanted}


def _relevance(
    claim: EvidenceClaim,
    wanted: list[str],
    weights: dict[str, float] | None = None,
) -> tuple[int, list[str]]:
    """How many of the job's requirements this claim demonstrably supports.

    Requirements are matched through the same alias set the extractor used to
    find them in the posting. Matching only the canonical label was asymmetric:
    the extractor would recognise "data validation" in a JD and record the
    requirement as "Data quality", then fail to see that same phrase in the
    evidence. That silently hid the most job-specific claims — the MISMO schema
    validation, which declares "data validation", scored zero relevance on a
    mortgage compliance posting and was dropped from the resume entirely.

    The claim's industry counts too, so domain experience outranks generic
    evidence on a domain-specific posting.
    """
    haystack = (
        claim.claim + " " + " ".join(claim.skills) + " " + claim.industry
    ).lower()
    hits = []
    for want in wanted:
        terms = [want, *SKILL_ALIASES.get(want, [])]
        if any(_contains(haystack, t.lower()) for t in terms):
            hits.append(want)

    if weights is None:
        return len(hits), hits

    # Counting every matched requirement equally let common skills outvote
    # rare ones. On a mortgage posting, a generic requirements-gathering
    # bullet matching four widely-supported skills beat the Encompass/LOS
    # bullet matching two — even though the LOS evidence is the only thing on
    # the page a mortgage employer cannot find in any other candidate. Scores
    # are scaled by 100 so the result stays an integer for sorting.
    score = sum(weights.get(h, 1.0) for h in hits)
    return int(round(score * 100)), hits


MONTHS = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _pretty_date(token: str) -> str:
    """Turn '2022-11' into 'Nov 2022'; pass through 'Present' unchanged."""
    token = (token or "").strip()
    if "-" in token:
        parts = token.split("-")
        if len(parts) >= 2 and parts[1] in MONTHS:
            return f"{MONTHS[parts[1]]} {parts[0]}"
    return token


def _pretty_range(date_range: str) -> str:
    for sep in (" to ", " – ", " - "):
        if sep in date_range:
            a, b = date_range.split(sep, 1)
            return f"{_pretty_date(a)} – {_pretty_date(b)}"
    return date_range


def _employment_lookup(profile: CandidateProfile) -> dict[str, dict[str, Any]]:
    """Map employer name to its role title and dates for section headings."""
    out: dict[str, dict[str, Any]] = {}
    for role in profile.employment_history or []:
        out[role.get("employer", "")] = role
    return out


def _sort_key(role: dict[str, Any] | None) -> str:
    """Sort key for reverse-chronological ordering; 'Present' sorts newest."""
    if not role:
        return "0000-00"
    end = (role.get("end_date") or "").strip()
    if end.lower() == "present":
        return "9999-99"
    return end or role.get("start_date", "0000-00")


def tailor_resume(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> dict[str, Any]:
    wanted = score["strongMatches"] + score["partialMatches"]
    weights = _requirement_weights(wanted, score, profile)
    employment = _employment_lookup(profile)

    ranked: list[tuple[int, list[str], EvidenceClaim]] = []
    coursework: list[dict[str, Any]] = []
    project_claims: list[tuple[int, list[str], EvidenceClaim]] = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        n, hits = _relevance(claim, wanted, weights)
        # Named deliverables get their own section. Listing them as employment
        # bullets buried a shipped platform among routine duties, and it also
        # meant one role's bullet cap decided how much of the candidate's most
        # substantial work an employer ever saw.
        if claim.project:
            project_claims.append((n, hits, claim))
            continue
        # Coursework under a job heading reads as padding — a recruiter sees
        # "in coursework" beside a job title and discounts the whole role. The
        # claim is real, so it moves to EDUCATION rather than being dropped,
        # and only when this posting actually calls for it.
        if claim.classification == "LEARNED_OR_ACADEMIC":
            if n:
                coursework.append(
                    {"employer": claim.employer, "text": claim.claim, "id": claim.claim_id}
                )
            continue
        ranked.append((n, hits, claim))
    # Relevance decides which bullets lead *within* a role, but never the order
    # of the roles themselves — a resume must read reverse-chronologically.
    ranked.sort(key=lambda t: -t[0])

    sections: list[dict[str, Any]] = []
    seen_employers: list[str] = []
    for n, hits, claim in ranked:
        if claim.employer not in seen_employers:
            seen_employers.append(claim.employer)
            role = employment.get(claim.employer, {})
            title = role.get("title", "")
            heading = f"{title} — {claim.employer}" if title else claim.employer
            location = role.get("location", "")
            sections.append(
                {
                    "id": claim.employer.lower().replace(" ", "-")[:40],
                    "heading": heading,
                    "employer": claim.employer,
                    "subheading": " · ".join(
                        p for p in [_pretty_range(claim.date_range), location] if p
                    ),
                    "bullets": [],
                }
            )
        section = next(s for s in sections if s["employer"] == claim.employer)
        section["bullets"].append(
            {
                "id": claim.claim_id,
                "text": claim.claim,
                # How many of this posting's requirements the bullet supports.
                # Selection needs the count, not just whether it's non-zero:
                # ranking on a boolean made every relevant bullet tie, so
                # section order decided, and a bullet answering three of the
                # JD's requirements lost to one answering a single requirement
                # purely because its role came later on the page.
                "relevance": n,
                "hits": hits,
                "changeType": "reordered" if n > 0 else "unchanged",
                "whyChanged": (
                    f"Prioritized — directly supports {', '.join(hits[:4])}."
                    if hits
                    else None
                ),
                "evidence": {
                    "source": f"{claim.source} — {claim.claim_id}",
                    "verifiedStatement": claim.claim,
                    "usedToSupport": ", ".join(hits) if hits else "General background",
                },
            }
        )

    # Reverse-chronological, newest role first. Non-negotiable on a resume;
    # relevance ordering earlier had a 2022 role appearing above a current one.
    sections.sort(key=lambda s: _sort_key(employment.get(s["employer"])), reverse=True)

    # Select rather than include everything. Every resume previously carried
    # every claim, so bullet ORDER was the only thing that varied — and for two
    # similar postings the order is identical, which is why two analyst
    # applications came out 99.5% the same document. Dropping the claims a
    # given posting has no use for is also just better resume practice.
    _order_by_coverage(sections, weights)
    _select_bullets(sections, weights=weights)

    projects = _build_projects(
        project_claims, weights, job.get("description", "")
    )

    # Align wording to the posting's vocabulary before auditing, so the
    # keyword-alignment score reflects what the employer will actually read.
    from .phrasing import align_resume

    align_resume({"sections": sections}, job.get("description", ""))

    # Hand-tailored wording for this specific posting wins over the rule-based
    # layer. The rule table can only swap verified synonyms in place; it cannot
    # restructure a sentence to lead with what the posting actually asks for.
    # Overrides are factually contained by the claim they replace — see
    # overrides.verify_override — and carry the original for audit.
    from .overrides import apply_overrides, get_document_edits

    overridden = apply_overrides(job["id"], sections)

    resume_score, audit = _audit(job, score, sections)

    # An edited summary or headline replaces the generated one. Edits are
    # applied last so nothing downstream can regenerate over them.
    edits = get_document_edits(job["id"])

    return {
        "jobId": job["id"],
        "summary": edits.get("summary") or _summary(job, score, profile),
        "headline": edits.get("headline") or _headline(job, profile),
        "editedFields": sorted(edits.keys()),
        "matchedSkills": score["strongMatches"] + score["partialMatches"],
        "jobTitle": job["title"],
        "companyName": job["company"]["name"],
        "version": 1,
        "status": "ready" if resume_score >= 90 else "draft",
        "rawFitScore": score["rawFitScore"],
        "resumeScore": resume_score,
        "scoreHistory": [resume_score],
        "sections": sections,
        "coursework": coursework,
        "projects": projects,
        "handTailoredBullets": overridden,
        "audit": audit,
        "updatedAt": None,
    }


def _project_affinity(claims: list[EvidenceClaim], description: str) -> int:
    """How much of a project's own vocabulary the posting actually uses.

    Requirement labels alone could not rank projects sensibly: extraction only
    names what the vocabulary recognises, so on a mortgage-compliance posting
    SettleDesk (Ginnie Mae, MISMO, post-closing) tied with an unrelated CI/CD
    platform — the JD never says "Ginnie Mae", so nothing distinguished them.
    Comparing against the posting's raw text restores that signal without
    inventing a requirement that was never stated.
    """
    text = (description or "").lower()
    if not text:
        return 0
    tokens: set[str] = set()
    for claim in claims:
        for skill in claim.skills:
            token = skill.lower().strip()
            if len(token) > 2:
                tokens.add(token)
    return sum(1 for t in tokens if _contains(text, t))


def _build_projects(
    project_claims: list[tuple[int, list[str], EvidenceClaim]],
    weights: dict[str, float],
    description: str = "",
    max_projects: int = 3,
    max_bullets: int = 3,
) -> list[dict[str, Any]]:
    """Group project-tagged claims into ranked, deduplicated project entries.

    Projects are ordered by how much of THIS posting they speak to, and their
    bullets are chosen by the same marginal-coverage rule used for employment,
    so a project earns its space by answering something the page has not
    already answered.
    """
    grouped: dict[str, list[tuple[int, list[str], EvidenceClaim]]] = {}
    for n, hits, claim in project_claims:
        grouped.setdefault(claim.project, []).append((n, hits, claim))

    entries: list[dict[str, Any]] = []
    for name, items in grouped.items():
        items.sort(key=lambda t: -t[0])
        entries.append(
            {
                "id": name.lower().replace(" ", "-")[:48],
                "name": name,
                # Requirement hits, plus how much of the project's own
                # vocabulary this posting actually uses.
                "relevance": sum(n for n, _, _ in items)
                + _project_affinity([c for _, _, c in items], description) * 300,
                "bullets": [
                    {
                        "id": c.claim_id,
                        "text": c.claim,
                        "relevance": n,
                        "hits": hits,
                        "changeType": "reordered" if n else "unchanged",
                        "whyChanged": (
                            f"Prioritized — directly supports {', '.join(hits[:4])}."
                            if hits
                            else None
                        ),
                        "evidence": {
                            "source": f"{c.source} — {c.claim_id}",
                            "verifiedStatement": c.claim,
                            "usedToSupport": ", ".join(hits) if hits else "General background",
                        },
                    }
                    for n, hits, c in items
                ],
            }
        )

    entries.sort(key=lambda e: -e["relevance"])
    entries = entries[:max_projects]

    # Same marginal-gain rule as employment: within each project keep the
    # bullets that add something new, not the ones that repeat a strong suit.
    covered: set[str] = set()
    for entry in entries:
        remaining = list(entry["bullets"])
        chosen: list[dict[str, Any]] = []
        while remaining and len(chosen) < max_bullets:
            best = max(
                remaining,
                key=lambda b: (
                    sum(weights.get(h, 1.0) for h in b["hits"] if h not in covered),
                    b["relevance"],
                ),
            )
            remaining.remove(best)
            chosen.append(best)
            covered.update(best["hits"])
        entry["bullets"] = chosen
    return entries


def _order_by_coverage(
    sections: list[dict[str, Any]], weights: dict[str, float]
) -> None:
    """Reorder bullets within each role by what they ADD, not what they score.

    Selection guarantees the first N bullets of every role, so ordering decides
    what is guaranteed. Pure relevance order put the two highest individual
    scorers first, and on a mortgage posting both answered "requirements
    gathering" — the second added almost nothing, while the Encompass/LOS and
    MISMO evidence that speaks to the posting's actual subject never made the
    page. Coverage is tracked across roles in page order, so a requirement
    already answered above is worth less further down.
    """
    covered: set[str] = set()
    for section in sections:
        remaining = list(section["bullets"])
        ordered: list[dict[str, Any]] = []
        while remaining:
            best = max(
                remaining,
                key=lambda b: (
                    sum(
                        weights.get(h, 1.0)
                        for h in b.get("hits", [])
                        if h not in covered
                    ),
                    b.get("relevance", 0),
                ),
            )
            remaining.remove(best)
            ordered.append(best)
            covered.update(best.get("hits", []))
        section["bullets"] = ordered


def _select_bullets(
    sections: list[dict[str, Any]],
    budget: int = 16,
    floor: int = 3,
    cap_per_role: int = 4,
    weights: dict[str, float] | None = None,
) -> None:
    """Keep the most relevant bullets, kept evenly spread across roles.

    A floor of one bullet per role let relevance take everything else, so a
    posting matching the current job heavily produced 5/1/1/2 — the older roles
    read as filler and the resume looked lopsided. A floor of two keeps every
    position substantiated, and the leftover budget still goes to whichever
    bullets support this specific posting, so tailoring survives.

    Bullets arrive relevance-ordered, so within a role this keeps the ones that
    matter most for this job and drops the least relevant first.
    """
    if not sections:
        return

    # A flat floor across every role ate almost the whole budget: four roles at
    # two bullets each guaranteed 8 of 9, leaving one slot for relevance, so a
    # posting could barely influence the page. Recent roles now hold the floor
    # and older ones drop to a single bullet — ordinary resume practice, and it
    # frees enough budget for tailoring to actually show.
    def floor_for(index: int) -> int:
        return floor if index < 2 else max(1, floor - 1)

    # Never promise a role more bullets than it actually has evidence for.
    guaranteed = sum(
        min(floor_for(i), len(s["bullets"])) for i, s in enumerate(sections)
    )
    remaining = max(budget - guaranteed, 0)

    rest: list[tuple[int, dict[str, Any]]] = []
    for order, section in enumerate(sections):
        for bullet in section["bullets"][floor_for(order):]:
            rest.append((order, bullet))

    # Fill the discretionary slots by requirement COVERAGE, not by per-bullet
    # score. Ranking each bullet independently kept picking near-duplicates:
    # the two highest scorers both answered "requirements gathering", so the
    # second one bought almost nothing while Encompass/LOS and MISMO evidence
    # — the only bullets speaking to this posting's actual subject — were left
    # off. Greedy marginal gain asks what a bullet ADDS to what is already on
    # the page, so breadth across the JD wins over repeating a strong suit.
    weights = weights or {}
    covered: set[str] = set()
    for order, section in enumerate(sections):
        for bullet in section["bullets"][: floor_for(order)]:
            covered.update(bullet.get("hits", []))

    def marginal_gain(bullet: dict[str, Any]) -> float:
        return sum(
            weights.get(h, 1.0) for h in bullet.get("hits", []) if h not in covered
        )

    keep: set[int] = set()
    taken: dict[int, int] = {}
    # No role may take the whole discretionary budget. Relevance counts industry,
    # so every claim at the current employer scores well on a same-industry
    # posting and one role swept all the spare slots, producing the lopsided
    # shape the floor exists to prevent.
    while len(keep) < remaining:
        available = [
            (order, b)
            for order, b in rest
            if id(b) not in keep and taken.get(order, 0) < cap_per_role
        ]
        if not available:
            break
        # Highest marginal gain wins; ties fall back to page order so the
        # choice stays stable and reverse-chronological.
        order, bullet = max(
            available, key=lambda pair: (marginal_gain(pair[1]), -pair[0])
        )
        taken[order] = taken.get(order, 0) + 1
        keep.add(id(bullet))
        covered.update(bullet.get("hits", []))

    for order, section in enumerate(sections):
        limit = floor_for(order)
        section["bullets"] = [
            b for i, b in enumerate(section["bullets"]) if i < limit or id(b) in keep
        ]


def _headline(job: dict[str, Any], profile: CandidateProfile) -> str:
    """Position the candidate against this posting's role family.

    A headline states what the candidate is aiming at, not a title they have
    held, so aligning it to the posting is positioning rather than a claim.
    It only ever narrows to a family the candidate has genuine evidence in;
    anything unrecognised falls back to their own headline.
    """
    title = (job.get("title") or "").lower()
    families = [
        (("business intelligence", "bi developer", "bi analyst"), "Business Intelligence Analyst"),
        (("data engineer", "analytics engineer", "etl"), "Analytics Engineer"),
        (("business analyst", "operations analyst"), "Business Analyst"),
        (("data analyst", "reporting analyst"), "Data Analyst"),
        (("project manager", "program manager", "delivery"), "Analytics Delivery Manager"),
        (("machine learning", "ai engineer", "ml engineer"), "AI/ML Analyst"),
    ]
    for needles, label in families:
        if any(n in title for n in needles):
            return label
    return profile.headline or "Business Analytics Consultant"


def _summary(
    job: dict[str, Any], score: dict[str, Any], profile: CandidateProfile
) -> str:
    """A three-sentence summary, re-framed per posting from verified facts.

    The structure is fixed because it is the one the candidate wrote and
    wants on every resume:

      1. positioning — role family, tenure, delivery domains, credentials
      2. hands-on expertise — the concrete tools and practices
      3. translation — turning data and requirements into decisions

    Sentences 1 and 2 are tailored: the domains in (1) are ordered by what
    the posting emphasises, and the skills in (2) are the ones this job's
    scoring already matched to real evidence. Sentence 3 is stable, because
    it describes how the candidate works rather than what a given employer
    asked for. Nothing is introduced that the profile does not already state.
    """
    base = (profile.professional_summary or "").strip()
    matched = score.get("strongMatches") or []
    if not base:
        return base
    if not matched:
        return base

    # Skill labels arrive title-cased for display, which reads wrong inside a
    # sentence — "direct experience in SQL, Data quality, Power BI". The skills
    # inventory is the authority on how each tool is spelled, so prefer its
    # spelling; anything it doesn't name is a generic practice ("data quality")
    # and gets lowercased. Guessing from capitalisation alone is not enough:
    # it lowercased "Python", which has no capital past the first character.
    canonical = {
        s.lower(): s
        for group in (profile.skills_inventory or {}).values()
        for s in group
    }

    def in_sentence(skill: str) -> str:
        known = canonical.get(skill.lower())
        if known:
            return known
        if any(c.isupper() for c in skill[1:]):
            return skill
        return skill[:1].lower() + skill[1:]

    role_family = _headline(job, profile)
    text = (job.get("description") or "").lower() + " " + (job.get("title") or "").lower()

    degrees = [e.get("degree", "").lower() for e in (profile.education or [])]
    credential = "an MBA and MS in Business Analytics" if any(
        "business analytics" in d for d in degrees
    ) else "an MBA"

    # --- sentence 1: which delivery domains to lead with -------------------
    # Each domain is backed by real evidence, so the choice here is ordering
    # and emphasis, never invention. A posting that never mentions AI should
    # not have the summary open on it.
    domains = _delivery_domains(text)
    domain_phrase = _join(domains)

    positioning = (
        f"{role_family} with 3+ years of experience delivering end-to-end "
        f"{domain_phrase}, backed by {credential}."
    )

    # --- sentence 2: the hands-on skills this posting actually asked for ---
    # Drawn from the scored matches, so every item is evidence-backed for
    # this specific job. Falls back to the profile's own list if scoring
    # surfaced too few to make a credible sentence.
    skills = [in_sentence(s) for s in matched[:7]]
    if len(skills) < 3:
        skills = ["Python", "SQL", "PySpark", "data pipelines", "data quality"]
    expertise = (
        f"Hands-on expertise in {_join(skills)}, with experience building and "
        f"deploying production analytics solutions."
    )

    # --- sentence 3: stable closing, taken from the candidate's own text ---
    translation = (
        "Skilled at translating complex data and business requirements into "
        "actionable insights, scalable solutions, and data-driven recommendations "
        "for cross-functional stakeholders and executive leadership."
    )

    return " ".join([positioning, expertise, translation])


# Delivery domains the evidence file supports. Order within the tuple is the
# default; a posting reorders it by pulling its own emphasis to the front.
_DOMAIN_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("analytics", ("analytics", "analysis", "reporting", "dashboard", "insight")),
    ("data engineering", ("pipeline", "etl", "warehouse", "ingestion", "data engineering")),
    ("AI solutions", ("machine learning", " ai ", "artificial intelligence", "llm", "model")),
]


def _delivery_domains(text: str) -> list[str]:
    """Order the three verified delivery domains by this posting's emphasis.

    All three are always present — each is backed by evidence and dropping one
    would understate real experience. Only the order changes, so the sentence
    opens on what the employer cares about most.
    """
    scored = []
    for idx, (label, signals) in enumerate(_DOMAIN_SIGNALS):
        hits = sum(text.count(sig) for sig in signals)
        scored.append((-hits, idx, label))
    scored.sort()
    return [label for _, _, label in scored]


def _join(items: list[str]) -> str:
    """Oxford-comma join: 'a', 'a and b', 'a, b, and c'."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _readability(sections: list[dict[str, Any]]) -> float:
    """Penalise bullets a recruiter can't skim, not resume length."""
    penalty = 0.0
    for section in sections:
        if len(section["bullets"]) > 6:
            penalty += 0.1 * (len(section["bullets"]) - 6)
    over_long = sum(
        1 for s in sections for b in s["bullets"] if len(b["text"].split()) > 42
    )
    penalty += 0.05 * over_long
    return max(0.0, 1.0 - penalty)


def _audit(
    job: dict[str, Any], score: dict[str, Any], sections: list[dict[str, Any]]
) -> tuple[int, dict[str, Any]]:
    reqs = score["requirements"]
    required = [r for r in reqs if r["importance"] == "required"]
    exact = [r for r in reqs if r["match"] == "exact"]
    gaps = [r for r in reqs if r["match"] == "gap"]
    missing_required = [r for r in required if r["match"] == "gap"]

    bullets = sum(len(s["bullets"]) for s in sections)

    def pct(n: float, d: float) -> float:
        return (n / d) if d else 1.0

    # A partial match is real evidence held to a lower standard, not an absence.
    # scoring.py already credits it 0.5 when computing fit; the audit counted
    # only exact matches, so the same resume scored strictly worse here than the
    # fit score said it should. Weighting them identically removes that split.
    def covered(rs: list[dict[str, Any]]) -> float:
        return sum(1.0 if r["match"] == "exact" else 0.5 if r["match"] == "partial" else 0.0
                   for r in rs)

    # Achievements was pinned at 0.9 regardless of content, so it measured
    # nothing. Score what a recruiter actually looks for: bullets carrying a
    # quantified outcome.
    quantified = sum(
        1 for s in sections for b in s["bullets"]
        if re.search(r"\d+\s*%|\b\d[\d,]{2,}\+?\b|\b\d+\+?\s+(?:markets|accounts|records)", b["text"])
    )

    categories = [
        ("requirement_coverage", "Requirement coverage", 25, pct(covered(reqs), max(len(reqs), 1))),
        ("relevant_experience", "Relevant experience", 20, pct(bullets, 6)),
        ("technical_skills", "Technical skills", 15, pct(covered(required), max(len(required), 1))),
        ("achievements", "Achievements", 10, pct(quantified, max(bullets * 0.5, 1))),
        # Total bullet count doesn't hurt readability — 13 bullets across four
        # roles reads fine. What hurts is a wall of bullets under one role, or
        # bullets too long to skim. Measure those instead of the total.
        ("readability", "Readability", 10, _readability(sections)),
        ("ats_structure", "ATS structure", 10, 1.0),
        ("keyword_alignment", "Keyword alignment", 5, pct(covered(reqs), max(len(reqs), 1))),
        ("education", "Education", 5, 1.0),
    ]

    scored = []
    total = 0
    for key, label, mx, ratio in categories:
        pts = int(round(min(1.0, max(0.0, ratio)) * mx))
        total += pts
        scored.append({"key": key, "label": label, "score": pts, "max": mx})

    decision = "SHORTLIST" if total >= 90 else "REVIEW" if total >= 75 else "REJECT"

    works = []
    if exact:
        works.append(
            f"{len(exact)} requirement(s) backed by sourced, verifiable accomplishments."
        )
    if bullets:
        works.append(f"{bullets} bullet(s) selected, each traceable to career evidence.")
    if not missing_required:
        works.append("Every required skill found in the posting has supporting evidence.")

    concerns = []
    if missing_required:
        concerns.append(
            "Required but unevidenced: "
            + ", ".join(r["label"] for r in missing_required[:5])
            + ". Reported as gaps rather than implied."
        )
    if gaps and not missing_required:
        concerns.append(
            "Preferred-only gaps: " + ", ".join(r["label"] for r in gaps[:5]) + "."
        )
    if not concerns:
        concerns.append("No unbacked claims detected.")

    return total, {
        "overall": total,
        "decision": decision,
        "categories": scored,
        "whatWorks": works or ["Resume assembled from verified evidence only."],
        "concerns": concerns,
    }
