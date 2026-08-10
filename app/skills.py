"""Reading requirements out of a job description.

Two layers, because either alone is dishonest.

The canonical vocabulary below deliberately covers far more than the candidate
knows: built only from their own skills, every job would score as a perfect
match and true gaps would be invisible.

But a fixed vocabulary has the same failure in subtler form. Anything outside
it is not merely unscored — it is unseen, so it can never be reported as a
gap. That biases scores upward precisely where the stakes are highest: the
more specialised the role, the more of its real requirements fall outside the
list, and the score ends up computed almost entirely from the generic ones the
candidate does match. A mortgage compliance role requiring HMDA validation and
LOS data analysis scored 98/100 with "no gaps" that way.

So `extract_unknown_requirements` also surfaces requirement-shaped terms the
vocabulary has never heard of. An unrecognised requirement is reported as a
gap rather than silently dropped — not knowing what a term means is never a
reason to assume the candidate meets it.
"""
from __future__ import annotations

import re

SKILL_ALIASES: dict[str, list[str]] = {
    # Languages
    "Python": ["python"],
    "R": [" r ", " r,", "(r)", " r/"],
    "SQL": ["sql"],
    "Scala": ["scala"],
    "Java": ["java "],
    "JavaScript": ["javascript", "typescript"],
    "Go": ["golang"],
    "SAS": ["sas"],
    "C++": ["c++"],
    # Data engineering
    "PySpark": ["pyspark"],
    "Spark": ["apache spark", "spark"],
    "Airflow": ["airflow"],
    "Hadoop": ["hadoop"],
    "Hive": ["hive"],
    "Kafka": ["kafka"],
    "dbt": ["dbt"],
    "ETL": ["etl", "elt"],
    "Data pipelines": ["data pipeline", "data pipelines", "pipelines"],
    "Data modeling": ["data modeling", "data modelling", "dimensional model"],
    "Data quality": ["data quality", "data integrity", "data validation"],
    "Data warehousing": ["data warehouse", "warehousing"],
    # Cloud / infra
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "CI/CD": ["ci/cd", "continuous integration", "continuous delivery"],
    "Terraform": ["terraform"],
    # Databases / warehouses
    "Snowflake": ["snowflake"],
    "Databricks": ["databricks"],
    "Redshift": ["redshift"],
    "BigQuery": ["bigquery"],
    "Postgres": ["postgres", "postgresql"],
    # BI / viz
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Looker": ["looker"],
    "Excel": ["excel"],
    # ML / stats
    "Machine learning": ["machine learning", " ml ", "ml models"],
    "Statistical modeling": ["statistical model", "statistics", "statistical analysis"],
    "Regression analysis": ["regression"],
    "Hypothesis testing": ["hypothesis test", "a/b test", "ab testing", "experimentation"],
    "Forecasting": ["forecasting", "forecast"],
    "Feature engineering": ["feature engineering"],
    "TensorFlow": ["tensorflow"],
    "PyTorch": ["pytorch"],
    "LLM": ["llm", "large language model", "generative ai", "genai"],
    "NLP": ["nlp", "natural language processing"],
    # Product / delivery
    "Agile": ["agile", "scrum"],
    "Project management": ["project management", "program management"],
    "Requirements gathering": [
        "requirements gathering",
        "gather requirements",
        "business requirements",
        "elicit",
    ],
    "Stakeholder management": ["stakeholder"],
    "Cross-functional collaboration": ["cross-functional", "cross functional"],
    "Dashboarding": ["dashboard"],
    "Reporting": ["reporting", "reports"],
    "Process improvement": ["process improvement", "process design", "workflow"],
    "Mentoring": ["mentor", "coaching"],
    # Risk / quality / compliance practices. These were absent, which cut both
    # ways: "root cause analysis" is something the candidate genuinely has
    # evidence for and it was never credited, while "control design" and
    # "regulatory compliance" are things they do not have and were never
    # flagged as gaps.
    "Root cause analysis": ["root cause", "root-cause"],
    "Control design": ["control design", "design controls", "internal controls"],
    "Risk management": ["risk mitigation", "risk management", "risk issues", "risk assessment"],
    "Regulatory compliance": [
        "regulatory compliance",
        "compliance with regulations",
        "regulatory reporting",
    ],
    "Test scenario development": [
        "test scenario",
        "test case",
        "uat",
        "user acceptance testing",
    ],
    "Automated testing": ["automated testing", "test automation"],
    "Audit": ["audit", "auditing"],
    # Domain
    "Financial services": ["financial services", "fintech", "banking"],
    "Mortgage": ["mortgage", "home loans"],
    # Aliases are matched as plain substrings, so a bare "los" would fire on
    # "close", "closing" and "Los Angeles". Every form here is unambiguous.
    "Loan origination system (LOS)": [
        "loan origination system",
        "loan origination",
        "los data",
        "los system",
        "(los)",
    ],
    "MISMO": ["mismo"],
    "HMDA": ["hmda"],
    "Healthcare": ["healthcare", "hipaa"],
    "Pharmaceutical": ["pharmaceutical", "pharma"],
    "Salesforce": ["salesforce"],
    "Jira": ["jira"],
}

