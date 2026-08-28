"""How postings for a role actually describe the work.

Why this exists
---------------
The coach could only ever see one job description and the candidate's own
claims, so its rewrites read like a careful person paraphrasing a single
posting. Ask it to strengthen "Python" and it reaches for the words in front of
it — "utilised Python for data manipulation" — which is how a resume announces
that its author has not done the work. Practitioners do not say "utilised". They
say what they built, in what, at what scale, and they name the libraries someone
who has actually used the tool would name.

That register cannot be invented from one posting. It is recoverable from many,
and CareerOS already holds many: the discovery snapshot is thousands of live
postings for roles in this candidate's own search. Reading forty of them for the
same title is a cheap, offline way to learn how the market talks about a skill
before writing a sentence containing it.

The line this module must not cross
-----------------------------------
It returns *vocabulary*, never *content*. Nothing here is evidence, and nothing
here may be claimed. A posting asking for Spark on 10TB pipelines does not mean
the candidate has touched 10TB, and the prompt built from this says so in as
many words. The containment gate is unchanged and still compares every rewrite
against the candidate's own recorded claim — that check is what actually holds
the line. This only changes which words are available for saying a true thing
well.

No network, no model
--------------------
Reads the durable discovery snapshot, which is already on disk. The coach is a
synchronous request path; making it wait on five job boards to answer "make this
sound more senior" would trade the entire feature's responsiveness for freshness
nobody asked for. A stale snapshot is fine here — role vocabulary moves in
years, not hours.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Postings to read per query. Forty is enough for a phrase to repeat often
# enough to mean something, and small enough to stay well inside a prompt.
MAX_POSTINGS = 40
MAX_PHRASINGS = 12
MAX_TOOLS = 14
MAX_EXPECTATIONS = 6

# Words that carry no register. "Strong", "excellent" and "proven" are the
# padding a posting uses about a candidate, not the language a practitioner
# uses about their own work, and letting them through would teach the coach
# exactly the wrong voice.
_STOP = frozenset("""
a an the and or of for to in on with by at from as is are be been being was were
this that these those you your our we their its it they will would can could may
must should have has had do does did not no if then than so such other more most
using use used utilise utilised utilize utilized strong excellent proven solid
demonstrated ability skills experience knowledge understanding familiarity
years year plus preferred required must-have nice ideal ideally candidate
role position job team work working across including etc via well good great
proficiency proficient advanced expert expertise hands-on handson deep
strongly highly equivalent related field degree bachelor master minimum
excel excels excelling exceptional outstanding passionate motivated
technical kenntnisse erfahrung sowie und der die das mit von
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_LINE = re.compile(r"[\n\r]+|(?<=[.;])\s+")
_TAG = re.compile(r"<[^>]+>")

# Tools read as tools by shape: a capitalised or symbol-bearing token that is
# not a sentence-initial ordinary word. Matching a fixed list instead would
# freeze the vocabulary at whatever was popular when the list was written.
_TOOLISH = re.compile(r"^(?:[A-Z][A-Za-z0-9.+#-]{1,19}|[a-z]+(?:SQL|DB|ML)|\w*\+\+)$")


def _text(posting: dict[str, Any]) -> str:
    raw = str(posting.get("description") or "")
    return _TAG.sub(" ", raw)


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _postings(title: str) -> list[dict[str, Any]]:
    """Live postings whose title overlaps the one being written for.

    Overlap is on title words rather than an exact match: "Senior Data Analyst"
    and "Data Analyst, Growth" describe the same work, and requiring the whole
    string to match would find nothing for most real titles.
    """
    from .discovery_store import current_snapshot

    wanted = {w for w in _tokens(title) if w not in _STOP and len(w) > 2}
    if not wanted:
        return []

    try:
        jobs = list(current_snapshot().jobs)
    except Exception:  # pragma: no cover - a missing snapshot is not an error
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for job in jobs:
        words = {w for w in _tokens(str(job.get("title") or "")) if w not in _STOP}
        overlap = len(wanted & words)
        if overlap:
            scored.append((overlap, job))

    scored.sort(key=lambda pair: -pair[0])
    return [job for _, job in scored[:MAX_POSTINGS]]


def _lines_about(postings: list[dict[str, Any]],
                 skill: str) -> list[tuple[int, str]]:
    """Sentences that name the skill, tagged with which posting they came from.

    The posting index is carried so recurrence can be counted across postings
    rather than across lines. One posting repeating "hands-on Spark" in three
    bullets is one company's house style; three postings saying it is the field.
    """
    needle = skill.lower().strip()
    out: list[tuple[int, str]] = []
    for index, posting in enumerate(postings):
        for line in _LINE.split(_text(posting)):
            line = " ".join(line.split())
            if 25 <= len(line) <= 240 and _mentions(line, needle):
                out.append((index, line))
    return out


def _across_postings(items: list[tuple[int, str]]) -> Counter[str]:
    """How many distinct postings each item appeared in.

    Counting occurrences instead let one verbose posting decide the vocabulary:
    a company that writes "hands-on Spark" in four separate bullets outvoted
    three companies that each said it once. Recurrence only means something
    across employers.
    """
    seen: dict[str, set[int]] = {}
    for index, item in items:
        seen.setdefault(item, set()).add(index)
    return Counter({item: len(where) for item, where in seen.items()})


