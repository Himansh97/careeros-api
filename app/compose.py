"""Outbound writing that sounds like a person and names the actual work.

Every message this system sent came from one fixed template: five paragraphs
with a comma-list of skills, one evidence claim and an optional gap line
swapped in. Thirteen drafts read identically and sat unsent for weeks. The one
that finally went out — rewritten by hand to answer a recruiter's seven
questions point by point — got a reply in twenty-four minutes.

The template's deeper failure is what it leaves out. `_best_proof` surfaces a
single claim ranked by keyword overlap, so of thirty-seven recorded
accomplishments the reader learns about one. The LOS-agnostic abstraction
layer, the sixteen-rule GL engine, the Ginnie Mae delivery, the platform that
took team onboarding from three weeks to same-day — none of it reaches a human.

So this module generates the wording. Two things make that safe rather than
reckless:

**The model never chooses the facts.** `select_evidence` picks the claims
before any prompt is built, from the same relevance function the resume writer
uses. The model is handed those claims verbatim and asked to write about them.

**Every factual sentence must name the claim it came from.** The returned JSON
maps sentence to `claim_id`, and each pair goes through `verify_override` and
`containment.semantic_findings` — the gates that already gate resume bullets. A
sentence carrying a figure or a tool name with no claim behind it is treated as
a reject, because unattributed fact is exactly the fabrication route.

Any rejection discards the whole generation and ships the deterministic
template. A missing key, a spent budget, a malformed response and a caught
fabrication all land in the same place: today's output, unchanged. Nothing here
is load-bearing for producing *a* message, only a better one.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .profile import CandidateProfile, EvidenceClaim

logger = logging.getLogger(__name__)

# How many accomplishments a single message may draw on.
#
# One was the old behaviour and it is why nobody learned what the candidate
# built. Beyond three the message stops being a note and becomes a resume in
# prose, which is the other way to get ignored.
MAX_EVIDENCE = 3

# Phrasing that marks a message as machine-written, or as written by someone
# not paying attention. "wanted to reach out directly" is in the template this
# replaces — without naming it here the model imitates it from the examples and
# the whole exercise produces a new house style instead of a human voice.
BANNED_PHRASES = (
    "i am writing to",
    "i hope this email finds you",
    "i hope this finds you",
    "leverage",
    "passionate about",
    "perfect fit",
    "wanted to reach out directly",
    "i wanted to reach out",
    "as you can see from my resume",
    "thank you for your time and consideration",
    "i believe i would be a great",
    "synergy",
    "dynamic environment",
)

# Language that promises a file. The API holds no mail credentials, so anything
# it hands to the candidate's mail client carries no attachment — and the old
# template said "Resume attached" regardless. Seven real recruiters received an
# email pointing at a file that was not there.
_ATTACHMENT_LANGUAGE = re.compile(
    r"\b(attach(ed|ing|ment)|enclosed|see the attached|cv attached)\b", re.I
)

_CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z0-9+#./-]{1,}\b")
_NUMBER = re.compile(r"\d")
_FIGURE = re.compile(r"\d[\d,\.]*\+?%?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")

# Capitalised words that carry no claim about experience. Days, months and the
# first-person pronoun appear mid-sentence constantly and are not proper nouns
# in the sense the gate cares about.
_COMMON_CAPITALS = frozenset(
    """i i'd i'm i've monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october november
    december english us usa u.s. american""".split()
)


def _word_tokens(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _bare_figures(figures: set[str]) -> set[str]:
    """Figures without trailing sentence punctuation, so "30%." matches "30%"."""
    return {f.rstrip(".,") for f in figures}


def _bare(word: str) -> str:
    """Lowercased, without trailing punctuation.

    The employer field reads "Supreme Lending (Everett Financial, Inc.)" and a
    sentence naming it writes "Inc.," — so the same word arrived as "Inc." from
    one side and "Inc" from the other, and a legitimate mention of the
    candidate's own employer was reported as an invented proper noun.
    """
    return word.lower().strip(".,;:!?)(-/")


# Containment findings that mean something for a resume bullet and nothing for
# a sentence in a letter.
#
# `unknown_verb` fires when the opening word is not in the seniority lexicon.
# That matters on a bullet, where the leading verb is what states how much
# authority the candidate held. A sentence in prose opens with whatever the
# sentence needs — "At Freyr Solutions, I..." reported an unranked verb of
# "at" — so the check produces a warning on almost every generated line and
# teaches the reviewer to ignore the warnings column. Seniority inflation is
# still caught: `seniority_escalation` reads the governed verbs anywhere in the
# sentence, not just the first word.
_NOT_APPLICABLE_TO_PROSE = frozenset({"unknown_verb"})


@dataclass
class Composition:
    """A generated message, and whether it earned the right to ship."""

    subject: str
    body: str
    used_claim_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated: bool = False

    @property
    def opening(self) -> str:
        """First sentence of the body, used to detect a house style forming."""
        for line in self.body.splitlines():
            line = line.strip()
            # Skip the greeting: every message starts "Hi <name>," and
            # comparing those would find a collision every time.
            if not line or line.lower().startswith(("hi ", "hello ", "dear ")):
                continue
            return re.split(r"(?<=[.!?])\s", line)[0].strip()
        return ""


def select_evidence(
    score: dict[str, Any],
    profile: CandidateProfile,
    limit: int = MAX_EVIDENCE,
) -> list[EvidenceClaim]:
    """The claims that best answer this posting, spread across capabilities.

    `_best_proof` returned exactly one, so a message could only ever describe a
    single piece of work. This returns up to `limit`, and deliberately refuses
    to stack near-duplicates: three claims that all demonstrate "requirements
    gathering" teach the reader one thing three times, while the domain
    evidence that would actually distinguish the candidate goes unmentioned.

    Relevance is delegated to `tailor._relevance` rather than reimplemented. A
    local copy of that logic previously ranked a Power BI dashboard above MISMO
    schema validation on a *mortgage compliance* posting, because it matched
    requirement labels as plain substrings with no aliases.

    Only `PRESENT_AND_EXPLICIT` claims are eligible. A claim describing
    something designed or in progress can be rewritten into a delivered outcome
    using only words already present, which passes every downstream check — so
    it is excluded here, before generation, rather than caught after.
    """
    from .tailor import _relevance

    wanted = list(score.get("strongMatches", [])) + list(score.get("partialMatches", []))

    ranked: list[tuple[float, EvidenceClaim, set[str]]] = []
    for claim in profile.evidence:
        if not claim.approved_for_resume:
            continue
        if claim.classification != "PRESENT_AND_EXPLICIT":
            continue
        hits, matched = _relevance(claim, wanted)
        if not hits:
            continue
        # A quantified outcome reads as credible, so it breaks ties — but it
        # cannot outrank genuine relevance, which is why it is a fraction.
        rank = hits + (0.5 if _NUMBER.search(claim.claim) else 0.0)
        ranked.append((rank, claim, set(matched)))

    ranked.sort(key=lambda r: r[0], reverse=True)

    chosen: list[EvidenceClaim] = []
    covered: set[str] = set()
    for _, claim, matched in ranked:
        if len(chosen) >= limit:
            break
        # Skip a claim that only re-proves requirements already covered. The
        # first claim through always passes, so this narrows variety rather
        # than blocking selection.
        if chosen and matched and matched <= covered:
            continue
        chosen.append(claim)
        covered |= matched

    # If the spread rule was too strict to fill the quota, top up by rank.
    if len(chosen) < limit:
        for _, claim, _matched in ranked:
            if len(chosen) >= limit:
                break
            if claim not in chosen:
                chosen.append(claim)

    return chosen


def _sentence_is_factual(text: str) -> bool:
    """Whether a sentence asserts something that needs backing.

    Numbers and mid-sentence proper nouns are what a reader will take as fact
    and what a hiring manager can check. "I'd welcome a short call" needs no
    claim behind it; "cut check time from 18 minutes to 9" does.
    """
    return bool(_NUMBER.search(text) or _CAPITALISED.search(text))


def _prose_problems(
    claim: EvidenceClaim, sentence: str, allowed: frozenset[str] = frozenset()
) -> list[str]:
    """The figure and proper-noun checks, calibrated for a sentence in a letter.

    `verify_override` is the right gate for its own job — rewriting a resume
    bullet, where the rewrite should be about the same length and draw on about
    the same words. Pointed at prose it produced false rejects immediately:
    "At Freyr Solutions, I coordinated..." was rejected for introducing the
    proper nouns "Freyr" and "Solutions", which are the claim's own employer,
    and for being "substantially longer than the claim", which a sentence with
    a conversational lead-in always will be.

    So the two checks that survive the change of medium are kept and the two
    that do not are dropped:

    * **Figures must exist in the claim.** An invented number is the same lie
      in any medium, and this is the check doing most of the work.
    * **Proper nouns must be sourced** — but from the claim *plus* what is
      legitimately available to a letter: the employer whose work it was, the
      skills recorded against it, and the company and role being written to.
      A recruiter's own company name appearing in a message to them is not
      fabrication.

    Length ratio and novel-word budget are dropped entirely. Both measure
    "distance from the original phrasing", which is the point of writing prose
    rather than a defect in it. `semantic_findings` still covers what those
    checks were reaching for: added scope shows up as seniority escalation or
    a re-anchored metric, and both are caught there.
    """
    problems: list[str] = []

    # Figures from the claim's own record, not only its sentence. A message may
    # legitimately say when the work happened — "in 2024", "over eighteen
    # months in 2023" — and the year lives in `date_range` rather than in the
    # claim text, so without this a true statement of timing reads as a
    # fabricated statistic.
    claim_numbers = set(_FIGURE.findall(claim.claim))
    claim_numbers |= set(_FIGURE.findall(claim.date_range or ""))
    claim_numbers |= {n.split("-")[0] for n in _FIGURE.findall(claim.date_range or "")}
    invented = {
        n for n in _FIGURE.findall(sentence) if n.rstrip(".,") not in _bare_figures(claim_numbers)
    }
    if invented:
        problems.append(
            f"introduces figures not in the claim: {', '.join(sorted(invented))}"
        )

    known = {_bare(w) for w in _word_tokens(claim.claim)}
    known |= {_bare(w) for w in _word_tokens(claim.employer)}
    for skill in claim.skills:
        known |= {_bare(w) for w in _word_tokens(skill)}
    known |= {_bare(w) for w in allowed}

    unsourced = {
        noun
        for noun in _CAPITALISED.findall(sentence)
        if _bare(noun) not in known and _bare(noun) not in _COMMON_CAPITALS
    }
    if unsourced:
        problems.append(
            f"names things the claim does not: {', '.join(sorted(unsourced))}"
        )

    return problems


def verify_sentences(
    sentences: list[dict[str, str]],
    claims: dict[str, EvidenceClaim],
    allowed: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Check each generated sentence against the claim it says it came from.

    Returns (rejections, warnings). A non-empty rejection list means the whole
    generation is discarded — not the offending sentence, the whole thing.
    Removing one sentence from a message written as a unit leaves prose that no
    longer reads as intended, and the deterministic fallback is always
    available and always honest.
    """
    from .containment import semantic_findings

    rejections: list[str] = []
    warnings: list[str] = []

    for item in sentences:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        claim_id = (item.get("claim_id") or "").strip()

        if not claim_id:
            # An unattributed sentence is fine if it asserts nothing checkable.
            if _sentence_is_factual(text):
                rejections.append(f"unattributed factual sentence: {text[:80]!r}")
            continue

        claim = claims.get(claim_id)
        if claim is None:
            rejections.append(f"cites a claim that was never supplied: {claim_id!r}")
            continue

        rejections.extend(
            f"{claim_id}: {p}" for p in _prose_problems(claim, text, allowed)
        )

        # The semantic gate transfers directly: seniority inflation, moving a
        # number onto a different noun, and inventing a tool are wrong in prose
        # for exactly the reasons they are wrong in a bullet.
        for finding in semantic_findings(
            claim.claim, text, seniority_ceiling=getattr(claim, "seniority_verb", "")
        ):
            if finding.code in _NOT_APPLICABLE_TO_PROSE:
                continue
            if finding.tier == "reject":
                rejections.append(f"{claim_id}: {finding.detail}")
            else:
                warnings.append(f"{claim_id}: {finding.detail}")

    return rejections, warnings


