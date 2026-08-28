"""The only place that submits an application to an employer.

This module ends a rule CareerOS was built around. Everything else here stages
work for the candidate to send themselves — `ready` meant a tailored resume and
an approval waiting for a human, and the Gmail path deliberately stops at an
unsent draft. Tsenta submits. That was decided explicitly on 2026-08-25, and
this module exists so the decision has exactly one implementation rather than
leaking into every caller that fancies applying to something.

Why the choke point matters more here than for the model
--------------------------------------------------------
`llm.py` funnels every Anthropic call so the spend ledger cannot develop a
hole. The stake here is higher: a model call that escapes the ledger costs
money, but an application that escapes this function is *sent to a real
employer under the candidate's name and cannot be recalled*. There is no
delete, no undo, and no second first impression. So every path to Tsenta runs
through `submit()`, and `submit()` refuses before it spends.

The eligibility gate is inside, not around
------------------------------------------
Three applications have already gone to roles the candidate cannot take —
Stripe Dublin/London and Exadel São Paulo (F-1 OPT confers no right to work
abroad) and Friedkin (states it will not sponsor). That happened at human
speed, before the location rule existed. Tsenta submits in two to three
seconds against a 35-role pipeline, which turns a mistake anyone could catch
into a mistake nobody can.

So `check_eligibility` is called *in here*, and a verdict that is not ELIGIBLE
refuses the submission. A caller cannot skip it by forgetting to ask, because
there is no code path that submits without passing through this check first.
REVIEW_REQUIRED refuses too: it means the posting says something about
sponsorship or authorization that nobody has read yet, and "nobody has read it
yet" is not a state to auto-apply from.

`force=True` exists for the candidate overriding their own gate through the UI.
It is threaded from an explicit human action and is never set by automation.

Duplicates are refused locally, not just remotely
-------------------------------------------------
Tsenta returns 409 `duplicate_application` for a posting this candidate already
applied to, which is a good backstop and a bad primary defence — it only knows
about applications *it* sent. The six outreach emails this pipeline sent by
hand in August are invisible to it. So an application already carrying a
`submitted_at` is refused before the network is touched.

It sends CareerOS's document, or it does not send
------------------------------------------------
The first version posted `{"profile_id": ..., "url": ...}` and nothing else, so
Tsenta attached *its own* stored resume and put it through *its own* rewriter.
Everything upstream — scoring, tailoring, the containment gate, the recruiter
audit, the ATS-safety re-extraction in `resume_qa.check_pdf` — was bypassed at
the one moment it exists for. Twenty-five applications went out that way before
anyone read the payload.

So `resume_pdf` is a required argument, and `submit()` refuses without it rather
than falling back. A fallback here is not a degraded mode, it is the failure
wearing a different name: it looks like a successful application and is one the
candidate never approved. Tsenta attaches a staged PDF verbatim — no
optimisation, no re-render — which is the only reason this is safe to delegate
at all.

The form fields are a separate problem from the document. Tsenta fills them from
the profile JSON, not by reading the PDF, so `profile_id` still travels and the
answers still have to reach it — see `review()`.

Duplicates are refused by role, not by row
------------------------------------------
Fivetran posted the same Senior Product Manager req twice on Greenhouse under
two ids. One row is `submitted`, the other still sits at `ready`, and a
row-level check sees two unrelated applications. `canonical_role_key` collapses
them so the second one is refused before the network is touched.

Failure is honest and costs nothing
-----------------------------------
Absent key, no credit, refused gate: `Submission` comes back with `ok=False`
and a reason, and nothing raises. Tsenta does not charge for a failed
application, and a refusal never reaches Tsenta at all.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import HTTP_TIMEOUT_SECONDS, tsenta_key
from .eligibility import check_eligibility

logger = logging.getLogger(__name__)

_API = "https://api.autojobs.me/v1"

# Terminal and waiting states as Tsenta reports them. `needs_review` and
# `needs_otp` are the two that hand control back to a human: the first when the
# account has review-before-submit switched on or Tsenta is unsure of a field,
# the second when the ATS wants a code from the candidate's email. Neither is a
# failure, and neither may be treated as "sent".
# Tsenta's stated ceiling for a staged resume.
MAX_RESUME_BYTES = 5_000_000

PENDING = ("queued", "running")
AWAITING_HUMAN = ("needs_review", "needs_otp")
SUBMITTED = "submitted"
FAILED = "failed"


@dataclass(frozen=True)
class Submission:
    """The result of asking Tsenta to apply. `ok` means Tsenta accepted it.

    `ok=True` with `status="needs_review"` is the common surprise: the request
    succeeded and the application has *not* been sent. Callers must read
    `status`, never infer sending from `ok`.
    """

    ok: bool
    status: str = ""
    application_id: str = ""
    ats: str = ""
    reason: str = ""
    price_usd: float = 0.0

    @property
    def sent(self) -> bool:
        """Whether this actually reached the employer."""
        return self.ok and self.status == SUBMITTED


def available() -> tuple[bool, str]:
    """Whether a submission would be attempted, and why not when it would not."""
    if not tsenta_key():
        return False, "no TSENTA_API_KEY configured"
    return True, ""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tsenta_key()}",
        "Content-Type": "application/json",
    }


def _error_reason(resp: httpx.Response) -> str:
    """Tsenta's error code, in words, without leaking the response wholesale."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
        return f"HTTP {resp.status_code}"
    code = (body.get("error") or {}).get("code") or body.get("code") or ""
    known = {
        "unauthorized": "the Tsenta API key was rejected",
        "insufficient_credit": "the Tsenta account is out of credit",
        "rate_limited": "Tsenta is rate limiting; try again shortly",
        "duplicate_application": "Tsenta has already applied to this posting",
        "invalid_request": "Tsenta rejected the candidate profile as incomplete",
        "not_found": "Tsenta does not know that profile or application",
        "invalid_state": "that application is not in a state allowing this",
    }
    return known.get(code) or f"{code or 'error'} (HTTP {resp.status_code})"


