"""Resume tailoring by selecting and ranking real evidence.

Tailoring here means: choose which verified accomplishments to lead with, and
in what order, based on what a specific job asks for. It never rewrites a
claim into something stronger than the source, and never adds a claim that
isn't in career_evidence.json. Every bullet carries its source.
"""
from __future__ import annotations

import re
from typing import Any

from .config import READY_SCORE
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
    # Composed STAR bullets, where the evidence supports them. A recorded claim
    # is usually one action; the situation it addressed and the result it
    # produced are recorded as separate claims about the same work, so a bullet
    # that reads situation-action-result is those pieces put back together.
    # `star.load` verifies each one against the union of its sources through the
    # same containment check overrides go through, so a composition that drifted
    # is dropped rather than served.
    #
    # A role with no composed bullets falls through to the claim-level path
    # below unchanged, which is why this is additive rather than a replacement.
    from .star import select as star_select

    starred: set[str] = set()
    for section in sections:
        composed = star_select(
            section["employer"], profile, wanted, job.get("description", ""), limit=3
        )
        if not composed:
            continue
        starred.add(section["employer"])
        section["bullets"] = [
            {
                **bullet.as_dict(),
                "relevance": len(bullet.themes),
                "hits": [w for w in wanted if w.lower() in bullet.text.lower()],
                "changeType": "rewritten",
                "whyChanged": (
                    "Composed as situation, action and result from "
                    f"{', '.join(bullet.source_claims)}."
                ),
            }
            for bullet in composed
        ]

    # Sections already reduced to three composed bullets are left alone: the
    # three are chosen and ordered as one story, and re-trimming by coverage
    # would break the arc it exists to make.
    plain = [s for s in sections if s["employer"] not in starred]
    _order_by_coverage(sections, weights)
    _select_bullets(plain, weights=weights)

    # Resolved once and reused: the headline, the project ordering and the
    # response all describe the same decision, so they cannot disagree.
    from .resume_variant import resolve

    variant = resolve(job, profile)

    projects = _build_projects(
        project_claims, weights, job.get("description", ""), variant=variant
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

    # The summary is needed by the keyword measure, and is generated here rather
    # than below so the audit sees the text that will actually ship.
    generated_summary = _summary(job, score, profile)
    resume_score, audit = _audit(job, score, sections, profile, generated_summary)

    # An edited summary or headline replaces the generated one. Edits are
    # applied last so nothing downstream can regenerate over them.
    edits = get_document_edits(job["id"])

    return {
        "jobId": job["id"],
        "summary": edits.get("summary") or generated_summary,
        "headline": edits.get("headline") or variant.label or profile.headline
        or "Business Analytics Consultant",
        # The framing decision, carried so the candidate can see which argument
        # this document is making before it is sent, and why.
        "variant": variant.as_dict(),
        "editedFields": sorted(edits.keys()),
        "matchedSkills": score["strongMatches"] + score["partialMatches"],
        "jobTitle": job["title"],
        "companyName": job["company"]["name"],
        "version": 1,
        # Rebased with the scale. See TARGET_SCORE for the arithmetic: the
        # audit used to hand out roughly ten points nobody earned, so 90 on the
        # old scale is about 80 on this one. Leaving it at 90 would have marked
        # almost every resume in the pipeline "draft" overnight without a single
        # document getting worse.
        "status": "ready" if resume_score >= READY_SCORE else "draft",
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
    variant: Any = None,
) -> list[dict[str, Any]]:
    """Group project-tagged claims into ranked, deduplicated project entries.

    Projects are ordered by how much of THIS posting they speak to, and their
    bullets are chosen by the same marginal-coverage rule used for employment,
    so a project earns its space by answering something the page has not
    already answered.

    `variant` nudges that order toward the role family. The nudge is smaller
    than one requirement hit on purpose: what the posting actually says is
    better evidence of what this reader wants than the family label inferred
    from its title, so a project the posting names still outranks one the
    family merely prefers. It only decides ties and near-ties, which is
    exactly where a data engineering reader should see the signed ledger first
    and a product reader should see the decision layer first.
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
                + _project_affinity([c for _, _, c in items], description) * 300
                + _variant_preference(name, variant),
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


def _project_key(name: str) -> str:
    """The identifier part of a project name, before its description.

    Projects are recorded as "Custody — signed AI decision ledger", so an
    exact-equality match against "Custody" silently never fires. That is
    exactly what happened: the preference was wired, its unit test passed
    against the short name it assumed, and the ordering did not move on real
    data. Matching the leading identifier is what the caller means.
    """
    head = re.split(r"\s+[—–-]\s+", (name or "").strip(), maxsplit=1)[0]
    return head.strip().lower()


def _variant_preference(project_name: str, variant: Any) -> int:
    """A tie-breaker, deliberately worth less than a single requirement hit.

    `_project_affinity` scores each posting-vocabulary hit at 300. This tops
    out at 200, so a project the posting genuinely talks about can never be
    displaced by one the role family merely prefers. Ordering within
    `leads_with` is preserved so the first named project wins a tie against
    the second.
    """
    if variant is None or not getattr(variant, "leads_with", ()):
        return 0
    key = _project_key(project_name)
    for position, wanted in enumerate(variant.leads_with):
        if key == _project_key(wanted):
            return 200 - position * 50
    return 0


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
            if id(b) not in keep
            # The cap has to know about the floor. `cap_per_role=4` on top of a
            # floor of 3 gave the most recent role 7 bullets — one over the
            # point where `_readability` deducts, on every resume the system
            # produced.
            and taken.get(order, 0)
            < min(cap_per_role, MAX_BULLETS_PER_ROLE - floor_for(order))
        ]
        if not available:
            break
        # Highest marginal gain wins. Ties are common — once the JD is covered,
        # most remaining bullets add nothing — and used to fall straight to page
        # order. Prefer a bullet that states an outcome: same evidence, but the
        # concrete one earns its place on the page. Page order still breaks the
        # remaining ties, so selection stays stable and reverse-chronological.
        order, bullet = max(
            available,
            key=lambda pair: (
                marginal_gain(pair[1]),
                is_quantified(pair[1]["text"]),
                -pair[0],
            ),
        )
        taken[order] = taken.get(order, 0) + 1
        keep.add(id(bullet))
        covered.update(bullet.get("hits", []))

    for order, section in enumerate(sections):
        limit = floor_for(order)
        section["bullets"] = [
            b for i, b in enumerate(section["bullets"]) if i < limit or id(b) in keep
        ]


# Each family, the posting titles that select it, and the skills that have to
# be evidenced before the candidate may be positioned as one. The third element
# is the part that was missing: the docstring below has always promised it, and
# without it a posting's title alone decided how the candidate was described.
#
# Matching is by regex rather than exact phrase. The first version listed
# literal needles — "data engineer", "business analyst" — and real postings do
# not use them: "Sr Data Solution Engineer", "Senior Revenue Analytics
# Analyst", "FP&A Analyst - Data Insights" and "Senior Analyst - Business
# Analytics" all fell through to the generic fallback. Measured across the 38
# tracked applications, 13 of them were positioned as "Business Analytics
# Consultant" while carrying eight to twelve strong matches for the role.
#
# Order matters: the first pattern that matches wins, so the more specific
# families are listed before the general ones.
def _headline(job: dict[str, Any], profile: CandidateProfile) -> str:
    """Position the candidate against this posting's role family.

    A headline states what the candidate is aiming at, not a title they have
    held, so aligning it to the posting is positioning rather than a claim.
    It only ever narrows to a family the candidate has genuine evidence in;
    anything unrecognised falls back to their own headline.

    That last sentence was a promise this function did not keep. It matched the
    posting title and returned the label, with no reference to the evidence
    file at all — so a posting titled "ML Engineer" described the candidate as
    an "AI/ML Analyst" whether or not a single machine-learning claim existed.
    The headline is the first line a recruiter reads, which makes it the worst
    place in the document to assert something unbacked.

    A family now requires evidence for at least one of the skills that define
    it, resolved through the same `_find_evidence` cascade scoring uses, so
    aliases and inflections are handled identically. Falling back to the
    candidate's own headline is always safe: it is theirs, and it claims
    nothing about this posting.

    The family table moved to `resume_variant`, which resolves the same
    decision and keeps it. It was computed here and discarded, so project
    ordering and the API could not see what the headline had already worked
    out — and a second copy of the table would have been a second vocabulary
    to drift.
    """
    from .resume_variant import resolve

    variant = resolve(job, profile)
    return variant.label or profile.headline or "Business Analytics Consultant"


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

    # No year count. "3+ years" was hardcoded here and traced to nothing in
    # the evidence file, which is the shape non-negotiable #1 exists to stop.
    # It was also checkable and wrong from the resume's own dates: the two
    # non-intern roles total 26 months, so a reviewer who counts finds a number
    # smaller than the first line claims and reads the rest sceptically. The
    # dates are on the page; they can do the arithmetic without being told.
    positioning = (
        f"{role_family} delivering end-to-end {domain_phrase}, "
        f"backed by {credential}."
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


# A bullet carries a measurable outcome. The audit used
# `\d+\s*%|\b\d[\d,]{2,}\+?\b|\b\d+\+?\s+(?:markets|accounts|records)`, which
# demanded three digits unless the noun was one of three hardcoded words — so
# "processing 1M+ records", "20+ regional markets" and "six failed deployments
# and four distinct root causes" all read as unquantified. The achievements
# category came out at 7/10 on every single resume in the pipeline, which is
# the signature of a broken measure rather than a real weakness.
#
# This counts figures the resume already states. It does not add any.
_QUANTIFIED = re.compile(
    r"""
      \d+(?:\.\d+)?\s*%                          # 40%, 12.5 %
    | \$\s?\d                                    # $2M, $450
    | \b\d[\d,]*\s*[KMB]\b                       # 1M, 250K, 2 B
    | \b\d[\d,]{2,}\b                            # 1,200 / 5000
    | \b\d+\s*\+                                 # 20+, 15+
    | \b\d+\s*(?:hours?|days?|weeks?|months?|years?|minutes?|seconds?)\b
    | \b\d+\s*(?:x|times|fold)\b                 # 3x, 4 times
      # "six failed deployments", "four distinct root causes" — a spelled
      # number, up to two adjectives, then a plural noun. `(?!of\b)` keeps
      # "three of the regions" out, which is a reference and not a count.
    | \b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+
      (?!of\b)(?:[a-z-]+\s+){0,2}[a-z-]+s\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Version strings look like figures and are not achievements. "Encompass
# Developer Connect V3" and "Python 3" must never count toward Achievements.
_VERSIONISH = re.compile(r"\b[vV]\d+(?:\.\d+)*\b|\b[A-Za-z]+\s\d(?:\.\d+)?\b")


def is_quantified(text: str) -> bool:
    """Does this bullet state a measurable outcome?"""
    stripped = _VERSIONISH.sub(" ", text)
    return bool(_QUANTIFIED.search(stripped))


# Above this, a single role reads as a wall rather than a list, and
# `_readability` starts deducting. Kept as one constant so the selection cap
# and the penalty threshold cannot drift apart — they did, and every resume
# shipped one 7-bullet role and a standing -1.
MAX_BULLETS_PER_ROLE = 6


def _readability(sections: list[dict[str, Any]]) -> float:
    """Penalise bullets a recruiter can't skim, not resume length."""
    penalty = 0.0
    for section in sections:
        if len(section["bullets"]) > MAX_BULLETS_PER_ROLE:
            penalty += 0.1 * (len(section["bullets"]) - MAX_BULLETS_PER_ROLE)
    over_long = sum(
        1 for s in sections for b in s["bullets"] if len(b["text"].split()) > 42
    )
    penalty += 0.05 * over_long
    return max(0.0, 1.0 - penalty)


def _ats_structure(
    sections: list[dict[str, Any]], profile: CandidateProfile
) -> float:
    """Whether this document has the parts an ATS parser needs to find.

    Was a literal `1.0`. Ten points handed to every resume regardless of what
    it contained is not a measurement, and it meant a resume missing an entire
    section still scored full marks for structure — including resumes whose
    rendered bytes fail `resume_qa.check_pdf`'s real checks, which run later
    and only print.

    These are the content-level preconditions for the checks `check_pdf` makes
    on the rendered PDF: a section it cannot find is a section that was never
    written. Everything here is cheap and available at audit time.
    """
    checks = [
        bool(profile.email),
        bool(profile.phone),
        bool(profile.education),
        bool(profile.skills_inventory),
        bool(sections),
        # An employment section whose roles carry no bullets parses as a bare
        # list of company names.
        all(s.get("bullets") for s in sections) if sections else False,
        # A role without dates is the single most common parse failure: the
        # employment block collapses into one undated run. The range lives in
        # `subheading` (built in tailor_resume) — an earlier version of this
        # checked a `dates` key that sections have never had, so it scored a
        # permanent 9/10 and looked like a measurement while being a constant.
        all(s.get("subheading") for s in sections) if sections else False,
    ]
    return sum(1 for c in checks if c) / len(checks)


def _education_fit(job: dict[str, Any], profile: CandidateProfile) -> float:
    """Whether the candidate's education meets what this posting asks for.

    Was a literal `1.0`. Education is not a constant — a posting can ask for a
    doctorate, or ask for nothing at all, and those are different situations
    that were scored identically.

    A posting that states no requirement scores full: there is nothing to fail.
    A stated requirement is met by holding that level or higher.
    """
    if not profile.education:
        return 0.0

    text = (job.get("description") or "").lower()
    levels = {"phd": 3, "doctorate": 3, "master": 2, "mba": 2, "bachelor": 1, "associate": 0}

    wanted = 0
    for term, rank in levels.items():
        if term in text:
            wanted = max(wanted, rank)

    if not wanted:
        return 1.0  # nothing asked, nothing to miss

    held = 0
    for entry in profile.education:
        degree = str(entry.get("degree", "")).lower()
        for term, rank in levels.items():
            if term in degree:
                held = max(held, rank)

    if held >= wanted:
        return 1.0
    # One level short is a stretch, not a disqualification — and the candidate
    # may still be the right person. Two or more short is a real gap.
    return 0.5 if wanted - held == 1 else 0.0


def _keyword_alignment(
    score: dict[str, Any], sections: list[dict[str, Any]], summary: str
) -> float:
    """How much of the posting's own language the document actually uses.

    Was a duplicate of `requirement_coverage` — the identical expression, so
    five of the hundred points carried no independent information at all.

    This measures a genuinely different thing. `requirement_coverage` asks
    whether the candidate *has evidence* for a requirement. This asks whether
    the resume *says the word*. Those come apart constantly: a bullet about
    "consolidating fragmented records" is real evidence for "data integration"
    and never contains the phrase, so a keyword-ranking ATS scores it as absent
    and a human skimming for the term does not see it.

    Only requirements with real evidence behind them count here. Rewarding a
    document for naming something it cannot support would be rewarding keyword
    stuffing, which is the opposite of the intent — and is actively detected by
    modern screeners.
    """
    backed = [r for r in score.get("requirements", []) if r.get("match") in ("exact", "partial")]
    if not backed:
        return 1.0

    haystack = " ".join(
        [summary] + [b["text"] for s in sections for b in s.get("bullets", [])]
    ).lower()

    named = 0
    for req in backed:
        label = str(req.get("label", "")).lower()
        if not label:
            continue
        # Word-boundary, consistent with scoring._contains — substring matching
        # here would credit "R" against "reporting".
        if re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", haystack):
            named += 1
    return named / len(backed)


def _audit(
    job: dict[str, Any],
    score: dict[str, Any],
    sections: list[dict[str, Any]],
    profile: CandidateProfile,
    summary: str = "",
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
        1 for s in sections for b in s["bullets"] if is_quantified(b["text"])
    )

    # Bullets that support at least one requirement this posting actually asks
    # for. A bullet with no hits is not wrong — it may be strong general
    # evidence — but it is not doing work for *this* application.
    earning = sum(
        1 for s in sections for b in s["bullets"] if b.get("hits")
    )

    categories = [
        ("requirement_coverage", "Requirement coverage", 25, pct(covered(reqs), max(len(reqs), 1))),
        # Was pct(bullets, 6). The selection budget is 16 and the floors
        # guarantee at least 12, so this clamped to 1.0 on every resume ever
        # generated — twenty points that never moved. What matters is not how
        # many bullets are present but how many of them earn their place, so
        # measure the share that actually support a requirement this posting
        # asks for.
        ("relevant_experience", "Relevant experience", 20, pct(earning, max(bullets, 1))),
        ("technical_skills", "Technical skills", 15, pct(covered(required), max(len(required), 1))),
        # Was pct(quantified, bullets * 0.5) — satisfied whenever half the
        # bullets carried a figure, which every resume managed. A recruiter
        # reading the page does not grade on a curve against half of it.
        ("achievements", "Achievements", 10, pct(quantified, max(bullets, 1))),
        # Total bullet count doesn't hurt readability — 13 bullets across four
        # roles reads fine. What hurts is a wall of bullets under one role, or
        # bullets too long to skim. Measure those instead of the total.
        ("readability", "Readability", 10, _readability(sections)),
        # These three were 1.0, 1.0 and a copy of requirement_coverage — twenty
        # of the hundred points carrying no information, which is most of why
        # every resume in the pipeline scored 90+.
        ("ats_structure", "ATS structure", 10, _ats_structure(sections, profile)),
        ("keyword_alignment", "Keyword alignment", 5,
         _keyword_alignment(score, sections, summary)),
        ("education", "Education", 5, _education_fit(job, profile)),
    ]

    scored = []
    total = 0
    for key, label, mx, ratio in categories:
        pts = int(round(min(1.0, max(0.0, ratio)) * mx))
        total += pts
        scored.append({"key": key, "label": label, "score": pts, "max": mx})

    # Thresholds moved down ten with the scale, not because standards dropped.
    decision = "SHORTLIST" if total >= 80 else "REVIEW" if total >= 65 else "REJECT"

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
        "shortfall": _shortfall(total, scored, gaps, missing_required, reqs, sections),
    }