VOICE = """You write short outbound messages for a job candidate. One message at a time.

You are given the posting, and two or three accomplishments from the candidate's verified
record. Write about those accomplishments. You may not add any others, and you may not add
detail to the ones you are given.

What makes these work:

- Greet them by first name on its own line: "Hi Hank," — not a bare "Hank,".
- **Then orient them before you pitch.** One short sentence saying who is writing and why
  this role, or why this person. A stranger opening a message that begins mid-argument
  about their job posting does not know who you are, and reads it as automated. This is
  the single most common way these fail: they are accurate, specific, and feel like a
  machine matched two documents. Name the role. It is not a cliché to tell someone which
  job you mean, it is a courtesy.
- Only then react to something specific in the posting. Avoid "I am writing to express my
  interest" and its relatives — orienting the reader takes one plain sentence, not a
  formula.
- Say one concrete thing the candidate built, with the number that came with it. Specificity
  is what makes a stranger believe you; adjectives are what makes them stop reading.
- Connect the paragraphs. Each one should follow from the last rather than sitting beside
  it as another bullet in prose form. A list of achievements with the bullets removed is
  still a list, and it reads like one.
- Write to a person, not to a requirements table. If they have a name and a job title, they
  are the reason this message exists rather than a form submission — sound like you know
  that.
- Vary sentence length. A short one after two long ones is how written speech sounds.
- At most one line with any wit in it, and only if the work earns it. A joke at the top of a
  cold email from a stranger reads as nervous. Dry beats clever.
- Be warm and direct. Assume the reader is busy, competent, and has read fifty of these
  today.
- End with one easy question and one graceful exit — naming the acceptable "no" is what
  makes the "yes" cheap to give. Close like a person: a plain "Thanks for reading this" or
  "Either way, I appreciate the time" beats trailing off after the ask.
- Under 200 words. It will be read on a phone. The extra room over the old limit is
  for the orienting sentence and the connective tissue, not for more achievements.

Hard rules:

- Every sentence that contains a number or a proper noun must come from one of the supplied
  accomplishments, and you must say which one.
- Do not invent figures, tools, companies, dates, team sizes or outcomes. Do not upgrade
  "supported" into "led", or move a number from the thing it measured onto something else.
- Do not claim the candidate has experience with anything the accomplishments do not show.
  If the posting asks for something they lack, either stay silent about it or name it
  plainly — never imply it.
- Do not mention an attached resume unless explicitly told one is attached.
- Never write a bare URL other than the portfolio host you are given, and never write
  a scheme, a path or a tracking parameter on anything.

Return only JSON, no prose around it:

{"subject": "...",
 "paragraphs": ["greeting line", "first paragraph", "second paragraph", "sign-off"],
 "sentences": [{"text": "<a sentence from a paragraph, verbatim>", "claim_id": "<id>"}]}

`paragraphs` is an array of strings, one per paragraph, with no newline characters inside
any of them. Include the greeting as the first element. Do not include the candidate's
contact details or signature block — those are appended afterwards.

`sentences` must list every sentence in the body that contains a number or a proper noun,
copied exactly as it appears, each with the id of the accomplishment it came from.
Sentences that assert nothing checkable may be omitted."""