def _phrasings(lines: list[tuple[int, str]], skill: str) -> list[str]:
    """Two- and three-word phrases that recur near the skill.

    Recurrence is the whole signal. One posting saying "orchestrated Airflow
    DAGs" is one writer's habit; nine saying it is what the job is called in
    the field, and that is the register worth borrowing.
    """
    needle = skill.lower().strip()
    grams: list[tuple[int, str]] = []
    for index, line in lines:
        words = [w for w in _tokens(line) if w not in _STOP and len(w) > 2]
        for size in (2, 3):
            for i in range(len(words) - size + 1):
                gram = words[i : i + size]
                if needle in gram and len(set(gram)) == size:
                    grams.append((index, " ".join(gram)))
    counts = _across_postings(grams)
    return [phrase for phrase, n in counts.most_common(MAX_PHRASINGS) if n > 1]


def _tools(lines: list[tuple[int, str]], skill: str) -> list[str]:
    """Named tools that keep company with this skill in real postings."""
    needle = skill.lower().strip()
    found: list[tuple[int, str]] = []
    for index, line in lines:
        for word in _WORD.findall(line):
            if word.lower() == needle or len(word) < 2:
                continue
            if _TOOLISH.match(word) and word.lower() not in _STOP:
                found.append((index, word))
    counts = _across_postings(found)
    return [tool for tool, n in counts.most_common(MAX_TOOLS) if n > 1]


def vocabulary(title: str, skill: str = "") -> dict[str, Any]:
    """How postings for this title talk, optionally narrowed to one skill.

    Returns empty structures rather than raising when there is nothing to read.
    A coach turn with no market context is a slightly worse coach turn; a coach
    turn that 500s because the snapshot was cold is a broken feature.
    """
    postings = _postings(title)
    if not postings:
        return {"sampled": 0, "titles": [], "skill": skill,
                "phrasings": [], "tools": [], "expectations": []}

    skill = (skill or "").strip()
    lines = _lines_about(postings, skill) if skill else []

    # Without a named skill there is nothing to centre the n-grams on, so the
    # useful signal is what these postings ask for rather than how they phrase
    # one tool. Expectations carry that.
    expectations: list[str] = []
    seen: set[str] = set()
    fallback = [
        " ".join(l.split())
        for p in postings
        for l in _LINE.split(_text(p))
        if 40 <= len(" ".join(l.split())) <= 200
    ]
    for line in ([text for _, text in lines] or fallback):
        key = line.lower()[:60]
        if key not in seen:
            seen.add(key)
            expectations.append(line)
        if len(expectations) >= MAX_EXPECTATIONS:
            break

    return {
        "sampled": len(postings),
        "titles": sorted({str(p.get("title") or "") for p in postings})[:6],
        "skill": skill,
        "phrasings": _phrasings(lines, skill) if skill else [],
        "tools": _tools(lines, skill) if skill else [],
        "expectations": expectations,
    }


def named_skill(instruction: str, known: list[str]) -> str:
    """The skill the candidate is asking about, if they named one.

    Matched against skills the candidate's own evidence already mentions plus a
    small floor of tools common enough to be asked about before they appear in
    anyone's vault. Returning "" is the normal case and means the turn gets
    role-level context instead of skill-level.
    """
    text = f" {instruction.lower()} "
    pool = {s.lower() for s in known if s} | {
        "python", "sql", "spark", "airflow", "dbt", "tableau", "power bi",
        "excel", "r", "scala", "java", "snowflake", "databricks", "kafka",
        "pandas", "pytorch", "tensorflow", "looker", "bigquery", "redshift",
    }
    # Longest first, so "power bi" wins over "bi" and "pyspark" over "spark".
    if _AS_VERB.search(text):
        # "someone who can excel at this" is not a request about spreadsheets.
        text = _AS_VERB.sub(" ", text)
    for skill in sorted(pool, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text):
            return skill
    return ""


# Skill names that are also ordinary English words. "Candidates who excel at
# ambiguity", "spark innovation", "go beyond", "R&D" — a word boundary is not
# enough to tell these from the tool, and matching them anyway returned dental
# and parental leave as Excel's vocabulary.
_AMBIGUOUS = frozenset({"excel", "spark", "r", "go", "scala", "julia", "hive"})

# The verb reading, in the shapes it actually takes in a posting.
_AS_VERB = re.compile(
    r"\b(?:excel|excels|excelling|spark|sparks|sparking|go|goes|going)\s+"
    r"(?:at|in|on|beyond|above|with)\b",
    re.I,
)


def _mentions(text: str, skill: str) -> bool:
    """Whether the skill is *named*, rather than merely appearing as a word.

    Two filters, because they catch different things.

    The boundary stops substring matches — "excel" inside "excellent", "r"
    inside "performance". The capitalisation rule stops the verb: postings write
    the tool as a proper noun ("Advanced Excel", "Apache Spark", "R"), and use
    the lowercase form when they mean the verb. That is a heuristic and it will
    miss a posting written in lowercase, which is the right way to be wrong
    here — a missed phrase costs a little vocabulary, a false match costs the
    candidate a resume line about a spreadsheet built from a benefits section.
    """
    if skill in _AMBIGUOUS:
        if _AS_VERB.search(text):
            return False
        # The proper-noun spellings only: "Excel", "EXCEL", "R". The lowercase
        # form of these words is the verb.
        forms = "|".join(re.escape(f) for f in {skill.capitalize(), skill.upper()})
        return re.search(rf"(?<![A-Za-z0-9])(?:{forms})(?![A-Za-z0-9])",
                         text) is not None
    return re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])",
                     text.lower()) is not None