_PUNCT = re.compile(r"[^a-z0-9]+")

# Suffixes a job board appends to one req when it lists it in several places or
# reposts it. They change the id and not the job, which is what defeats a
# row-level duplicate check.
_NOISE = re.compile(
    r"\b(?:remote|hybrid|onsite|on site|contract|full time|part time|"
    r"[0-9]{4}|i{1,3}|iv|v|jr|sr|senior|junior|and|the|of|for)\b"
)


def _applied_roles() -> set[str]:
    """Imported lazily: `store` imports this module for `canonical_role_key`."""
    from .store import applied_role_keys as lookup

    return lookup()


def canonical_role_key(company: str, title: str) -> str:
    """One key for one req, however many times it was posted.

    Deliberately conservative about what it strips. Collapsing too much merges
    genuinely different roles — Target currently has eight distinct analyst
    postings that share almost every word — and a false merge silently hides a
    role the candidate wanted. A false split only costs the duplicate check,
    which Tsenta's own 409 still backstops.

    Seniority words are dropped because the same req is routinely listed as both
    "Senior Data Analyst" and "Data Analyst"; level lives in the posting, not in
    whether it is the same job.
    """
    def norm(text: str) -> str:
        cleaned = _NOISE.sub(" ", _PUNCT.sub(" ", (text or "").lower()))
        return " ".join(cleaned.split())

    return f"{norm(company)}|{norm(title)}"


def check_resume_bytes(pdf: bytes) -> str:
    """Why these bytes cannot be sent, or "" if they can.

    Separate from `stage_resume` because this is a local fact and belongs with
    the other local refusals, ahead of the key check. A missing resume is a
    CareerOS bug; discovering it only after the key is configured would hide it
    on exactly the machines where it matters.
    """
    if not pdf:
        return "no resume PDF was produced for this job"
    if not pdf.startswith(b"%PDF"):
        return "the resume bytes are not a PDF"
    if len(pdf) > MAX_RESUME_BYTES:
        return f"the resume PDF is over {MAX_RESUME_BYTES // 1_000_000}MB"
    return ""