def _requirements_brief(job: dict[str, Any], score: dict[str, Any]) -> str:
    """What this posting actually asks for, in the posting's own words."""
    lines: list[str] = []
    for req in (score.get("requirements") or [])[:12]:
        label = req.get("label")
        if not label:
            continue
        importance = req.get("importance") or "preferred"
        match = req.get("match") or "gap"
        lines.append(f"- {label} ({importance}; candidate: {match})")
    if not lines:
        lines.append("- (no requirements extracted)")
    return "\n".join(lines)


def _prompt(
    job: dict[str, Any],
    score: dict[str, Any],
    profile: CandidateProfile,
    claims: list[EvidenceClaim],
    contact: dict[str, Any] | None,
    *,
    has_attachment: bool,
    avoid_openings: list[str],
) -> str:
    who = (contact or {}).get("name") or ""
    role = (contact or {}).get("title") or ""
    body = (job.get("description") or "")[:2500]

    parts = [
        f"POSTING: {job.get('title')} at {job['company']['name']}",
        f"LOCATION: {job.get('location')}",
        "",
        "WHAT THE POSTING ASKS FOR:",
        _requirements_brief(job, score),
        "",
        "POSTING TEXT (excerpt):",
        body,
        "",
        "RECIPIENT: " + (f"{who}, {role}" if who else "unknown — greet without a name"),
        "",
        f"CANDIDATE: {profile.name} — {profile.headline}",
        f"LOCATION: {profile.location}. WORK AUTHORISATION: {profile.work_authorization}",
        "",
        "ACCOMPLISHMENTS YOU MAY WRITE ABOUT (verbatim; do not embellish):",
    ]
    for claim in claims:
        parts.append(f"[{claim.claim_id}] ({claim.employer}) {claim.claim}")

    parts.append("")
    parts.append(
        "RESUME IS ATTACHED: yes — you may refer to it."
        if has_attachment
        else "RESUME IS ATTACHED: no — do not mention an attachment."
    )
    from .outreach import portfolio_host

    site = portfolio_host(getattr(profile, "portfolio_url", ""))
    if site:
        parts.append(
            f"PORTFOLIO: {site} — live demos of the candidate's own work. You may "
            "point at it once, in the body, only if one of the accomplishments above "
            "is the sort of thing a reader would want to see running. Write the host "
            "exactly as given, with no scheme and no path. It also appears in the "
            "signature, so do not repeat it there."
        )

    if avoid_openings:
        parts.append("")
        parts.append(
            "Recent messages opened with the lines below. Do not open like any of them:"
        )
        parts.extend(f"- {line}" for line in avoid_openings)

    return "\n".join(parts)


