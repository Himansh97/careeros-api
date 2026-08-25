"""Per-job bullet overrides — hand-tailored wording for a specific posting.

The rule-based phrasing layer only substitutes verified synonyms, which is
safe but limited: it cannot restructure a sentence to lead with what a
posting actually asks for. This holds human-written alternatives for a
specific job, applied at tailoring time.

The original claim is never modified. An override stores the rewritten text
alongside the claim it came from, so the source of every statement stays
checkable and a rewrite can always be compared against what it is based on.

The rule enforced here is factual containment: an override may reframe,
reorder or re-emphasise, but every number, employer, tool and outcome in it
must already appear in the claim. `verify_override` checks that, and a
failing override is rejected rather than exported.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import connect, now

# Editable non-bullet fields. Kept to a whitelist so an edit call can't set
# arbitrary keys on the generated document.
EDITABLE_FIELDS = ("summary", "headline")

_NUMBER = re.compile(r"\d[\d,\.]*\+?%?")

# Ceiling on how much genuinely new vocabulary a rewrite may carry. The
# aligned ProdAnalytics bullets peak at four novel words; ten is what an
# invented extra responsibility costs. Six sits between the two.
MAX_NOVEL_WORDS = 6

_STOPWORDS = frozenset(
    """the a an and or but for with from into onto that this these those
    of to in on at by as is are was were be been being it its their there
    while when where which who whom whose than then so such not no nor
    also both each more most other some any all only own same very can
    will just should now across over under through during before after
    above below between within without upon per via
    """.split()
)

_STEM_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(word: str) -> str:
    """Crude suffix strip so 'gathering' and 'gathered' compare equal."""
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def verify_override(original: str, rewritten: str) -> list[str]:
    """Return reasons the rewrite is not supported by the original claim."""
    problems: list[str] = []

    original_numbers = set(_NUMBER.findall(original))
    new_numbers = set(_NUMBER.findall(rewritten))
    invented = new_numbers - original_numbers
    if invented:
        problems.append(f"introduces figures not in the claim: {', '.join(sorted(invented))}")

    # Capitalised tokens are usually tools, systems or proper nouns. A rewrite
    # may drop them, but must not introduce one the claim never mentioned.
    # Only MID-SENTENCE capitals are tested. A rewrite is allowed to change the
    # opening verb — that is most of what re-leading a bullet means — and the
    # first word is capitalised for position, not because it names anything.
    # Every capital after that is a tool, system or employer, which is exactly
    # the class of detail a rewrite must not invent.
    original_words = {w.lower() for w in re.findall(r"[A-Za-z0-9+/\.]+", original)}
    body = rewritten.split(maxsplit=1)[1] if len(rewritten.split()) > 1 else ""
    added = {
        token.strip(".,()")
        for token in re.findall(r"\b[A-Z][A-Za-z0-9+/\.]{1,}\b", body)
        if token.strip(".,()").lower() not in original_words
    }
    if added:
        problems.append(f"introduces proper nouns not in the claim: {', '.join(sorted(added))}")

    # Fabrication does not have to arrive as a digit or a capitalised tool.
    # "while owning the analytics roadmap and chairing the governance council"
    # invents a scope the claim never states, in ordinary lowercase words, and
    # stays under the length ratio. So count the substantive words the rewrite
    # uses that the claim does not.
    #
    # A few are expected and legitimate — re-leading a bullet means new verbs
    # and connectives — so this fires on accumulation, not on any single word.
    def content_words(text: str) -> set[str]:
        return {
            _stem(w.lower())
            for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text)
            if w.lower() not in _STOPWORDS
        }

    novel = content_words(rewritten) - content_words(original)
    if len(novel) > MAX_NOVEL_WORDS:
        problems.append(
            f"introduces {len(novel)} substantive words absent from the claim "
            f"({', '.join(sorted(novel)[:8])}), which suggests added scope"
        )

    if len(rewritten) > len(original) * 1.6:
        problems.append("substantially longer than the claim, which suggests added content")

    return problems


# Where each author's rewrites land, per verdict. Explicit rather than the old
# `author != "user"` negation, because a negation silently hands any new author
# the strictest policy — which for `llm` would hard-reject exactly the
# borderline rewrites the review queue exists to catch.
_POLICY: dict[str, tuple[str, str, str]] = {
    #            pass       review            reject
    "system":  ("active",  "rejected",       "rejected"),
    "llm":     ("active",  "pending_review", "rejected"),
    "user":    ("active",  "active",         "active"),
}

# Layer precedence. This used to ride on `ORDER BY author` sorting 'system'
# before 'user' alphabetically — which breaks silently the moment a third
# author exists, because 'llm' < 'system'. The system layer would have
# overwritten the LLM layer with no error and no failing test.
_LAYER_PRECEDENCE = {"system": 0, "llm": 1, "user": 2}


def verdict_for(findings: list[Any]) -> str:
    """pass / review / reject, from the combined findings.

    Two composition rules. Any single reject beats any number of reviews — a
    hard containment failure is not negotiable. And three reviews escalate to
    a reject, mirroring the padding check's existing logic: a rewrite that is
    one tier more senior *and* loosely anchored *and* a third longer is not
    three small doubts, it is one large one.
    """
    tiers = [getattr(f, "tier", "reject") for f in findings]
    if "reject" in tiers:
        return "reject"
    reviews = tiers.count("review")
    if reviews >= 3:
        return "reject"
    return "review" if reviews else "pass"


def assess_override(text: str, original: str, author: str = "system",
                    seniority_ceiling: str = "") -> dict[str, Any]:
    """Judge a rewrite without storing it: verdict, outcome and findings.

    Split out of `save_override` so a proposal can be shown to the candidate
    with its verdict attached *before* anything is written. The resume coach
    needs exactly that — it drafts several rewrites at once, and a draft the
    gate would reject has to be visible as rejected rather than silently
    dropped or, worse, silently saved.

    `save_override` calls this rather than repeating it. One definition of
    "is this contained", or the preview and the write will eventually disagree
    about the same sentence — and the preview is the one the candidate reads.
    """
    from .containment import Finding, semantic_findings

    # Lexical checks stay exactly as they were; the semantic ones catch what
    # set-comparison structurally cannot see.
    lexical = verify_override(original, text)
    findings: list[Finding] = [
        Finding("lexical", "reject", detail) for detail in lexical
    ]
    findings += semantic_findings(original, text, seniority_ceiling=seniority_ceiling)

    verdict = verdict_for(findings)
    outcome = _POLICY.get(author, _POLICY["system"])[
        {"pass": 0, "review": 1, "reject": 2}[verdict]
    ]
    return {
        "verdict": verdict,
        "outcome": outcome,
        "problems": [f.detail for f in findings],
        "findings": [f.__dict__ for f in findings],
    }


def save_override(job_id: str, claim_id: str, text: str, original: str,
                  rationale: str = "", author: str = "system",
                  seniority_ceiling: str = "") -> dict[str, Any]:
    """Store a rewritten bullet.

    Containment is enforced differently depending on who wrote it, and the
    difference is deliberate:

    - `system` — written by the assistant from the evidence file. A failure
      here means it invented something, so the write is REJECTED.
    - `user` — typed by the candidate about their own career. They know
      things the evidence file doesn't record yet, so a failure is a WARNING,
      not a veto. The text is saved, the warnings are stored with it, and the
      resume surfaces the bullet as unverified so it never quietly passes as
      evidence-backed.

    - `llm` — proposed by the resume coach. Stricter than the candidate and
      looser than the assistant's own generation: a clean rewrite applies, a
      questionable one is queued for review rather than applied, and a
      contained failure is refused outright. A model may not vouch for a claim
      about someone else's career.

    Refusing the candidate's own edits would be the wrong call — it is their
    history. Recording that the claim is unbacked is the honest middle.
    """
    assessment = assess_override(text, original, author, seniority_ceiling)
    verdict = assessment["verdict"]
    outcome = assessment["outcome"]
    problems = assessment["problems"]
    findings_json = assessment["findings"]

    if outcome == "rejected":
        # Not stored. A row whose only purpose is to be ignored is a row a
        # future `status` bug can ship.
        return {"ok": False, "verdict": verdict, "queued": False,
                "problems": problems,
                "findings": findings_json}

    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO bullet_overrides
               (job_id, claim_id, text, rationale, created_at, author, warnings,
                status, verdict, original_text)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, claim_id, text, rationale, now(), author,
             json.dumps(problems) if problems else None,
             outcome, verdict, original),
        )
    return {"ok": True, "verdict": verdict, "queued": outcome == "pending_review",
            "jobId": job_id, "claimId": claim_id, "warnings": problems,
            "findings": [f.__dict__ for f in findings]}


def get_overrides(job_id: str) -> dict[str, dict[str, Any]]:
    """Merge the layers for each bullet — a user edit sits on top of a system one."""
    with connect() as conn:
        # Only active rows. This is the containment guarantee at the *read*
        # path, independent of the save-time gate: a queued rewrite cannot
        # reach a resume even if some future code path stores one carelessly.
        rows = conn.execute(
            """SELECT claim_id, text, rationale, author, warnings, original_text
               FROM bullet_overrides
               WHERE job_id=? AND COALESCE(status,'active')='active'""",
            (job_id,),
        ).fetchall()

    # Precedence applied explicitly rather than by alphabetical accident.
    ordered = sorted(rows, key=lambda r: _LAYER_PRECEDENCE.get(r["author"] or "system", -1))
    merged: dict[str, dict[str, Any]] = {}
    for r in ordered:
        merged[r["claim_id"]] = {
            "text": r["text"],
            "rationale": r["rationale"] or "",
            "author": r["author"] or "system",
            "warnings": json.loads(r["warnings"]) if r["warnings"] else [],
            "originalText": r["original_text"] or "",
        }
    return merged


def clear_override(job_id: str, claim_id: str, author: str = "user") -> None:
    """Drop one layer of a bullet, by default the candidate's own edit.

    Reverting an edit should land on the tailored wording, not on the raw
    evidence claim — so only the user layer is removed.
    """
    with connect() as conn:
        conn.execute(
            "DELETE FROM bullet_overrides WHERE job_id=? AND claim_id=? AND author=?",
            (job_id, claim_id, author),
        )


def save_document_edit(job_id: str, field: str, text: str) -> dict[str, Any]:
    """Store an edited summary or headline for one job."""
    if field not in EDITABLE_FIELDS:
        return {"ok": False, "problems": [f"'{field}' is not editable"]}
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO document_edits (job_id, field, text, created_at)
               VALUES (?,?,?,?)""",
            (job_id, field, text.strip(), now()),
        )
    return {"ok": True, "jobId": job_id, "field": field}


