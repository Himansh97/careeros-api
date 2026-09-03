"""Which of the candidate's three arguments a posting should hear.

The same career supports more than one honest framing. The application history
shows three role families being applied to in volume — analyst work, data
engineering, and product analytics — and they screen for genuinely different
things. A data engineering reviewer wants the LOS-agnostic fetching layer and
the schema-validated XML delivery; a product analytics reviewer wants the
discovery interviews and the acceptance criteria. Both are true, both are in
the evidence file, and a single ordering serves one of them badly.

**A variant changes emphasis, never claims.** It decides which projects lead
and which family the headline positions against. Every bullet still comes from
`career_evidence.json`, still passes the containment gate, and is still
selected by relevance to this posting's stated requirements. Nothing here can
add a fact, and nothing here can promote a claim the evidence does not carry.

Two rules make this safe to run unattended:

**A family is only claimed when the evidence defends it.** This is the rule
`tailor._headline` already enforced and the reason it was written: an earlier
version matched the posting title and returned the label with no reference to
the evidence at all, so a posting titled "ML Engineer" described the candidate
as an "AI/ML Analyst" whether or not a single machine-learning claim existed.
Resolution here goes through the same `_find_evidence` cascade scoring uses,
so aliases and inflections behave identically.

**A posting that matches nothing gets GENERAL, and says so.** `matched` is
empty on a default. The caller can tell "this is an analyst posting" from "I
could not tell, so I did not narrow", which are different facts and were
previously indistinguishable because the family was computed inside the
headline and thrown away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Variant:
    """A resolved framing, carrying the reason it was chosen.

    `matched` is the phrase in the posting title that selected the family, and
    `defended_by` is the evidence skill that permitted it. Both are empty on
    GENERAL, which is how a default is told apart from a match.
    """

    key: str
    label: str
    leads_with: tuple[str, ...] = ()
    matched: str = ""
    defended_by: str = ""

    @property
    def is_default(self) -> bool:
        return not self.matched

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "leadsWith": list(self.leads_with),
            "matchedOn": self.matched,
            "defendedBy": self.defended_by,
            "isDefault": self.is_default,
            "why": self.why(),
        }

    def why(self) -> str:
        if self.is_default:
            return (
                "No role family matched this posting's title, so the resume is "
                "framed generally rather than narrowed to a guess."
            )
        return (
            f"The title matched {self.matched!r}, and the evidence file carries "
            f"{self.defended_by!r}, so the resume leads with that framing."
        )


# The default. Named rather than None so every caller gets a real Variant and
# nothing downstream has to handle the absent case.
GENERAL = Variant(key="analyst", label="", leads_with=())


@dataclass(frozen=True)
class _Family:
    key: str
    pattern: str
    label: str
    # One of these with real evidence permits the family. Requiring all of them
    # would refuse families the candidate genuinely works in.
    defining: tuple[str, ...]
    # Projects to prefer when this family is resolved, named by the leading
    # identifier of the project as recorded in the evidence file. A preference,
    # not an override: a project the posting never mentions does not get promoted past
    # one it does, because the posting is better evidence of what matters to
    # this reader than the family label is.
    leads_with: tuple[str, ...] = field(default_factory=tuple)


# Ordered. The first title match wins, so narrower patterns come first —
# "data product analyst" must reach PRODUCT before the generic analyst rule,
# and "analytics engineer" must reach DATA_ENGINEER before either.
FAMILIES: tuple[_Family, ...] = (
    _Family(
        key="product_analyst",
        # Product-flavoured analyst titles. "product operations", "product
        # data analyst", "product performance analyst" all live here, as does
        # a plain "product analyst".
        #
        # Deliberately one-directional. A reverse arm — analyst-then-product —
        # was tried and read "Quantitive Analyst, Structured Products
        # Investment Team" as product analytics, because "structured products"
        # is a financial instrument and the words are simply adjacent. It was
        # caught routing the real Point72 posting. "Product" only qualifies a
        # role when it comes *before* the analyst noun; after it, it is usually
        # naming what the business sells.
        pattern=r"product.{0,24}(analyst|analytics|operations)",
        label="Product Analyst",
        defining=("stakeholder management", "requirements gathering", "data analysis"),
        leads_with=("Optionora", "RECONDESK", "CareerOS"),
    ),
    _Family(
        key="data_engineer",
        # Any engineering title in a data or analytics context, which is where
        # "Sr Data Solution Engineer", "Analytics Engineer" and "Data
        # Management Engineer" all live.
        pattern=(
            r"(data|analytics|etl|pipeline|platform|backend).{0,24}engineer"
            r"|engineer.{0,24}(data|analytics)"
            r"|data developer"
        ),
        label="Analytics Engineer",
        defining=("etl", "data pipelines", "data pipeline design"),
        leads_with=("Enterprise DevSecOps CI/CD Platform", "SettleDesk", "Custody"),
    ),
    _Family(
        key="ai_ml",
        pattern=r"machine learning|\bml\b|\bai\b|data scien",
        label="AI/ML Analyst",
        defining=("machine learning", "statistical modeling", "python"),
        leads_with=("CareerOS", "Custody"),
    ),
    _Family(
        key="business_intelligence",
        pattern=r"business intelligence|\bbi\b(?!\w)",
        label="Business Intelligence Analyst",
        defining=("power bi", "dashboarding"),
    ),
    _Family(
        key="delivery",
        pattern=r"project manager|program manager|delivery manager|scrum",
        label="Analytics Delivery Manager",
        defining=("project management", "stakeholder management"),
    ),
    _Family(
        key="financial",
        pattern=r"fp&a|financial analyst|revenue analyst|risk.{0,20}analyst|quantitative analyst",
        label="Financial Analyst",
        defining=("sql", "data analysis"),
    ),
)


def resolve(job: dict[str, Any], profile: Any) -> Variant:
    """Pick the framing this posting should get, or decline to narrow.

    Never raises and never returns None. A posting with no title, an
    unrecognised title, or a recognised title the evidence cannot defend all
    resolve to GENERAL, because each of those is a case where narrowing would
    be a guess dressed as a decision.
    """
    # Imported here rather than at module scope: `scoring` imports the profile
    # layer, and a top-level import makes a cycle out of what is otherwise a
    # leaf module.
    from .scoring import _find_evidence

    # str() rather than trusting the field: job dicts arrive from nine job
    # board APIs and a null or a number in `title` must produce a default, not
    # an AttributeError inside the submit path.
    title = str(job.get("title") or "").lower()
    if not title:
        return GENERAL

    for family in FAMILIES:
        found = re.search(family.pattern, title)
        if not found:
            continue
        for skill in family.defining:
            _, match = _find_evidence(skill, profile)
            if match in ("exact", "partial"):
                return Variant(
                    key=family.key,
                    label=family.label,
                    leads_with=family.leads_with,
                    matched=found.group(0).strip(),
                    defended_by=skill,
                )
        # The title matched a family the evidence cannot defend. Stop rather
        # than falling through to a broader pattern: the honest answer is that
        # this posting is asking for something not evidenced, and a looser
        # match would hide that behind a label that happens to fit.
        break

    return GENERAL