# Requirements that carry more weight when present in a JD.
CORE_SKILLS = {
    "Python",
    "SQL",
    "Data pipelines",
    "ETL",
    "Machine learning",
    "Statistical modeling",
    "Requirements gathering",
    "Tableau",
    "Power BI",
    "Data modeling",
}


def _in_title(alias: str, title_text: str) -> bool:
    """Word-boundary match against the job title.

    Titles are short, so a substring test is especially dangerous here — "go"
    would fire on "Google" and "R" on almost anything.
    """
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", title_text) is not None


def extract_requirements(
    description: str, title: str = ""
) -> list[tuple[str, bool]]:
    """Return (skill, is_required) pairs found in a job description.

    `is_required` is a heuristic: a skill mentioned in a sentence containing
    "required"/"must have", or that appears more than twice, is treated as
    required; otherwise preferred.

    A skill named in the job TITLE is always required, however the body reads.
    "Senior Backend Engineer, Analytics Instrumentation (Golang)" mentions Go
    once, in passing, so it was scored preferred — the gap cost almost nothing
    and the role came out at 91 for a candidate who has never written Go, while
    Python (mentioned often, and evidenced) counted as a required match. An
    employer does not put a language in the title as a nice-to-have.
    """
    text = f" {description.lower()} "
    title_text = f" {title.lower()} "
    found: list[tuple[str, bool]] = []

    for skill, aliases in SKILL_ALIASES.items():
        occurrences = 0
        matched = False
        for alias in aliases:
            count = text.count(alias)
            if count:
                matched = True
                occurrences += count
        if not matched:
            continue

        required = occurrences >= 3 or skill in CORE_SKILLS
        # Named in the title — required, whatever the body says.
        if any(_in_title(alias, title_text) for alias in aliases):
            required = True
        # Look for explicit requirement language near the first mention.
        if not required:
            for alias in aliases:
                idx = text.find(alias)
                if idx == -1:
                    continue
                window = text[max(0, idx - 220) : idx + 220]
                if any(
                    phrase in window
                    for phrase in ("required", "must have", "you have", "minimum")
                ):
                    required = True
                    break
        found.append((skill, required))

    found.extend(extract_unknown_requirements(description, found))
    return found


# --------------------------------------------------------------------------
# Open-vocabulary requirement detection
# --------------------------------------------------------------------------
# SKILL_ALIASES is a closed list, so anything outside it was invisible to
# scoring — not scored, not matched, and critically not reported as a gap.
# The effect was systematic and worst exactly where it mattered most: the more
# specialised a role, the fewer of its real requirements the vocabulary knew,
# so the score was computed almost entirely from the generic ones the
# candidate does match. A SoFi Mortgage Compliance Analyst role requiring HMDA
# validation, LOS data analysis, control design and test scenario development
# extracted five generic requirements, matched all five, and scored 98/100
# with "no gaps" — for a job the candidate is not qualified for.
#
# The fix is to stop assuming the vocabulary is complete. Terms that look like
# requirements but aren't recognised are surfaced as unknown, so they count
# against coverage and appear as gaps instead of vanishing.