def _parse(raw: str) -> dict[str, Any] | None:
    """Pull the JSON object out of a model response."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def check_style(subject: str, body: str, *, has_attachment: bool) -> list[str]:
    """Style rules that are cheaper to enforce than to ask for.

    A model asked for variety produces variety for three messages and a house
    style by the twentieth, so the phrases that mark machine-writing are listed
    rather than described.

    The attachment rule is not style. The API cannot attach a file, so copy
    that promises one is wrong in a way the recipient will notice.
    """
    problems: list[str] = []
    haystack = f"{subject}\n{body}".lower()

    for phrase in BANNED_PHRASES:
        if phrase in haystack:
            problems.append(f"uses template phrasing: {phrase!r}")

    if not has_attachment and _ATTACHMENT_LANGUAGE.search(f"{subject}\n{body}"):
        problems.append("promises an attachment that will not be there")

    return problems


# ---------------------------------------------------------------- repetition

_OPENINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS compose_openings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    opening TEXT NOT NULL,
    at TEXT NOT NULL
);
"""

# How many recent openings are shown to the model and checked against. Twenty is
# roughly a fortnight of sending at this volume — far enough back that a repeat
# would be noticed by a recruiter who received two, near enough that the prompt
# stays small.
_OPENING_MEMORY = 20


