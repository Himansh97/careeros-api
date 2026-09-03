"""Hard eligibility gate — knockout criteria that no resume tailoring can fix.

This exists because scoring alone is dangerously misleading. A posting can
match a candidate's skills almost perfectly and still be impossible: a
Leidos Data Analyst role scored 95/100 on skills while requiring US
citizenship and a Secret clearance, which an F-1/OPT candidate cannot obtain.

Skill fit and eligibility are different questions. This module answers the
second one first, so an ineligible role is never presented as a top match.
"""
from __future__ import annotations

import re
from typing import Any

# Phrases that indicate a citizenship requirement. Matched case-insensitively
# against the job description.
CITIZENSHIP_PATTERNS = [
    r"u\.?s\.?\s+citizenship\s+(?:is\s+)?required",
    r"must\s+be\s+a\s+u\.?s\.?\s+citizen",
    r"united\s+states\s+citizenship\s+required",
    r"citizenship[:\s]+u\.?s\.?\s+citizenship\s+required",
    r"\bus\s+citizens?\s+only\b",
    r"must\s+be\s+authorized.{0,40}without\s+sponsorship",
]

# ITAR / EAR export-control language. This is a separate category because the
# wording rarely says "citizenship required" — it enumerates the statuses that
# qualify as a US person. F-1/OPT is not among them, so a candidate can read
# the paragraph and still miss that it excludes them.
EXPORT_CONTROL_PATTERNS = [
    r"\bitar\b",
    r"export\s+control",
    r"export\s+regulations",
    r"\bu\.?s\.?\s+person(?:s)?\s+(?:status|requirement|as\s+defined)",
    r"must\s+be\s+a?\s*\(?i\)?\s*u\.?s\.?\s+citizen\s+or\s+national",
    r"citizen\s+or\s+national.{0,80}permanent\s+resident",
    r"lawful\s+permanent\s+resident.{0,60}(?:refugee|asylee)",
    r"\bear\b\s+(?:regulations|controlled)",
]

CLEARANCE_PATTERNS = [
    r"\bsecret\s+clearance\b",
    r"\btop\s+secret\b",
    r"\bts/sci\b",
    r"security\s+clearance\s+(?:is\s+)?required",
    r"active\s+clearance",
    r"\bpublic\s+trust\s+clearance\b",
    r"ability\s+to\s+obtain\s+(?:a\s+)?(?:secret|security)\s+clearance",
]

NO_SPONSORSHIP_PATTERNS = [
    r"(?:will|do)\s+not\s+(?:provide|offer|sponsor).{0,30}sponsorship",
    r"no\s+(?:visa\s+)?sponsorship",
    r"unable\s+to\s+(?:provide|offer)\s+sponsorship",
    r"not\s+able\s+to\s+sponsor",
    r"without\s+(?:current\s+or\s+future\s+)?sponsorship",
    r"cannot\s+sponsor",
]


def _hits(text: str, patterns: list[str]) -> list[str]:
    found = []
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            # Return the surrounding phrase so the reason is checkable, not
            # just a boolean the candidate has to take on faith.
            start = max(0, m.start() - 60)
            found.append(text[start : m.end() + 60].strip().replace("\n", " "))
    return found


# Countries and cities that identify a posting as based outside the US. Kept to
# unambiguous names: a false positive here silently hides a job the candidate
# could have taken, which is worse than the noise it prevents.
_FOREIGN_MARKERS = (
    "united kingdom", "england", "scotland", "ireland", "germany", "france",
    "spain", "portugal", "netherlands", "belgium", "sweden", "norway",
    "denmark", "finland", "poland", "romania", "czech", "austria",
    "switzerland", "italy", "greece", "brazil", "mexico", "argentina",
    "colombia", "chile", "peru", "canada", "australia", "new zealand",
    "singapore", "japan", "china", "hong kong", "korea", "philippines",
    "vietnam", "thailand", "malaysia", "indonesia", "israel", "turkey",
    "south africa", "nigeria", "kenya", "egypt", "uae", "dubai",
    "london", "dublin", "berlin", "munich", "paris", "madrid", "barcelona",
    "amsterdam", "stockholm", "copenhagen", "warsaw", "prague", "vienna",
    "zurich", "milan", "são paulo", "sao paulo", "mexico city", "toronto",
    "vancouver", "montreal", "sydney", "melbourne", "tokyo", "bangalore",
    "bengaluru", "hyderabad", "mumbai", "pune", "chennai", "delhi", "noida",
    "gurgaon", "gurugram", "manila", "tel aviv",
)

# Signals that a posting is US-based even when a foreign marker also appears —
# "Remote, Canada; Remote, US" lists both, and a US option makes it viable.
# Postal abbreviations for the 50 states, DC and inhabited territories.
US_STATES = frozenset(
    """AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS
    MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI
    WY DC PR VI GU AS MP""".split()
)

# Countries and regions the marker list was missing. The gap was not academic:
# a Vercel "Remote - India" role reached the top of the daily feed at 88,
# because only the city "Bengaluru" was listed and never the country. Regional
# shorthands are included for the same reason — "EMEA" and "APAC" name places
# the candidate cannot work in as surely as a city does.
_FOREIGN_EXTRA = (
    "india", " uk", "u.k.", "pakistan", "bangladesh", "taiwan", "saudi",
    "qatar", "emirates", "uae", "emea", "apac", "latam", "anz",
)

_US_MARKERS = (
    "united states", " usa", " u.s.", "remote, us", "remote - us", "us remote",
    "remote (us", "anywhere in the us",
)