# Sections that describe the company or the benefits, not the job. Anything
# from here on is boilerplate and would otherwise contribute noise terms.
_BOILERPLATE_MARKERS = (
    "why you'll love working here",
    "why you’ll love working here",
    "benefits",
    "equal opportunity",
    "eeo",
    "pay range",
    "compensation range",
    "to view our benefits",
    "privacy notice",
)

# Acronyms that are legal, HR, or general-web boilerplate rather than skills.
_ACRONYM_STOPLIST = frozenset(
    """US USA EEO EOE PTO HR CEO CTO CFO COO VP SVP EVP FAQ ID OK TV LLC INC LTD
    ADA LGBTQ LGBTQIA BIPOC AA DEI OT FT PT NA TBD ETC IE EG AM PM EST PST CST
    MST UTC GMT URL WWW HTTP HTTPS PDF FAQ Q1 Q2 Q3 Q4 K1 W2 401K IRA HSA FSA
    COBRA FMLA USA NYC SF LA TX CA NY IL WA MA GA FL NC VA CO AZ OR NV UT
    """.split()
)

_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")
# "HMDA Data Validation:", "Root Cause Analysis:", "Control Design:" — JDs very
# commonly label each responsibility with a Title Case phrase before a colon.
_LABELLED_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9/&\-]*(?:\s+[A-Z][A-Za-z0-9/&\-]*){0,4})\s*:")
# Short Title Case lines with no colon — the "What you'll need" bullet style
# ("Mortgage Experience", "Automated Testing").
_TITLE_LINE_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9\-]+){0,3})\s*$")

# Words that mark a line as a section heading rather than a requirement. Any
# candidate term containing one of these is structure, not content.
_HEADING_WORDS = frozenset(
    {
        "role", "roles", "about", "who", "we", "us", "you", "your", "our",
        "responsibilities", "qualifications", "requirements", "requirement",
        "nice", "must", "preferred", "minimum", "overview", "summary", "note",
        "location", "salary", "compensation", "position", "department",
        "reports", "team", "offer", "mission", "impact", "skills", "benefits",
        "description", "apply", "process", "hiring", "interview", "culture",
        "values", "expect", "day", "week", "join", "why", "how", "what",
    }
)


# Generic personal qualities. No résumé bullet can evidence "problem solving"
# in a way a matcher could confirm, so emitting it as a requirement guarantees
# a permanent false gap that drags coverage down. The SoFi posting labels a
# responsibility "Problem Solving: Root Cause Analysis:" — the specific half,
# root cause analysis, is scoreable and already matches exactly; the generic
# half was counted as a separate unmet requirement from the same sentence.
#
# Only terms unscoreable *in principle* belong here. Anything a claim could
# demonstrate — stakeholder management, mentoring, requirements gathering —
# stays a real requirement.
_SOFT_SKILL_TERMS = frozenset(
    {
        "problem solving", "problem solver", "problem-solving",
        "attention to detail", "detail oriented", "detail-oriented",
        "organized", "organised", "self starter", "self-starter",
        "team player", "hard working", "hard-working", "proactive",
        "proactive approach", "critical thinking", "time management",
        "interpersonal skills", "work ethic", "adaptability", "flexibility",
        "multitasking", "fast learner", "passionate", "motivated",
    }
)