def stage_resume(pdf: bytes) -> tuple[str, str]:
    """Put the tailored PDF where Tsenta can attach it. Returns (upload_id, why_not).

    Staging is separate from applying so a failure here costs nothing: no
    credit is spent, no employer is contacted, and the caller gets a reason
    rather than an application filed with the wrong document.
    """
    bad = check_resume_bytes(pdf)
    if bad:
        return "", bad

    ready, why = available()
    if not ready:
        return "", why

    try:
        resp = httpx.post(
            f"{_API}/resumes",
            headers={"Authorization": f"Bearer {tsenta_key()}"},
            files={"file": ("resume.pdf", pdf, "application/pdf")},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tsenta: resume staging failed — %s", type(exc).__name__)
        return "", f"could not reach Tsenta ({type(exc).__name__})"

    if resp.status_code >= 400:
        return "", _error_reason(resp)

    body = resp.json()
    upload_id = str(body.get("upload_id") or body.get("id") or "")
    return (upload_id, "") if upload_id else ("", "Tsenta staged the resume but returned no id")


def submit(
    job: dict[str, Any],
    profile: Any,
    *,
    profile_id: str,
    resume_pdf: bytes,
    already_submitted: bool = False,
    force: bool = False,
    cover_letter: str = "",
    applied_role_keys: set[str] | None = None,
) -> Submission:
    """Apply to one posting, with CareerOS's own resume. The only function that can.

    `resume_pdf` is required and has no default. Tsenta will happily attach its
    own stored resume if none is supplied, which produces a successful-looking
    application carrying a document the candidate never approved — so this
    refuses instead. See the module docstring.

    `already_submitted` is the row-level guard and should carry the
    application's `submitted_at`; `applied_role_keys` is the role-level one and
    is looked up when not supplied. `force` is the candidate overriding their own
    eligibility gate by hand; automation must never pass it.
    """
    url = (job.get("applyUrl") or job.get("url") or "").strip()
    if not url:
        return Submission(False, reason="that job has no application URL")

    if already_submitted and not force:
        return Submission(
            False,
            reason="CareerOS already records this as submitted; refusing to send it twice",
        )

    # By role, not by row. Two ids for one req is the common shape, and the
    # row-level check above cannot see it.
    if not force:
        # `company` is a dict from the sources layer and a string from the
        # applications table; both reach this function.
        company = job.get("company") or ""
        key = canonical_role_key(
            company.get("name", "") if isinstance(company, dict) else str(company),
            str(job.get("title") or ""),
        )
        try:
            known = applied_role_keys if applied_role_keys is not None else _applied_roles()
        except Exception as exc:  # noqa: BLE001
            # Fail closed. Not knowing whether this was already sent is not a
            # reason to send it — the action cannot be undone.
            logger.warning("tsenta: duplicate lookup failed — %s", type(exc).__name__)
            return Submission(
                False,
                reason="could not check whether this role was already applied to, "
                       "so it was not sent",
            )
        if key in known:
            return Submission(
                False,
                reason="this role was already applied to under a different posting "
                       "id; refusing to send it twice",
            )

    bad_resume = check_resume_bytes(resume_pdf)
    if bad_resume:
        return Submission(False, reason=f"the tailored resume was not staged: {bad_resume}")

    verdict = check_eligibility(job, profile).get("verdict")
    if verdict != "ELIGIBLE" and not force:
        return Submission(
            False,
            reason=(
                f"eligibility says {verdict}: refusing to submit. This is the "
                "check that the Stripe Dublin and Exadel São Paulo applications "
                "predate."
            ),
        )

    ready, why = available()
    if not ready:
        return Submission(False, reason=why)

    upload_id, why_not = stage_resume(resume_pdf)
    if not upload_id:
        return Submission(False, reason=f"the tailored resume was not staged: {why_not}")

    payload: dict[str, Any] = {
        "profile_id": profile_id,
        "url": url,
        # The whole point. Tsenta attaches this verbatim rather than
        # substituting the resume it holds.
        "resume_upload_id": upload_id,
        # Pause at the filled form so CareerOS's own answers can be written in
        # before it goes. This is the machine reviewing, not the candidate.
        "review_before_submit": True,
    }
    if cover_letter.strip():
        payload["cover_letter"] = {"text": cover_letter.strip()}
    try:
        resp = httpx.post(
            f"{_API}/applications",
            headers=_headers(),
            json=payload,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a failed submit must not break the app
        logger.warning("tsenta: submit failed — %s", type(exc).__name__)
        return Submission(False, reason=f"could not reach Tsenta ({type(exc).__name__})")

    if resp.status_code >= 400:
        return Submission(False, reason=_error_reason(resp))

    body = resp.json()
    return Submission(
        ok=True,
        status=str(body.get("status") or ""),
        application_id=str(body.get("id") or ""),
        ats=str(body.get("ats") or ""),
        price_usd=float(body.get("price_usd") or 0.0),
    )


def status(application_id: str) -> Submission:
    """Where one Tsenta application has got to. Read-only."""
    ready, why = available()
    if not ready:
        return Submission(False, reason=why)

    try:
        resp = httpx.get(
            f"{_API}/applications/{application_id}",
            headers=_headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        return Submission(False, reason=f"could not reach Tsenta ({type(exc).__name__})")

    if resp.status_code >= 400:
        return Submission(False, reason=_error_reason(resp))

    body = resp.json()
    return Submission(
        ok=True,
        status=str(body.get("status") or ""),
        application_id=str(body.get("id") or ""),
        ats=str(body.get("ats") or ""),
        reason=str(body.get("failure_reason") or ""),
        price_usd=float(body.get("price_usd") or 0.0),
    )


def review(application_id: str, *, approve: bool, note: str = "") -> Submission:
    """Release or kill an application sitting at `needs_review`.

    This is the human checkpoint. `approve=True` sends it to the employer, so
    this function is as irreversible as `submit()` and must only ever be
    reached from an explicit candidate action.
    """
    ready, why = available()
    if not ready:
        return Submission(False, reason=why)

    try:
        resp = httpx.post(
            f"{_API}/applications/{application_id}/review",
            headers=_headers(),
            json={"decision": "approve" if approve else "reject", "note": note},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        return Submission(False, reason=f"could not reach Tsenta ({type(exc).__name__})")

    if resp.status_code >= 400:
        return Submission(False, reason=_error_reason(resp))

    body = resp.json()
    return Submission(
        ok=True,
        status=str(body.get("status") or ""),
        application_id=str(body.get("id") or ""),
        reason=str(body.get("failure_reason") or ""),
    )
