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

Failure is honest and costs nothing
-----------------------------------
Absent key, no credit, refused gate: `Submission` comes back with `ok=False`
and a reason, and nothing raises. Tsenta does not charge for a failed
application, and a refusal never reaches Tsenta at all.
"""
from __future__ import annotations

import logging
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


def submit(
    job: dict[str, Any],
    profile: Any,
    *,
    profile_id: str,
    already_submitted: bool = False,
    force: bool = False,
) -> Submission:
    """Apply to one posting. The only function in this codebase that can.

    `already_submitted` is the local duplicate guard and should carry the
    application's `submitted_at`. `force` is the candidate overriding their own
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

    payload = {"profile_id": profile_id, "url": url}
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