def get_document_edits(job_id: str) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT field, text FROM document_edits WHERE job_id=?", (job_id,)
        ).fetchall()
    return {r["field"]: r["text"] for r in rows}


def clear_document_edit(job_id: str, field: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM document_edits WHERE job_id=? AND field=?", (job_id, field)
        )


def reset_edits(job_id: str, scope: str = "user") -> None:
    """Discard edits for a job.

    Default scope is the candidate's own edits only, which is what a reset
    button in the app means: undo what I changed, keep the tailored resume
    that was prepared for me. `scope="all"` additionally drops the
    hand-tailored system layer, returning every bullet to its raw evidence
    claim — a rebuild-from-scratch, not an undo.
    """
    with connect() as conn:
        if scope == "all":
            conn.execute("DELETE FROM bullet_overrides WHERE job_id=?", (job_id,))
        else:
            conn.execute(
                "DELETE FROM bullet_overrides WHERE job_id=? AND author='user'", (job_id,)
            )
        conn.execute("DELETE FROM document_edits WHERE job_id=?", (job_id,))


def clear_overrides(job_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM bullet_overrides WHERE job_id=?", (job_id,))


def apply_overrides(job_id: str, sections: list[dict[str, Any]]) -> int:
    """Swap in hand-tailored wording, recording what it replaced."""
    overrides = get_overrides(job_id)
    if not overrides:
        return 0
    applied = 0
    for section in sections:
        for bullet in section.get("bullets", []):
            entry = overrides.get(bullet.get("id", ""))
            if not entry:
                continue
            bullet["originalText"] = bullet.get("originalText") or bullet["text"]
            bullet["text"] = entry["text"]
            bullet["changeType"] = "reworded"
            bullet["editedBy"] = entry["author"]
            bullet["whyChanged"] = entry["rationale"] or (
                "Edited by you." if entry["author"] == "user"
                else "Hand-tailored to this posting."
            )
            # An edit the evidence file doesn't support must not present as
            # sourced. Keep the warnings on the bullet so the UI and the audit
            # can both show it as your claim rather than a verified one.
            if entry["warnings"]:
                bullet["unverified"] = True
                bullet["verificationWarnings"] = entry["warnings"]
                bullet["evidence"] = {
                    **bullet.get("evidence", {}),
                    "source": "Edited by candidate — not backed by career_evidence.json",
                }
            applied += 1
    return applied
