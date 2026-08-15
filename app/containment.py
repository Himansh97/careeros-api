"""The semantic checks `verify_override` cannot make.

`verify_override` is a strong gate and it is lexical. It compares token sets:
numbers, capitalised nouns, novel content words, length. Three fabrications
slip through it cleanly, and they are precisely the three an eager rewriter
produces. All three were run against the live gate and returned no problems:

    "Supported a migration..."        -> "Drove the migration..."        clean
    "...reducing cycle time by 40%"   -> "...driving adoption by 40%"    clean
    "...using Python and SQL"         -> "...using python, sql, dbt"     clean

Seniority inflation uses only words already present. Metric re-attribution
leaves the token set identical. Lowercase tool names escape a check that only
looks at capitals. None of them can be caught by comparing sets, so this module
compares *structure* instead: which verb governs the sentence, which noun each
number sits beside, and whether a token is a tool name we recognise.

Everything here is closed-vocabulary. An unknown verb or an unknown noun is
never a rejection — it is a REVIEW, routed to a human. A gate that guesses in
the strict direction trains people to override it, and an override habit is
worse than a slightly smaller vocabulary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .overrides import _STOPWORDS, _stem

# ── tiers ────────────────────────────────────────────────────────────────
#
# Equivalence classes, not a smooth scale. Two verbs in the same tier are
# interchangeable, which is exactly what re-leading a bullet does — the whole
# point of an override is to open with the posting's verb instead of the
# claim's. Only crossing a tier boundary is a change in asserted authority.

VERB_TIERS: dict[str, int] = {
    # 0 — participation
    "assist": 0, "help": 0, "contribut": 0, "particip": 0, "attend": 0,
    "shadow": 0,
    # 1 — support
    "support": 1, "maintain": 1, "monitor": 1, "track": 1, "updat": 1,
    "assist": 1,
    # 2 — execution
    "gather": 2, "analyz": 2, "analys": 2, "document": 2, "test": 2,
    "validat": 2, "process": 2, "clean": 2, "prepar": 2, "compil": 2,
    "appli": 2, "use": 2, "ran": 2, "run": 2, "conduct": 2, "present": 2,
    "mentor": 2, "diagnos": 2, "research": 2, "review": 2, "perform": 2,
    "troubleshoot": 2, "resolv": 2, "investigat": 2,
    # 3 — construction
    "build": 3, "built": 3, "develop": 3, "engineer": 3, "implement": 3,
    "design": 3, "creat": 3, "deploy": 3, "automat": 3, "standardiz": 3,
    "synthesiz": 3, "integrat": 3, "consolidat": 3, "architect": 3,
    "refactor": 3, "migrat": 3,
    # 4 — direction
    "led": 4, "lead": 4, "driv": 4, "drove": 4, "coordinat": 4,
    "manag": 4, "spearhead": 4, "orchestrat": 4, "oversaw": 4, "overse": 4,
    # 5 — ownership
    "own": 5, "direct": 5, "head": 5, "govern": 5, "establish": 5,
    "found": 5, "chair": 5, "author": 5,
}

# A verb after one of these belongs to somebody else. "supported the team that
# led the migration" must not licence "Led the migration" — without this, the
# check reads the highest verb in the sentence regardless of who performed it,
# which is the exact loophole a careful rewriter would find.
_ATTRIBUTION_BREAKERS = frozenset({"that", "which", "who", "whom", "whose"})

# Nouns too generic to prove a number stayed attached to the same thing. An
# overlap consisting only of these is a weak match, not a clean one: a token
# window genuinely cannot tell "cycle time" from "onboarding time", and it
# should say so rather than guess.
_GENERIC_ANCHORS = frozenset(
    """time data rate cost value volume number count level system process work
    result output quality performance size scale team accuracy efficiency
    """.split()
)

_NUM = re.compile(r"\d[\d,\.]*[KMB]?\+?%?")
_ANCHOR_WINDOW = 4


@dataclass(frozen=True)
class Finding:
    """One problem, and how seriously to take it."""

    code: str
    tier: str  # "reject" | "review"
    detail: str


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+", text.lower())


def governed_verbs(text: str) -> set[str]:
    """Ranked verbs the *candidate* performed, not ones in a sub-clause."""
    words = _words(text)
    out: set[str] = set()
    for i, word in enumerate(words):
        stem = _stem(word)
        if stem not in VERB_TIERS:
            continue
        if i and words[i - 1] in _ATTRIBUTION_BREAKERS:
            continue
        out.add(stem)
    return out


def lead_verb(text: str) -> str:
    """The verb the sentence opens with, stemmed.

    Authority is asserted by the verb a bullet *leads* with. A ranked verb
    later in the sentence is usually a participle describing an outcome —
    "reducing", "driving a 40% improvement" — and reading those as claims of
    authority false-rejects ordinary phrasing. Measured on the real corpus,
    scanning the whole sentence flagged "driving adoption" as a four-tier
    escalation in a bullet that opened with "Supported".
    """
    parts = text.split(maxsplit=1)
    if not parts:
        return ""
    return _stem(re.sub(r"[^A-Za-z]", "", parts[0]).lower())


def seniority_escalation(original: str, rewritten: str, ceiling: str = "") -> int:
    """Tiers the rewrite's opening verb claims above what the claim supports.

    `ceiling` is the claim's recorded `seniority_verb` when there is one. It is
    authoritative because it was derived from the claim's own opening verb; the
    fallback below reads the original's opening verb the same way rather than
    scanning the sentence, which could be dragged up by a verb in a sub-clause
    the candidate never performed.
    """
    lead = lead_verb(rewritten)
    if lead not in VERB_TIERS:
        return 0  # unrankable openings are handled by `unknown_lead_verb`

    if ceiling and ceiling in VERB_TIERS:
        base = VERB_TIERS[ceiling]
    else:
        base_verb = lead_verb(original)
        if base_verb not in VERB_TIERS:
            return 0
        base = VERB_TIERS[base_verb]

    return VERB_TIERS[lead] - base


def elevated_mid_sentence(original: str, rewritten: str) -> set[str]:
    """High-authority verbs introduced away from the opening.

    Not a rejection: "driving a 40% reduction" is ordinary writing. But a
    rewrite that opens modestly and then says "while leading the workstream"
    has still claimed something, so it goes to a human.
    """
    lead = lead_verb(rewritten)
    original_stems = {_stem(w) for w in _words(original)}
    return {
        v
        for v in governed_verbs(rewritten)
        if VERB_TIERS[v] >= 4 and v != lead and v not in original_stems
    }


def unknown_lead_verb(original: str, rewritten: str) -> str | None:
    """A leading verb we cannot rank and the claim never used.

    Routed to review rather than rejected: a gap in the lexicon is our problem,
    not the writer's, and every one of these is a free signal about which verb
    to add next.
    """
    parts = rewritten.split(maxsplit=1)
    if not parts:
        return None
    lead = _stem(re.sub(r"[^A-Za-z]", "", parts[0]).lower())
    if not lead or lead in VERB_TIERS:
        return None
    if lead in {_stem(w) for w in _words(original)}:
        return None
    return lead


def _anchor_contexts(text: str) -> dict[str, set[str]]:
    """Each number mapped to the content words around it."""
    tokens = re.findall(r"\d[\d,\.]*[KMB]?\+?%?|[A-Za-z][A-Za-z\-]*", text)
    out: dict[str, set[str]] = {}
    for i, tok in enumerate(tokens):
        if not _NUM.fullmatch(tok):
            continue
        lo, hi = max(0, i - _ANCHOR_WINDOW), i + _ANCHOR_WINDOW + 1
        ctx = {
            _stem(w.lower())
            for w in tokens[lo:hi]
            if not _NUM.fullmatch(w) and len(w) >= 4 and w.lower() not in _STOPWORDS
        }
        out.setdefault(tok, set()).update(ctx)
    return out


def anchor_findings(original: str, rewritten: str) -> list[Finding]:
    """Every number must still sit beside the thing it measured."""
    orig, rw = _anchor_contexts(original), _anchor_contexts(rewritten)
    out: list[Finding] = []
    for number, ctx in rw.items():
        base = orig.get(number)
        if base is None:
            continue  # an invented figure; verify_override already owns that
        shared = ctx & base
        if not shared:
            out.append(
                Finding(
                    "anchor_moved", "reject",
                    f"'{number}' is attached to different subject matter than in the claim",
                )
            )
        elif shared <= _GENERIC_ANCHORS:
            out.append(
                Finding(
                    "anchor_generic", "review",
                    f"'{number}' is only loosely anchored to the claim "
                    f"({', '.join(sorted(shared))})",
                )
            )
    return out


# Product and technology names, curated deliberately rather than derived.
#
# The first version built this from `SKILL_ALIASES`, which was wrong: that
# vocabulary mixes tools with practices, so "forecast", "reports" and
# "mortgage" were treated as tool names and rejected three of the candidate's
# own shipped bullets. A closed vocabulary has to be closed on purpose.
#
# The test for membership is "could a rewrite invent this and mislead an
# employer" — a product name, not a discipline. `dbt` and `npm` are lowercase
# by convention and belong here; `dashboarding` and `forecasting` are things
# you do and do not.
TOOL_NAMES: frozenset[str] = frozenset(
    """
    python pyspark spark scala java javascript typescript golang kotlin swift
    sql tsql plsql nosql
    pandas numpy scipy sklearn scikit tensorflow pytorch keras xgboost
    airflow dagster prefect luigi dbt fivetran informatica talend
    tableau powerbi looker qlik quicksight superset metabase
    snowflake redshift bigquery databricks synapse teradata
    postgres postgresql mysql mariadb oracle sqlserver mongodb cassandra
    dynamodb redis elasticsearch
    hadoop hive presto trino impala kafka rabbitmq flink beam
    aws azure gcp lambda ec2 s3 glue athena sagemaker
    docker kubernetes terraform ansible jenkins github gitlab bitbucket
    jira confluence servicenow
    excel vba sas spss stata matlab
    salesforce workday netsuite sap encompass meridianlink mismo
    alteryx knime rapidminer
    fastapi flask django react nextjs node
    """.split()
)

# Single letters are never matched. "R" and "C" are real languages and also
# ordinary words; the capitalised-proper-noun check in `verify_override`
# already covers an invented "R", and matching a bare letter here would fire
# on almost every sentence.


def known_tools() -> frozenset[str]:
    """The curated vocabulary, plus any tool the candidate actually lists.

    Their own inventory is included because it names systems specific to their
    work — Encompass, MISMO — that a general list would miss, and inventing one
    of those is exactly the kind of detail an employer would check.
    """
    from .profile import load_profile

    names = set(TOOL_NAMES)
    try:
        profile = load_profile()
    except Exception:  # noqa: BLE001 - the curated list still works alone
        return frozenset(names)

    for group in (profile.skills_inventory or {}).values():
        for item in group:
            token = re.sub(r"[^A-Za-z0-9+]", "", str(item)).lower()
            # Only single-token entries, and only ones long enough to be a
            # name rather than an abbreviation that collides with prose.
            if token and len(token) > 2 and " " not in str(item).strip():
                names.add(token)
    return frozenset(names)


def invented_tools(original: str, rewritten: str) -> set[str]:
    """Tool names the rewrite introduces that the claim never mentions."""
    vocab = known_tools()
    flat = lambda t: {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+\.]*", t)}
    return (flat(rewritten) & vocab) - flat(original)


def semantic_findings(
    original: str, rewritten: str, *, seniority_ceiling: str = ""
) -> list[Finding]:
    """Everything `verify_override` structurally cannot see."""
    out: list[Finding] = []

    escalation = seniority_escalation(original, rewritten, seniority_ceiling)
    if escalation >= 2:
        out.append(
            Finding(
                "seniority_escalation", "reject",
                f"claims {escalation} levels more authority than the evidence supports",
            )
        )
    elif escalation == 1:
        out.append(
            Finding(
                "seniority_step", "review",
                "claims one level more authority than the evidence states",
            )
        )

    if elevated := elevated_mid_sentence(original, rewritten):
        out.append(
            Finding(
                "elevated_language", "review",
                f"introduces higher-authority language mid-sentence: "
                f"{', '.join(sorted(elevated))}",
            )
        )

    if lead := unknown_lead_verb(original, rewritten):
        out.append(
            Finding("unknown_verb", "review", f"opens with an unranked verb: {lead!r}")
        )

    out.extend(anchor_findings(original, rewritten))

    if tools := invented_tools(original, rewritten):
        out.append(
            Finding(
                "invented_tool", "reject",
                f"names tools the claim does not: {', '.join(sorted(tools))}",
            )
        )

    return out
