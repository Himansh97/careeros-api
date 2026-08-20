"""Finding an address without buying one, and never confusing found with guessed.

Every paid provider is dry: Hunter's free tier is spent, Apollo's free plan
returns 403 for people search by design, and Tomba and Anymailfinder have no
key. That left five companies unreachable, so this is the half of the problem
that can be solved without paying anyone.

Three sources, and the distinction between them is the whole point:

* **Harvested** — a real address, taken from public Git commit metadata that
  GitHub publishes through its own API. Somebody committed with it, so it exists.
  This is not a guess and is never labelled as one.
* **Inferred** — a name run through the pattern a company demonstrably uses.
  Companies are internally consistent, which is the regularity every one of
  these tools relies on. It is still a guess, and it says so.
* **Unverified** — no pattern known. Reported as such rather than fabricated.

What this deliberately does not do is scrape LinkedIn. Their terms prohibit it,
and `prefill.py` is structurally incapable of submitting an application for the
same class of reason — a system whose value rests on its output being
trustworthy does not get to break the rules it finds inconvenient.

The honest limit: coverage. A company that publishes no code yields nothing here,
permanently. Hunter's advantage is a decade of breadth, not cleverness, and
nothing built in an afternoon replaces it. This wins on the companies that write
open source and loses everywhere else.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"

# Unauthenticated GitHub allows 60 requests an hour, which one company exhausts.
# The candidate's own `gh` login raises it to 5,000, and asking `gh` for the
# token avoids storing a second copy of a credential that already exists.
def github_token() -> str | None:
    try:
        done = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = done.stdout.strip()
    return token if done.returncode == 0 and token else None


# Addresses that belong to machinery rather than people.
_NOISE = re.compile(
    r"(noreply|no-reply|\[bot\]|dependabot|renovate|github-actions|"
    r"users\.noreply\.github\.com|semantic-release|greenkeeper)",
    re.IGNORECASE,
)

def usable(email: str, suffix: str) -> bool:
    """Whether a commit address is a person at this company.

    Its own function so the filter can be tested. Asserting against the regex
    directly proves the pattern matches, not that the harvester consults it —
    a test that passes with the filter removed is not guarding anything.
    """
    address = (email or "").lower().strip()
    return bool(address) and address.endswith(suffix) and not _NOISE.search(address)


_REPOS_SCANNED = 5
_COMMITS_PER_REPO = 100


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CareerOS/1.0"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def find_org(client: httpx.AsyncClient, company: str, domain: str) -> str | None:
    """The GitHub organisation for a company, or None.

    Searched rather than guessed from the name. "Datadog" publishes as `DataDog`
    and "Western Digital" as `westerndigitalcorporation`, so deriving the org
    from the company name would miss more than it found.
    """
    r = await client.get(
        f"{GITHUB_API}/search/users",
        params={"q": f"{company} type:org", "per_page": 5},
        headers=_headers(),
    )
    if r.status_code != 200:
        return None
    for item in (r.json().get("items") or [])[:5]:
        login = item.get("login")
        if not login:
            continue
        detail = await client.get(f"{GITHUB_API}/orgs/{login}", headers=_headers())
        if detail.status_code != 200:
            continue
        blog = (detail.json().get("blog") or "").lower()
        # Confirmed against the org's own stated website. Matching on name alone
        # picks up fan accounts and unrelated orgs with similar names.
        if domain and domain.lower() in blog:
            return login
    return None


async def harvest_github(
    client: httpx.AsyncClient, org: str, domain: str
) -> list[dict[str, Any]]:
    """Real addresses at `domain`, from public commit metadata in `org`."""
    repos = await client.get(
        f"{GITHUB_API}/orgs/{org}/repos",
        params={"sort": "pushed", "per_page": _REPOS_SCANNED},
        headers=_headers(),
    )
    if repos.status_code != 200:
        return []

    suffix = f"@{domain.lower()}"
    found: dict[str, str] = {}
    for repo in repos.json():
        # Forks carry the upstream project's committers, not this company's --
        # the first Lyft repo scanned was a fork and yielded nothing but
        # strangers' addresses.
        if repo.get("fork"):
            continue
        commits = await client.get(
            f"{GITHUB_API}/repos/{repo['full_name']}/commits",
            params={"per_page": _COMMITS_PER_REPO},
            headers=_headers(),
        )
        if commits.status_code != 200:
            continue
        for commit in commits.json():
            author = (commit.get("commit") or {}).get("author") or {}
            email = (author.get("email") or "").lower().strip()
            name = (author.get("name") or "").strip()
            if not usable(email, suffix):
                continue
            found.setdefault(email, name)

    return [
        {"email": email, "name": name, "source": "github", "verified": True,
         "confidence": 95,
         "why": "committed publicly from this address, so it exists"}
        for email, name in found.items()
    ]


# ------------------------------------------------------------------ patterns

_PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "firstl": lambda f, l: f"{f}{l[0]}",
    "first": lambda f, l: f,
    "last": lambda f, l: l,
}


def _name_parts(name: str) -> tuple[str, str] | None:
    """First and last name, keeping hyphens and single-letter given names.

    Splitting on every non-letter broke two real cases from live data. It cut
    "Jean-Philippe Bempel" into three parts and built `jean.bempel@`, where the
    real address is `jean-philippe.bempel@`. And dropping parts shorter than two
    characters discarded initials entirely, so "A Smith" produced no name at all.

    Splitting on whitespace instead keeps a hyphenated given name whole, while a
    single token — a username like "rkim-stripe" rather than a person's name —
    still correctly yields nothing to infer from.
    """
    tokens = [
        re.sub(r"[^a-z-]", "", token.lower()).strip("-")
        for token in (name or "").split()
    ]
    tokens = [t for t in tokens if t]
    return (tokens[0], tokens[-1]) if len(tokens) >= 2 else None


def detect_pattern(known: list[tuple[str, str]]) -> tuple[str | None, int]:
    """The address pattern a company uses, and how many samples agree.

    Returns (None, 0) rather than a best guess when the samples disagree. A
    company using two patterns cannot have one inferred for it, and picking the
    more common would produce an address nobody has.
    """
    votes: Counter = Counter()
    for name, email in known:
        parts = _name_parts(name)
        if not parts:
            continue
        first, last = parts
        local = email.split("@")[0].lower()
        for label, build in _PATTERNS.items():
            if local == build(first, last):
                votes[label] += 1
                break
    if not votes:
        return None, 0
    best, count = votes.most_common(1)[0]
    # One dissenting sample out of many is noise; an even split is not a pattern.
    if len(votes) > 1 and count <= sum(votes.values()) / 2:
        return None, 0
    return best, count


def apply_pattern(name: str, domain: str, pattern: str) -> str | None:
    parts = _name_parts(name)
    if not parts or pattern not in _PATTERNS:
        return None
    return f"{_PATTERNS[pattern](*parts)}@{domain}"


# ----------------------------------------------------------------- mx check


def accepts_mail(domain: str) -> bool | None:
    """Whether the domain publishes mail servers at all.

    Cheap, and it is the only part of "verification" that can be done honestly
    without a paid service. It proves the domain can receive mail — never that a
    particular mailbox exists, which is what an SMTP probe would claim and what
    most servers now refuse to answer.

    None when the lookup itself could not run, which is not the same as a domain
    that rejects mail.
    """
    try:
        done = subprocess.run(
            ["dig", "+short", "MX", domain], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return bool(done.stdout.strip())


# ------------------------------------------------------------------ the tool


async def harvest(company: str, domain: str,
                  known: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Everything findable for one company, each result carrying its provenance."""
    result: dict[str, Any] = {
        "company": company,
        "domain": domain,
        "acceptsMail": accepts_mail(domain),
        "org": None,
        "contacts": [],
        "pattern": None,
        "patternSamples": 0,
        "notes": [],
    }

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        org = await find_org(client, company, domain)
        result["org"] = org
        if org:
            result["contacts"] = await harvest_github(client, org, domain)
        else:
            result["notes"].append(
                "no GitHub organisation matched this company's own website"
            )

    # The pattern is learned from what was harvested first, because those are
    # this company's real addresses, and only then from contacts recorded
    # earlier. Hunter reported Stripe as `firstlast`; its own commits say most
    # of the company is plain `first`.
    samples = [(c["name"], c["email"]) for c in result["contacts"] if c.get("name")]
    samples += known or []
    pattern, count = detect_pattern(samples)
    result["pattern"], result["patternSamples"] = pattern, count
    if not pattern and samples:
        result["notes"].append("samples disagree — no single pattern to infer from")
    return result