# What a strong tailored resume should reach. Below this the resume still ships
# — a real posting the candidate is a 78 for is worth applying to — but the
# audit says plainly what is holding it down.
# Rebased from 85 when five of the eight audit categories stopped being
# constants. The old scale credited roughly ten points to every resume
# regardless of content — twenty hardcoded or duplicated, partly offset by
# genuinely-earned structure and education marks — so the observed median fell
# from 90 to 81 with no document changing. 75 here is the same standard 85 was
# meant to express, measured on a scale that now discriminates.
TARGET_SCORE = 75


def _fixes(
    reqs: list[dict[str, Any]], sections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Concrete actions, derived from what selection already worked out.

    "You are 5 short" is a number to be disappointed by. What turns it into
    work is naming the bullet to change and what to change about it.

    Partial matches are the whole opportunity here, and they split into two
    cases that need opposite responses:

    * **A claim backs it** (`evidence` is set) — the accomplishment is on the
      page but does not use the posting's word for it. Rewriting the bullet to
      name the requirement makes it an exact match. Tailoring can fix this.
    * **Nothing backs it** (`evidence` is None) — the skill is in the
      inventory, or implied by a tool, with no accomplishment behind it.
      Rewording cannot help; only recording real evidence can.

    Requirements already matched exactly are not mentioned. There is nothing to
    do about them, and listing them as "fixes" would pad the panel with noise —
    which is how a list stops being read.
    """
    on_page: dict[str, int] = {}
    for index, section in enumerate(sections):
        for bullet in section["bullets"]:
            for hit in bullet.get("hits", []):
                on_page.setdefault(hit, index)

    fixes: list[dict[str, Any]] = []
    for req in reqs:
        if req["match"] != "partial":
            continue
        label = req["label"]
        weight = 2 if req["importance"] == "required" else 1

        if req.get("evidence"):
            fixes.append({
                "requirement": label,
                "kind": "reword",
                "fixable": True,
                "action": f"Name “{label}” explicitly in the bullet that already covers it.",
                "detail": req["evidence"][:160],
                "weight": weight,
            })
        else:
            fixes.append({
                "requirement": label,
                "kind": "evidence",
                "fixable": False,
                "action": (
                    f"“{label}” is in your skills inventory but no accomplishment "
                    "demonstrates it. Rewording cannot close this one."
                ),
                "detail": None,
                "weight": weight,
            })

    # Required before preferred, so the panel leads with what is screened on.
    fixes.sort(key=lambda f: (-f["weight"], f["kind"] != "reword"))
    return fixes[:6]


def _shortfall(
    total: int,
    scored: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    missing_required: list[dict[str, Any]],
    reqs: list[dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Why this resume is under target, and whether tailoring can fix it.

    Selection, ordering and phrasing are ours to improve. Coverage is not: it
    is a fact about what the candidate has done, and the only honest way to
    raise it is to record evidence that exists but is not in the file yet.
    Saying which of the two is responsible is the difference between a number
    the candidate can act on and one they can only be disappointed by.
    """
    if total >= TARGET_SCORE:
        return None

    lost = {c["key"]: c["max"] - c["score"] for c in scored if c["max"] > c["score"]}
    # These three all read straight off requirement coverage.
    evidence_bound = sum(
        lost.get(k, 0)
        for k in ("requirement_coverage", "technical_skills", "keyword_alignment")
    )
    fixable = sum(v for k, v in lost.items() if k not in
                  ("requirement_coverage", "technical_skills", "keyword_alignment"))

    missing = [r["label"] for r in missing_required] or [r["label"] for r in gaps]
    return {
        "target": TARGET_SCORE,
        "short": TARGET_SCORE - total,
        # Named actions, not just a number. Empty when there is genuinely
        # nothing to do but record new evidence.
        "fixes": _fixes(reqs or [], sections or []),
        "evidenceBound": evidence_bound,
        "tailoringBound": fixable,
        "missing": missing[:8],
        # evidenceBound and tailoringBound are points lost per category, which
        # sum to more than the distance to target whenever other categories are
        # already full. State which dominates rather than implying they add up.
        "summary": (
            f"{TARGET_SCORE - total} short of {TARGET_SCORE}. "
            + (
                "Almost all of it is requirement coverage — the posting asks for "
                + ", ".join(missing[:3])
                + (" and others" if len(missing) > 3 else "")
                + ", which the evidence file does not support. Re-tailoring cannot "
                "close this; only recording evidence you can stand behind can."
                if evidence_bound >= fixable and missing
                else "Most of it is presentation rather than coverage, so "
                "re-tailoring can close it."
            )
        ),
    }