def recent_openings(channel: str = "email", limit: int = _OPENING_MEMORY) -> list[str]:
    from .store import connect

    with connect() as conn:
        conn.executescript(_OPENINGS_SCHEMA)
        rows = conn.execute(
            "SELECT opening FROM compose_openings WHERE channel=? "
            "ORDER BY id DESC LIMIT ?",
            (channel, limit),
        ).fetchall()
    return [r["opening"] for r in rows]


def remember_opening(opening: str, channel: str = "email") -> None:
    if not opening.strip():
        return
    from .store import connect, now

    with connect() as conn:
        conn.executescript(_OPENINGS_SCHEMA)
        conn.execute(
            "INSERT INTO compose_openings (channel, opening, at) VALUES (?,?,?)",
            (channel, opening.strip(), now()),
        )


def _normalise(line: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", line.lower()).strip()


# ------------------------------------------------------------------- compose


_SIGNOFF = re.compile(
    r"^(best|thanks|thank you|regards|best regards|cheers|sincerely|"
    r"all the best|warmly)\b[,.!]*\s*$",
    re.I,
)


def _drop_signoff(paragraphs: list[str], profile: CandidateProfile) -> list[str]:
    """Remove a closing the model added, so the appended one is not a second.

    The prompt asks it not to sign off, and it mostly complies — but "Best,
    Himanshu" came back on one message in four, which then sat directly above
    the real signature block and read as a mistake. Cheaper to strip than to
    keep re-asking, and stripping is safe: a sign-off carries no information.
    """
    first_name = (profile.name or "").split(" ")[0].lower()
    out = list(paragraphs)
    while out:
        tail = out[-1].strip()
        lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
        # A trailing block is a sign-off when every line in it is either a
        # closing word or the candidate's own name.
        if lines and all(
            _SIGNOFF.match(ln)
            or ln.lower().rstrip(",.") in {first_name, (profile.name or "").lower()}
            for ln in lines
        ):
            out.pop()
            continue
        break
    return out


def _signature(profile: CandidateProfile) -> str:
    """Appended rather than generated.

    Contact details are the one part of the message with no room for
    interpretation, and asking a model to reproduce a phone number is asking
    for a transcription error in the only field where one is unrecoverable.
    """
    from .outreach import _linkedin_handle, portfolio_host

    lines = [profile.name]
    contact_line = " | ".join(x for x in (profile.phone, profile.email) if x)
    if contact_line:
        lines.append(contact_line)
    handle = _linkedin_handle(profile.linkedin_url)
    if handle:
        lines.append(f"LinkedIn: {handle}")
    site = portfolio_host(getattr(profile, "portfolio_url", ""))
    if site:
        # Last line of the block, because it is the one a curious reader
        # clicks and the one they should still see after skimming past
        # the phone number they were never going to dial.
        lines.append(f"Work: {site}")
    return "\n".join(lines)


def _allowed_nouns(
    job: dict[str, Any],
    profile: CandidateProfile,
    contact: dict[str, Any] | None,
) -> frozenset[str]:
    """Proper nouns a letter may use without them counting as invention.

    The recipient's own company and job title appearing in a message addressed
    to them is not a claim about the candidate's experience, and neither is
    their name or the candidate's. Everything else still has to come from the
    claim being cited.
    """
    words: list[str] = []
    words += _word_tokens(job.get("title", ""))
    words += _word_tokens((job.get("company") or {}).get("name", ""))
    words += _word_tokens(job.get("location", ""))
    words += _word_tokens(getattr(profile, "name", ""))
    words += _word_tokens(getattr(profile, "location", ""))
    words += _word_tokens(getattr(profile, "portfolio_url", ""))
    words += _word_tokens((contact or {}).get("name", ""))
    words += _word_tokens((contact or {}).get("title", ""))
    return frozenset(w.lower() for w in words)


def compose_email(
    job: dict[str, Any],
    score: dict[str, Any],
    profile: CandidateProfile,
    contact: dict[str, Any] | None = None,
    *,
    has_attachment: bool = False,
    attempts: int = 2,
) -> Composition | None:
    """Write one outreach email, or return None to use the rule-based one.

    None is the honest answer to every failure — no key, spent budget, bad
    JSON, a caught fabrication, template phrasing — because the caller has a
    deterministic message ready and shipping that is never wrong, only duller.
    """
    from .llm import complete

    claims = select_evidence(score, profile)
    if not claims:
        # Nothing in the vault speaks to this posting. A generated message would
        # have to be about nothing, which is worse than the template.
        return None

    by_id = {c.claim_id: c for c in claims}
    avoid = recent_openings("email")

    for attempt in range(attempts):
        result = complete(
            _prompt(
                job, score, profile, claims, contact,
                has_attachment=has_attachment,
                avoid_openings=avoid,
            ),
            purpose="outreach_email",
            system=VOICE,
            job_id=job.get("id"),
            # The response carries the message *and* a sentence-to-claim map,
            # so it is roughly twice the length of the email itself. At 1400
            # this truncated mid-string on longer postings and the JSON simply
            # failed to parse — which looked like a malformed response and was
            # actually a budget too small for the schema being asked for.
            max_tokens=3000,
        )
        if result is None:
            return None

        parsed = _parse(result.text)
        if not parsed:
            logger.info(
                "compose: unparseable response for %s — %r",
                job.get("id"), result.text[:300],
            )
            continue

        subject = (parsed.get("subject") or "").strip()
        # Paragraphs rather than one string: a body with real newlines in it is
        # invalid inside a JSON string unless escaped, and that is the single
        # most common way these responses fail to parse.
        paragraphs = [
            str(p).strip() for p in (parsed.get("paragraphs") or []) if str(p).strip()
        ]
        paragraphs = _drop_signoff(paragraphs, profile)
        body = "\n\n".join(paragraphs)
        if not subject or not body:
            continue

        body = f"{body}\n\n{_signature(profile)}"

        composition = Composition(
            subject=subject, body=body,
            used_claim_ids=[c.claim_id for c in claims], generated=True,
        )

        style = check_style(subject, body, has_attachment=has_attachment)
        rejections, warnings = verify_sentences(
            parsed.get("sentences") or [], by_id, _allowed_nouns(job, profile, contact)
        )

        opening = composition.opening
        repeated = _normalise(opening) in {_normalise(a) for a in avoid}

        if style or rejections or repeated:
            for problem in style + rejections:
                logger.info("compose: rejected for %s — %s", job.get("id"), problem)
            if repeated:
                logger.info("compose: opening repeats a recent message")
            # One retry, with the failed opening added to what to avoid. A
            # second failure means the deterministic message ships.
            if attempt + 1 < attempts and opening:
                avoid = avoid + [opening]
            continue

        composition.warnings = warnings
        remember_opening(opening, "email")
        return composition

    return None
