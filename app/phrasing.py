"""JD-aligned phrasing via verified term equivalences.

A bullet saying "data pipeline" when the posting says "ETL" describes the
same work in different words. A recruiter skimming for their own vocabulary
misses it, and a keyword filter misses it outright.

Two kinds of change are allowed, and nothing else:

  REPLACE  — the two terms are interchangeable, so one swaps for the other
             ("root-cause analysis" -> "root cause analysis").
  AUGMENT  — the posting's term names part of what the evidence describes, so
             it is added rather than substituted ("reporting infrastructure"
             -> "dashboard and reporting infrastructure"). Replacing here
             would both lose accuracy and break grammar: substituting the
             plural "dashboards" into "a unified ... reporting infrastructure"
             produced "a unified SQL-to-Power BI dashboards".

Numbers, employers, dates, tools and the substance of the work are never
touched. Every rewrite is grammar-checked before it is accepted, and any
rewrite that fails is discarded in favour of the original wording — a
wrong-but-keyword-rich bullet is worse than an honest one, because it still
has to survive an interview.

Deliberately excluded despite being tempting matches:
  hypothesis testing != A/B testing    (related method, different practice)
  data pipeline      != data modeling  (adjacent, not the same work)
  reporting          != BI strategy    (execution, not ownership)
"""
from __future__ import annotations

import re
from typing import Any

REPLACE = "replace"
AUGMENT = "augment"

# jd_term -> (mode, evidence phrases that support it)
# Only noun-phrase to noun-phrase pairs of the same grammatical shape. Three
# entries were removed after their output was read rather than regex-checked:
#
#   "data pipeline"     -> "ETL"            gave "a production ETL"; ETL is not
#                                           a thing you build, so the head noun
#                                           has to survive: "ETL pipeline".
#   "validating output" -> "data validation" swapped a verb phrase for a noun,
#                                           leaving the clause without a verb.
#   "demand-forecasting model" + "predictive" read as "predictive and
#                                           demand-forecasting model".
EQUIVALENCES: dict[str, tuple[str, list[str]]] = {
    "ETL pipeline": (REPLACE, ["data pipeline", "data-cleansing pipeline"]),
    "root cause analysis": (REPLACE, ["root-cause analysis"]),
    "requirements elicitation": (REPLACE, ["requirements gathering"]),
    "stakeholder engagement": (REPLACE, ["stakeholder management"]),
    "dashboard": (AUGMENT, ["reporting infrastructure"]),
}

# Article/quantifier followed by a plural noun, or vice versa — the signature
# of a substitution that broke agreement.
_BAD_AGREEMENT = re.compile(
    r"\b(a|an|each|every|one)\s+(?:[a-z-]+\s+){0,3}(dashboards|pipelines|models|reports|systems|analyses)\b",
    re.IGNORECASE,
)
_DOUBLE_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Hyphens and plurals must not hide a term that is already present.

    'data quality' looked absent from 'multi-layer data-quality controls'
    because of the hyphen, so it got added alongside it — producing
    'data quality and multi-layer data-quality controls'.
    """
    flat = re.sub(r"[-\u2013\u2014]", " ", text.lower())
    return re.sub(r"\s+", " ", flat)


def _uses(term: str, text: str) -> bool:
    """True if the term appears, tolerating hyphenation and a plural 's'."""
    t = _normalise(term)
    haystack = _normalise(text)
    return re.search(rf"(?<!\w){re.escape(t)}(?:s|es)?(?!\w)", haystack) is not None


def _is_sane(text: str) -> bool:
    """Reject a rewrite that reads wrong, however good its keywords are."""
    if _BAD_AGREEMENT.search(text):
        return False
    if _DOUBLE_WORD.search(text):
        return False
    if re.search(r"\s{2,}|\s+[,.]", text):
        return False
    return True


def align_bullet(text: str, description: str) -> tuple[str, list[str]]:
    """Return (rephrased text, substitutions made). Original on any failure."""
    out = text
    changes: list[str] = []

    for jd_term, (mode, evidence_terms) in EQUIVALENCES.items():
        if not _uses(jd_term, description) or _uses(jd_term, out):
            continue
        for evidence_term in sorted(evidence_terms, key=len, reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(evidence_term)}(?!\w)", re.IGNORECASE)
            match = pattern.search(out)
            if not match:
                continue

            original = match.group(0)
            if mode == AUGMENT and _uses(jd_term, original):
                # The evidence phrase already contains the posting's term;
                # adding it again only creates redundancy.
                continue
            if mode == REPLACE:
                new_text = jd_term
                if original[:1].isupper():
                    new_text = jd_term[:1].upper() + jd_term[1:]
            else:  # AUGMENT — keep the accurate phrase, prepend the JD's word
                new_text = f"{jd_term} and {original[:1].lower()}{original[1:]}"
                if original[:1].isupper():
                    new_text = new_text[:1].upper() + new_text[1:]

            candidate = out[: match.start()] + new_text + out[match.end():]
            if not _is_sane(candidate):
                continue  # keep the candidate's own wording
            out = candidate
            changes.append(f"'{original}' -> '{new_text}'")
            break

    return out, changes


def align_resume(resume: dict[str, Any], description: str) -> dict[str, Any]:
    total = 0
    for section in resume.get("sections", []):
        for bullet in section.get("bullets", []):
            original = bullet.get("text", "")
            aligned, changes = align_bullet(original, description)
            if not changes:
                continue
            bullet["text"] = aligned
            bullet["originalText"] = original
            bullet["changeType"] = "reworded"
            existing = bullet.get("whyChanged") or ""
            note = "Matched the posting's wording: " + "; ".join(changes) + "."
            bullet["whyChanged"] = f"{existing} {note}".strip()
            total += len(changes)
    resume["phrasingSubstitutions"] = total
    return resume