# The markers above name places; work rights are recorded per ISO country. This
# maps one to the other so a location the candidate may legally work in can be
# told apart from one they cannot. Only countries where a right could plausibly
# be held need an entry -- anything unmapped has no recorded right and is
# blocked, which is the safe direction.
_COUNTRY_OF_MARKER: dict[str, str] = {
    "india": "IN", "bengaluru": "IN", "bangalore": "IN", "mumbai": "IN",
    "hyderabad": "IN", "pune": "IN", "chennai": "IN", "delhi": "IN",
    "noida": "IN", "gurgaon": "IN", "gurugram": "IN", "kolkata": "IN",
    "ahmedabad": "IN", "pondicherry": "IN",
    "canada": "CA", "toronto": "CA", "vancouver": "CA",
    "united kingdom": "GB", " uk": "GB", "u.k.": "GB", "london": "GB",
}


def country_for(place: str) -> str | None:
    """The ISO code for a place name, when one is known."""
    low = (place or "").lower()
    for marker, code in _COUNTRY_OF_MARKER.items():
        if marker in low:
            return code
    return None


def may_work_in(profile: Any, place: str) -> bool:
    """Whether the candidate has a recorded, unrestricted right to work there.

    Absent means no, deliberately. A missing entry is "not recorded", and
    treating that as permission would turn a data gap into a green light on the
    one question where being wrong is expensive.
    """
    code = country_for(place)
    if not code:
        return False
    right = (getattr(profile, "work_rights", None) or {}).get(code) or {}
    return bool(right.get("unrestricted"))


def _foreign_location(job: dict[str, Any]) -> str | None:
    """The non-US place this role is based, or None.

    Only the location field is consulted. Descriptions mention offices and
    customers all over the world, so scanning them would flag almost everything.
    """
    location = (job.get("location") or "").lower()
    if not location or "not specified" in location:
        return None
    if any(m in location for m in _US_MARKERS):
        return None
    # A US state abbreviation or "remote" alone means it is reachable. The
    # abbreviation is checked against the actual list of states: a bare
    # two-letter-code pattern also matches ", UK", so "London, UK" was reading
    # as US-based and passing the gate.
    m = re.search(r",\s*([A-Z]{2})\b", job.get("location") or "")
    if m and m.group(1) in US_STATES:
        return None
    hit = next((m for m in (*_FOREIGN_MARKERS, *_FOREIGN_EXTRA) if m in location), None)
    return hit.title() if hit else None


def check_eligibility(job: dict[str, Any], profile: Any) -> dict[str, Any]:
    """Return a verdict plus the exact wording that triggered it."""
    desc = job.get("description", "") or ""
    auth = (getattr(profile, "work_authorization", "") or "").lower()

    # A candidate on a student/OPT visa is not a citizen and cannot hold or
    # obtain a US security clearance.
    non_citizen = any(
        k in auth for k in ("opt", "f-1", "f1", "h-1b", "h1b", "visa", "not a u.s. citizen")
    )

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # A role based outside the country the candidate is authorised to work in is
    # a hard blocker of exactly the same kind as ITAR — the work authorisation
    # simply does not reach it. The gate only read the description before, so a
    # Dublin/London People Analytics role scored 96 and led the shortlist, and a
    # São Paulo posting sat alongside it. F-1 OPT authorises work in the US only.
    # A non-US location is only a blocker where no right to work is recorded.
    # This rule used to reason from the US-shaped `work_authorization` string
    # alone, so it treated every country identically and ruled out all 127
    # India postings as ineligible while the candidate holds Indian
    # citizenship. It still blocks Dublin and Sao Paulo, because no right is
    # recorded for those.
    foreign = _foreign_location(job)
    if foreign and non_citizen and not may_work_in(profile, job.get("location") or foreign):
        blockers.append(
            {
                "type": "work_location",
                "detail": (
                    f"Role is based in {foreign}. F-1/OPT authorises employment "
                    "in the United States only, and no right to work in "
                    f"{foreign} is recorded."
                ),
                "quote": job.get("location", "") or foreign,
            }
        )

    citizenship = _hits(desc, CITIZENSHIP_PATTERNS)
    clearance = _hits(desc, CLEARANCE_PATTERNS)
    export_control = _hits(desc, EXPORT_CONTROL_PATTERNS)
    no_sponsor = _hits(desc, NO_SPONSORSHIP_PATTERNS)

    if export_control:
        entry = {
            "type": "export_control",
            "detail": (
                "Posting is subject to ITAR/export-control rules, which limit "
                "eligibility to US citizens, permanent residents, refugees or "
                "asylees. F-1/OPT does not qualify."
            ),
            "quote": export_control[0][:220],
        }
        (blockers if non_citizen else warnings).append(entry)

    if citizenship:
        entry = {
            "type": "citizenship",
            "detail": "Posting requires U.S. citizenship.",
            "quote": citizenship[0][:220],
        }
        (blockers if non_citizen else warnings).append(entry)

    if clearance:
        entry = {
            "type": "security_clearance",
            "detail": (
                "Posting requires a security clearance, which requires U.S. "
                "citizenship to obtain."
            ),
            "quote": clearance[0][:220],
        }
        (blockers if non_citizen else warnings).append(entry)

    if no_sponsor and non_citizen:
        warnings.append(
            {
                "type": "no_sponsorship",
                "detail": (
                    "Employer states they will not sponsor. Workable on OPT today, "
                    "but this role has no path once OPT expires."
                ),
                "quote": no_sponsor[0][:220],
            }
        )

    if blockers:
        verdict = "INELIGIBLE"
    elif warnings:
        verdict = "REVIEW_REQUIRED"
    else:
        verdict = "ELIGIBLE"

    return {
        "verdict": verdict,
        "blockers": blockers,
        "warnings": warnings,
        "workAuthorization": getattr(profile, "work_authorization", ""),
    }