def _is_heading(key: str) -> bool:
    """True when a candidate term reads as a section heading.

    JDs label their sections with Title Case lines that look exactly like the
    labelled-requirement pattern — "Minimum requirements:", "About the team:" —
    so without this they were reported as requirements the candidate lacks,
    padding the denominator and depressing every score.
    """
    normalised = re.sub(r"[^a-z ]+", " ", key.lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()
    if normalised in _SOFT_SKILL_TERMS:
        return True
    words = [w for w in re.split(r"[^a-z]+", key) if w]
    if not words:
        return True
    return any(w in _HEADING_WORDS for w in words)


def _requirement_text(description: str) -> str:
    """Trim trailing benefits/legal boilerplate so only role content is scanned.

    A marker only counts once most of the document is behind it. These phrases
    also appear in headers — this JD opens with "Employee Applicant Privacy
    Notice" — and cutting at the first occurrence truncated the body to
    nothing, silently disabling the detection this function feeds.
    """
    lowered = description.lower()
    floor = int(len(description) * 0.4)
    cut = len(description)
    for marker in _BOILERPLATE_MARKERS:
        idx = lowered.find(marker, floor)
        if idx != -1:
            cut = min(cut, idx)
    return description[:cut]


def extract_unknown_requirements(
    description: str, already_found: list[tuple[str, bool]]
) -> list[tuple[str, bool]]:
    """Find requirement-shaped terms the canonical vocabulary doesn't know.

    Returns (term, is_required) pairs. These are reported so they can be
    matched against evidence and, when unmatched, counted as real gaps.
    """
    body = _requirement_text(description)
    if not body.strip():
        return []

    # Anything the vocabulary already covers, in any of its surface forms,
    # must not be re-reported under a different name.
    covered = " ".join(
        alias for skill, _ in already_found for alias in SKILL_ALIASES.get(skill, [])
    )
    covered += " " + " ".join(skill.lower() for skill, _ in already_found)

    # Surface forms of everything the vocabulary already reported, long enough
    # to be distinctive. A JD naming the same requirement twice in different
    # words ("Requirements gathering" and "Business Requirements Gathering")
    # must count once, or the coverage denominator inflates arbitrarily.
    covered_terms = [t for t in covered.split("  ") if len(t.strip()) >= 6]
    covered_terms += [
        alias
        for skill, _ in already_found
        for alias in SKILL_ALIASES.get(skill, [])
        if len(alias) >= 6
    ]
    covered_terms += [skill.lower() for skill, _ in already_found if len(skill) >= 6]

    seen: set[str] = set()
    out: list[tuple[str, bool]] = []

    def consider(term: str, required: bool) -> None:
        term = term.strip(" :-–—").strip()
        key = term.lower()
        if not term or len(term) < 2 or key in seen or _is_heading(key):
            return
        # Already reported under a canonical name, in either direction.
        if any(c in key or key in c for c in covered_terms):
            return
        # Near-duplicate of something already collected here — "Problem
        # Solving" and "Problem Solver" are the same requirement twice.
        stem = " ".join(w[:4] for w in key.split()[:2])
        if stem in seen:
            return
        seen.add(key)
        seen.add(stem)
        out.append((term, required))

    lowered_body = body.lower()
    # "What you'll need" / "requirements" sections describe must-haves; a term
    # appearing after such a heading is treated as required.
    need_idx = min(
        (
            i
            for i in (
                lowered_body.find("what you'll need"),
                lowered_body.find("what you’ll need"),
                lowered_body.find("requirements"),
                lowered_body.find("qualifications"),
            )
            if i != -1
        ),
        default=-1,
    )
    nice_idx = lowered_body.find("nice to have")

    for line in body.splitlines():
        pos = body.find(line)
        # A term is "required" when it sits in the must-have section and
        # before any explicit "nice to have" list.
        required = need_idx != -1 and pos >= need_idx and (nice_idx == -1 or pos < nice_idx)

        m = _LABELLED_RE.match(line)
        if m:
            consider(m.group(1), required)
            continue
        m = _TITLE_LINE_RE.match(line)
        if m and need_idx != -1 and pos >= need_idx:
            consider(m.group(1), required)

    # Domain acronyms anywhere in the role body (HMDA, LOS, MISMO, GAAP…).
    #
    # A single passing mention is not a requirement — "GDP", "IP" and "AI"
    # turned up that way in prose and were counted as unmet requirements. A
    # genuine system or regulation the job is about gets named repeatedly:
    # HMDA and LOS each appear three times in the SoFi posting.
    for token in _ACRONYM_RE.findall(body):
        if token in _ACRONYM_STOPLIST or token.lower() in covered:
            continue
        if len(token) < 3 or body.count(token) < 2:
            continue
        consider(token, True)

    return out
