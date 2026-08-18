"""CareerOS API — live job discovery, evidence-based scoring, tailoring, outreach."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from .config import ALLOWED_ORIGINS, GREENHOUSE_COMPANIES, SCORE_BUDGET
from .contacts import (
    company_domain,
    get_contact,
    lookup_contacts,
    resolve_domain_by_company,
    hunter_key,
    list_contacts,
    save_contact,
    set_contact_status,
)
from .providers import configured_providers
from .eligibility import _foreign_location, check_eligibility
from .discovery import add_to_cache, failed_sources, fetch_all_jobs, filter_jobs, source_counts
from .outreach import build_outreach
from .profile import ProfileNotFound, load_profile
from .scoring import score_job_cached as score_job
from .store import (
    add_approval,
    advance,
    get_application,
    list_applications,
    list_approvals,
    resolve_approval,
    set_resume_score,
    upsert_application,
)
from .priority import priority
from .skills import classify_posting
from .tailor import tailor_resume
from .recruiter_messages import (
    approve_draft,
    dismiss_draft,
    get_message,
    list_messages,
    retry_draft,
    update_draft,
)

app = FastAPI(title="CareerOS API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _profile():
    try:
        return load_profile()
    except ProfileNotFound as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Candidate profile not available: {exc}",
        ) from exc


async def _job_or_404(job_id: str) -> dict[str, Any]:
    jobs = await fetch_all_jobs()
    for j in jobs:
        if j["id"] == job_id:
            return j
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/api/usage")
async def usage_report(days: int = 30) -> dict[str, Any]:
    """What the resume writer has spent, and what is left of the budget.

    Cost is computed here from the published per-token rates rather than read
    back from Anthropic, so it tracks the invoice closely without being it. The
    Console remains the authority on what you are actually billed.
    """
    from .usage import summary

    return summary(days)


@app.get("/api/usage/budget")
async def usage_budget() -> dict[str, Any]:
    """Just the caps and what is left — cheap enough to poll."""
    from .usage import budget_state

    return budget_state()


@app.get("/api/skywatch")
async def skywatch_feed() -> dict[str, Any]:
    """Live sky: near-Earth approaches, geomagnetic activity, ISS position.

    Public sources, no API keys, cached per-feed by how fast each one actually
    changes. A feed that fails is named in `failures` and omitted rather than
    served stale, because the entire value of putting live data on a page is
    that it is live.
    """
    from .skywatch import skywatch

    return await skywatch()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sources": ["Greenhouse", "Ashby", "Lever", "The Muse", "Arbeitnow", "RemoteOK"],
        "greenhouseCompanies": GREENHOUSE_COMPANIES,
        "lastFetchCounts": source_counts(),
        # Named so a degraded search is visible. A failed source returns no
        # jobs, which otherwise reads as an employer with no openings.
        "failedSources": failed_sources(),
        "contactLookup": {
            "providers": configured_providers(),
            "enabled": any(p["configured"] for p in configured_providers()),
            "note": (
                "Providers are tried in order and fail over automatically when a "
                "quota is exhausted or an account is restricted. Manual contact "
                "entry always works with no key at all."
            ),
        },
        "notCovered": {
            "linkedin": (
                "No public jobs API; their terms prohibit automated access even "
                "to publicly rendered pages. Not implemented by choice."
            ),
            "indeed": "No free public search API available to a server.",
        },
    }


@app.get("/api/profile")
async def profile() -> dict[str, Any]:
    p = _profile()
    return {
        "name": p.name,
        "email": p.email,
        "phone": p.phone,
        "location": p.location,
        "linkedinUrl": p.linkedin_url,
        "workAuthorization": p.work_authorization,
        "education": p.education,
        "certifications": p.certifications,
        "skillsInventory": p.skills_inventory,
        "employmentHistory": p.employment_history,
        "preferences": p.preferences,
        "applicationAnswers": p.application_answers,
        "evidence": [
            {
                "id": c.claim_id,
                "employer": c.employer,
                "claim": c.claim,
                "skills": c.skills,
                "industry": c.industry,
                "dateRange": c.date_range,
                "classification": c.classification,
                "approvedForResume": c.approved_for_resume,
                "source": c.source,
            }
            for c in p.evidence
        ],
    }


class SearchRequest(BaseModel):
    query: str | None = None
    location: str | None = None
    workArrangements: list[str] | None = None
    minimumFit: int | None = None
    limit: int = 40
    # "fresh" | "fit" | "newest". See the sort in `search` for why fresh is
    # the default rather than newest.
    sort: str = "fresh"


@app.post("/api/jobs/search")
async def search(req: SearchRequest) -> dict[str, Any]:
    p = _profile()
    all_jobs = await fetch_all_jobs()
    matched = filter_jobs(all_jobs, req.query, req.location, req.workArrangements)

    stamped = datetime.now(timezone.utc).isoformat()
    # One query for the whole pipeline rather than a lookup per job — search
    # scores dozens of jobs and each would otherwise open its own connection.
    from .store import job_flags, list_applications

    by_job = {a["jobId"]: a for a in list_applications()}
    flags = job_flags()

    # Dismissed jobs are dropped before prescreening, not after. The scoring
    # budget is the scarce resource here — leaving them in would let rejected
    # postings keep consuming slots that a real candidate could have used.
    matched = [j for j in matched if not flags.get(j["id"], {}).get("dismissed")]

    # Full scoring parses whole descriptions, so it can't run on every job the
    # sources return. It used to run on the first 120 in FETCH order, which
    # meant the result was sorted by fit but chosen arbitrarily — a 98-scoring
    # role at position 452 was invisible while sales roles led the list.
    # Pre-rank on titles first (cheap), then deep-score the best candidates.
    from .prescreen import rank_for_scoring

    candidates, set_aside = rank_for_scoring(matched, p, SCORE_BUDGET)

    scored: list[dict[str, Any]] = []
    for job in candidates:
        s = score_job(job, p)
        if req.minimumFit is not None and s["rawFitScore"] < req.minimumFit:
            continue
        record = by_job.get(job["id"])
        scored.append(
            {
                **job,
                **s,
                "discoveredAt": stamped,
                "applicationStatus": record["status"] if record else "discovered",
                "resumeScore": record.get("resumeScore") if record else None,
                "saved": flags.get(job["id"], {}).get("saved", False),
                "dismissed": False,
                # "pasted" (the candidate added it) vs "fetched" (discovery
                # found it). Defaulted rather than assumed: a job served from a
                # cache written before this field existed has no origin, and
                # calling it "pasted" would misattribute the whole daily haul.
                "origin": job.get("origin", "fetched"),
                "matchScope": job.get("matchScope", "title"),
                # What is worth doing next, which is not the same question as
                # where the candidate is strongest. Contains no probability —
                # see priority.py for why.
                "priority": priority(job, s["rawFitScore"], True),
            }
        )

    # Ordering.
    #
    # This used to sort on fit alone, so a strong match from five weeks ago sat
    # above an equally strong one posted this morning — and the older req has
    # already accumulated a stack of applications the newer one has not. That
    # is a real difference in queue position, not a prediction about outcome.
    #
    # `newest` is available and is deliberately not the default: sorting purely
    # by date puts a 42-fit posting from today above a 96-fit posting from last
    # week, which is a worse list. `fresh` multiplies fit by the freshness
    # factor from priority.py, so recency decides between comparable matches
    # and never rescues a weak one — a 96 at three weeks old (0.78) still
    # outranks a 70 posted today.
    def _age(job: dict[str, Any]) -> float:
        days = (job.get("priority") or {}).get("freshness", {}).get("days")
        return 1e9 if days is None else float(days)

    # A title match beats a description match regardless of the sort mode: the
    # words the candidate typed meant the role, not "mentioned somewhere".
    scope_rank = {"title": 0, "company": 1, "description": 2}

    def _scope_of(job: dict[str, Any]) -> int:
        return scope_rank.get(job.get("matchScope", "title"), 0)

    if req.sort == "newest":
        scored.sort(key=lambda j: (_scope_of(j), _age(j), -j["rawFitScore"]))
    elif req.sort == "fit":
        scored.sort(key=lambda j: (_scope_of(j), -j["rawFitScore"]))
    else:
        scored.sort(
            key=lambda j: (
                _scope_of(j),
                -(
                    j["rawFitScore"]
                    * (j.get("priority") or {}).get("freshness", {}).get("factor", 0.85)
                ),
            )
        )
    return {
        "jobs": scored[: req.limit],
        "total": len(matched),
        "scored": len(scored),
        # Say plainly how much of the pool was actually evaluated. A ranked
        # list built from a subset reads as "these are the best matches" when
        # it is really "the best of what was looked at" — the UI needs to be
        # able to tell the difference.
        "setAside": set_aside,
        "sources": sorted({j["source"] for j in matched}) or ["Greenhouse"],
        "sort": req.sort,
    }


class JobFlagRequest(BaseModel):
    # The desired state, not a toggle. The UI updates optimistically, so two
    # fast clicks would race a toggle into the wrong value; sending the state
    # the user asked for makes the call idempotent and replayable.
    value: bool = True


@app.post("/api/jobs/{job_id}/save")
async def job_save(job_id: str, req: JobFlagRequest) -> dict[str, Any]:
    from .store import set_job_flag

    return {"jobId": job_id, **set_job_flag(job_id, saved=req.value)}


@app.post("/api/jobs/{job_id}/dismiss")
async def job_dismiss(job_id: str, req: JobFlagRequest) -> dict[str, Any]:
    from .store import set_job_flag

    return {"jobId": job_id, **set_job_flag(job_id, dismissed=req.value)}


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str) -> dict[str, Any]:
    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    # Report the real pipeline state, not a hardcoded "discovered". Both of
    # these were fixed values, so a job that had already been tailored still
    # came back with no resumeScore and status "discovered" — which made the
    # UI offer "Tailor Resume" forever and silently re-run it on every click.
    from .store import get_application, job_flags

    record = get_application(f"app_{job_id}")
    flag = job_flags().get(job_id, {})
    return {
        **job,
        **s,
        "discoveredAt": datetime.now(timezone.utc).isoformat(),
        "applicationStatus": record["status"] if record else "discovered",
        "resumeScore": record.get("resumeScore") if record else None,
        "saved": flag.get("saved", False),
        "dismissed": flag.get("dismissed", False),
        # What the posting screens on, versus what it merely says. A candidate
        # who reads "5+ years" and "fast-paced environment" as hard bars
        # withdraws from roles they can do.
        "posting": classify_posting(job.get("description", ""), job.get("title", "")),
        "priority": priority(job, s["rawFitScore"], True),
    }


@app.post("/api/jobs/{job_id}/tailor")
async def tailor(job_id: str) -> dict[str, Any]:
    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    resume = tailor_resume(job, s, p)
    resume["updatedAt"] = datetime.now(timezone.utc).isoformat()

    from .resume_qa import check_resume

    resume["qaFindings"] = check_resume(resume, p)

    record = upsert_application(job, s)
    # Re-tailoring a job the candidate has already acted on must not put it
    # back in the approval queue. Opening a resume to re-read it is not a
    # request to apply again, and this endpoint is hit by ordinary browsing.
    from .automation import _COMMITTED_STATUSES

    already_acted = (record or {}).get("status") in _COMMITTED_STATUSES
    if record:
        set_resume_score(record["id"], resume["resumeScore"])
    if record and not already_acted:
        add_approval(
            "application",
            job_id,
            {
                "jobTitle": job["title"],
                "companyName": job["company"]["name"],
                "title": "Ready to apply",
                "whatCareerOSWantsToDo": (
                    f"A tailored resume (score {resume['resumeScore']}) is ready for "
                    f"{job['title']} at {job['company']['name']}. Open the posting to "
                    f"apply — CareerOS does not submit on your behalf."
                ),
                "whyApprovalRequired": (
                    "CareerOS never submits an application. It prepares everything "
                    "and stops here so you review and apply yourself."
                ),
                "rawFitScore": s["rawFitScore"],
                "resumeScore": resume["resumeScore"],
                "applyUrl": job.get("applyUrl"),
            },
        )
    return resume


@app.put("/api/jobs/{job_id}/resume/bullets/{claim_id}")
async def edit_bullet(job_id: str, claim_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Edit one bullet on the tailored resume.

    The edit is checked against the claim it derives from, but a candidate
    editing their own history is never blocked — see overrides.save_override.
    Anything the evidence file can't support comes back as `warnings` and is
    marked unverified on the resume rather than silently accepted as sourced.
    """
    from .overrides import save_override

    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    p = _profile()
    claim = next((c for c in p.evidence if c.claim_id == claim_id), None)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"no evidence claim '{claim_id}'")

    result = save_override(
        job_id, claim_id, text, claim.claim,
        rationale=(body.get("rationale") or "").strip(),
        author="user",
        # The claim's recorded verb is the authoritative ceiling. Without it
        # the check re-derives one from the claim text, which is the same
        # answer most of the time and wrong exactly when the claim was edited.
        seniority_ceiling=claim.seniority_verb,
    )
    return {**result, "original": claim.claim}


@app.delete("/api/jobs/{job_id}/resume/bullets/{claim_id}")
async def revert_bullet(job_id: str, claim_id: str) -> dict[str, Any]:
    """Revert one bullet to its generated wording."""
    from .overrides import clear_override

    clear_override(job_id, claim_id)
    return {"ok": True, "jobId": job_id, "claimId": claim_id}


@app.put("/api/jobs/{job_id}/resume/{field}")
async def edit_document_field(job_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    """Edit the summary or headline for one job's resume."""
    from .overrides import EDITABLE_FIELDS, save_document_edit

    if field not in EDITABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"field must be one of {', '.join(EDITABLE_FIELDS)}",
        )
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return save_document_edit(job_id, field, text)


@app.delete("/api/jobs/{job_id}/resume/edits")
async def reset_resume_edits(job_id: str, scope: str = "user") -> dict[str, Any]:
    """Undo edits for this job.

    Defaults to the candidate's own edits, keeping the tailored resume.
    `?scope=all` also discards the hand-tailored layer.
    """
    from .overrides import reset_edits

    if scope not in {"user", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'user' or 'all'")
    reset_edits(job_id, scope)
    return {"ok": True, "jobId": job_id, "scope": scope}


@app.get("/api/jobs/{job_id}/resume.{fmt}")
async def resume_document(job_id: str, fmt: str, download: bool = False):
    """Download the tailored resume as an ATS-friendly PDF or DOCX."""
    from fastapi.responses import Response

    from .documents import build_docx, build_pdf, safe_filename

    if fmt not in {"pdf", "docx"}:
        raise HTTPException(status_code=400, detail="format must be pdf or docx")

    p = _profile()
    job = await _job_or_404(job_id)
    resume = tailor_resume(job, score_job(job, p), p)

    stem = safe_filename(f"{p.name}_{job['company']['name']}_{job['title']}")
    if fmt == "pdf":
        data = build_pdf(resume, p)
        media = "application/pdf"

        # Every exported PDF is validated for ATS readability. A failure is
        # logged rather than raised — the candidate still gets their file,
        # but the problem is not silent.
        from .resume_qa import check_pdf

        for issue in check_pdf(data, p):
            if issue["severity"] == "high":
                print(f"[ATS] {issue['type']}: {issue['detail']}")
    else:
        data = build_docx(resume, p)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    # `inline` by default so the in-app preview can render the real document in
    # an iframe rather than triggering a download. A preview built from
    # anything other than these exact bytes could disagree with what the
    # employer receives, which is the one thing it must never do. `?download=1`
    # restores the attachment behaviour for the export buttons.
    #
    # DOCX has no browser renderer, so it always downloads.
    inline = fmt == "pdf" and not download
    disposition = "inline" if inline else "attachment"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'{disposition}; filename="{stem}.{fmt}"'},
    )


@app.post("/api/jobs/{job_id}/outreach")
async def outreach(job_id: str) -> dict[str, Any]:
    from .outreach_store import upsert_outreach

    from .outreach import pick_contact

    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)

    # Find a real addressee before drafting. Contact lookup already existed and
    # worked, but only behind its own endpoint — this one never called it, so
    # every draft was generated unaddressed and carried a note claiming no
    # recruiter could be found while /contacts returned verified ones.
    contact = None
    domain = company_domain(job["company"]["name"], job.get("applyUrl", ""))
    if not domain:
        # ATS- and board-hosted postings expose no employer domain, which left
        # most applications with no addressee at all. Hunter can resolve the
        # company name to a domain, and only its answer is trusted — never a
        # pattern guessed from the name.
        domain = await resolve_domain_by_company(job["company"]["name"])
    if domain:
        found = await lookup_contacts(domain)
        contact = pick_contact(found.get("contacts") or [])
        if contact:
            # Persist so the Contacts page and the outreach record can both
            # reference the same person rather than each holding a loose copy.
            contact = save_contact(
                {**contact, "jobId": job_id, "company": job["company"]["name"]}
            ) or contact

    draft = build_outreach(job, s, p, contact)

    # Persist so the Outreach page and follow-up scheduling have something to
    # work from — generating a draft and forgetting it makes both useless.
    record = upsert_outreach(
        {
            "jobId": job_id,
            "company": job["company"]["name"],
            "jobTitle": job["title"],
            "contactId": (contact or {}).get("id"),
            "emailSubject": draft["emailSubject"],
            "emailDraft": draft["emailDraft"],
            "linkedinDraft": draft["linkedinDraft"],
        }
    )
    return {**draft, "outreachId": record.get("id"), "status": record.get("status")}



async def _draft_outreach_once(job_id: str) -> dict[str, Any]:
    """Draft outreach for a job, unless there is already something there.

    Called when a resume is approved: approval is a deliberate act on one job,
    which makes spending a provider credit proportionate in a way an autopilot
    sweep over thousands of postings never was. That is why autopilot still
    refuses to do this and approval is allowed to.

    Guarded, because `upsert_outreach` overwrites. Re-approving a job whose
    draft has been edited — or already sent — would destroy that work and spend
    a second credit for the privilege. Existing outreach is left alone and
    reported as such.
    """
    from .outreach_store import get_outreach

    existing = get_outreach(f"o_{job_id}")
    if existing:
        return {
            "drafted": False,
            "reason": "already_exists",
            "status": existing.get("status"),
            "detail": (
                f"Outreach for this job already exists ({existing.get('status')}) "
                "and was left untouched."
            ),
        }
    try:
        record = await outreach(job_id)
    except HTTPException as exc:
        # A failed lookup must never fail the approval. The candidate approved a
        # resume; that decision stands whether or not an address could be found.
        return {"drafted": False, "reason": "lookup_failed", "detail": str(exc.detail)[:160]}
    return {
        "drafted": True,
        "outreachId": record.get("outreachId"),
        "detail": "Email and LinkedIn message drafted. Nothing was sent.",
    }


@app.post("/api/jobs/{job_id}/resume/approve")
async def approve_resume(job_id: str) -> dict[str, Any]:
    """Approve the tailored resume, and start the recruiter research.

    The Approve button on the resume page was pure local state — it flipped a
    boolean and showed a toast saying the feature was not connected. So both
    approval paths now land here: the queue and the resume page mean the same
    thing and do the same thing.

    Approving does not submit anything. It records the decision and drafts the
    outreach, which still waits for the candidate to send it.
    """
    from .store import get_application

    await _job_or_404(job_id)
    approval_id = f"appr_application_{job_id}"
    resolve_approval(approval_id, "approved")

    record = get_application(f"app_{job_id}")
    if record:
        advance(f"app_{job_id}", "ready", "Resume approved by candidate")

    return {
        "jobId": job_id,
        "approved": True,
        "outreach": await _draft_outreach_once(job_id),
    }


@app.get("/api/applications")
async def applications() -> dict[str, Any]:
    return {"applications": list_applications()}


def _valid_recipient_address(address: str) -> str:
    """Accept a bare address or a canonical display-name address from the store."""
    name, parsed = parseaddr(address)
    is_bare_address = address == parsed
    is_display_address = (
        bool(name)
        and "@" not in name
        and address == f"{name} <{parsed}>"
    )
    if (
        not address
        or address != address.strip()
        or "\r" in address
        or "\n" in address
        or address.count("@") != 1
        or any(character.isspace() for character in parsed)
        or not (is_bare_address or is_display_address)
    ):
        raise ValueError("Invalid recipient address")
    return parsed


class RecruiterDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: list[str] | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = None
    body: str | None = None

    @field_validator("to", "cc", "bcc")
    @classmethod
    def validate_recipients(cls, addresses: list[str] | None) -> list[str] | None:
        if addresses is None:
            return addresses
        return [_valid_recipient_address(address) for address in addresses]

    @field_validator("subject", "body")
    @classmethod
    def validate_nonempty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Draft text must not be empty")
        return value


def _recruiter_message_or_404(message_id: str) -> dict[str, Any]:
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Recruiter message not found")
    return message


def _review_draft(message: dict[str, Any]) -> dict[str, Any]:
    """Return candidate-review content, never a claimed outgoing Gmail result.

    Send state is stripped here for the same reason the Gmail ids are: approving
    or editing a draft says nothing about whether mail went out, and a `sentAt`
    key riding along on that response invites a caller to read one into the
    other. `GET /api/recruiter-messages` and the detail route return the full
    record, so the UI still sees it — from a route that is about state rather
    than about review.
    """
    draft = dict(message["draft"])
    draft.pop("gmailMessageId", None)
    draft.pop("gmailDraftId", None)
    draft.pop("sentAt", None)
    draft.pop("gmailSentMessageId", None)
    return draft


@app.get("/api/recruiter-messages")
async def recruiter_messages(applicationId: str | None = None) -> dict[str, Any]:
    return {"messages": list_messages(applicationId)}


@app.get("/api/recruiter-messages/{message_id}")
async def recruiter_message_detail(message_id: str) -> dict[str, Any]:
    return _recruiter_message_or_404(message_id)


@app.put("/api/recruiter-messages/{message_id}/draft")
async def recruiter_message_draft(
    message_id: str, update: RecruiterDraftUpdate
) -> dict[str, Any]:
    _recruiter_message_or_404(message_id)
    try:
        return _review_draft(update_draft(message_id, update.model_dump(exclude_none=True)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Recruiter message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/recruiter-messages/{message_id}/approve")
async def recruiter_message_approve(message_id: str) -> dict[str, Any]:
    _recruiter_message_or_404(message_id)
    try:
        return _review_draft(approve_draft(message_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Recruiter message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class SentPayload(BaseModel):
    gmailSentMessageId: str
    sentAt: str | None = None


@app.post("/api/recruiter-messages/{message_id}/sent")
async def recruiter_message_sent(message_id: str, req: SentPayload) -> dict[str, Any]:
    """Record that the candidate sent this reply from Gmail.

    CareerOS does not send, so this is always reported from outside — by the
    reconcile script reading the Sent folder, or by the candidate saying so.
    """
    from .recruiter_messages import mark_draft_sent

    _recruiter_message_or_404(message_id)
    try:
        # Not `_review_draft`: recording that mail went out is the one response
        # whose whole point is the send state it strips.
        draft = mark_draft_sent(message_id, req.gmailSentMessageId, req.sentAt)["draft"]
        return {
            "id": draft["id"],
            "status": draft["status"],
            "sentAt": draft["sentAt"],
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Recruiter message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/recruiter-messages/{message_id}/dismiss")
async def recruiter_message_dismiss(message_id: str) -> dict[str, Any]:
    _recruiter_message_or_404(message_id)
    try:
        return _review_draft(dismiss_draft(message_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Recruiter message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/recruiter-messages/{message_id}/retry")
async def recruiter_message_retry(message_id: str) -> dict[str, Any]:
    _recruiter_message_or_404(message_id)
    try:
        return _review_draft(retry_draft(message_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Recruiter message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/applications/{app_id}")
async def application_detail(app_id: str) -> dict[str, Any]:
    record = get_application(app_id)
    if not record:
        raise HTTPException(status_code=404, detail="Application not found")
    return record


class OutcomeRequest(BaseModel):
    outcome: str  # "rejected" | "offer" | "withdrawn"
    reason: str = ""
    stage: str = ""


@app.post("/api/applications/{app_id}/outcome")
async def application_outcome(app_id: str, req: OutcomeRequest) -> dict[str, Any]:
    """Record how an application ended, and how far it got.

    Captured now so the analysis has real data when there is enough of it.
    Nothing here infers a reason — the employer rarely gives one, and a guess
    stored beside a stated reason becomes indistinguishable from it.
    """
    from .store import record_outcome

    if req.outcome not in ("rejected", "offer", "withdrawn"):
        raise HTTPException(
            status_code=400, detail="outcome must be rejected|offer|withdrawn"
        )
    if not get_application(app_id):
        raise HTTPException(status_code=404, detail="Application not found")
    record_outcome(app_id, req.outcome, req.reason, req.stage)
    return get_application(app_id) or {}


class AdvanceRequest(BaseModel):
    status: str
    note: str | None = None


@app.post("/api/applications/{app_id}/advance")
async def application_advance(app_id: str, req: AdvanceRequest) -> dict[str, Any]:
    if not get_application(app_id):
        raise HTTPException(status_code=404, detail="Application not found")
    advance(app_id, req.status, req.note or f"Moved to {req.status}")
    return get_application(app_id)


class ImportedJob(BaseModel):
    id: str | None = None
    title: str
    company: str
    location: str | None = None
    description: str = ""
    applyUrl: str = ""
    postedAt: str | None = None
    workArrangement: str | None = None
    salaryText: str | None = None


class ImportRequest(BaseModel):
    source: str
    jobs: list[ImportedJob]


@app.post("/api/jobs/import")
async def import_external(req: ImportRequest) -> dict[str, Any]:
    """Accept postings from sources a server can't legitimately call itself.

    Indeed's public API is retired and partner-gated; LinkedIn prohibits
    automated access. Postings gathered through a legitimate client-side
    channel can be imported here and are then treated like any other job.
    """
    from .imported import import_jobs

    stored = import_jobs([j.model_dump() for j in req.jobs], req.source)
    await fetch_all_jobs(force=True)  # refresh cache so they appear immediately
    return {"imported": stored, "source": req.source}


def _is_blocking(note: str | None) -> bool:
    """Whether a `next_action` needs the candidate, or is just the pipeline
    narrating itself. Shares its markers with alerts._BLOCKING_NOTES."""
    from .alerts import _BLOCKING_NOTES

    low = (note or "").lower()
    return any(marker in low for marker in _BLOCKING_NOTES)


@app.get("/api/apply-queue")
async def apply_queue() -> dict[str, Any]:
    """What to work through next, in the order worth doing it.

    The pieces of this already existed and never met: applications sit in
    `ready`, `priority()` knows what is worth doing next, `aging_applications`
    knows what is going stale, and `prefill_apply.py --all` can open a batch —
    but the only way to act on any of it was one job at a time through eight
    steps and two context switches.

    Ordering is aging first, then priority. That is deliberate: a prepared
    application that has sat a week is losing value every day it waits, while a
    fresh high-priority one is not. Within each group `priority()` decides,
    which already blends fit, freshness, effort and source trust.

    Nothing here submits. Every row still ends at the employer's own form with
    the candidate pressing the button.
    """
    from .alerts import STALE_AFTER_DAYS
    from .priority import priority
    from .store import list_applications

    profile = _profile()
    pool = {j["id"]: j for j in await fetch_all_jobs()}
    now_ts = datetime.now(timezone.utc)

    rows: list[dict[str, Any]] = []
    for record in list_applications():
        if record.get("status") not in ("ready", "qualified", "tailoring"):
            continue
        job_id = record.get("jobId")
        job = pool.get(job_id)

        # A posting that has left its board cannot be applied to, so it is not
        # work — it is an alert, and it already has one.
        note = (record.get("nextAction") or "").lower()
        if "closed" in note or "no longer accepting" in note:
            continue

        # A role the candidate is not authorised to take is not work either.
        #
        # `us_only` keeps these out of discovery, but the queue is built from
        # stored applications and so bypassed it entirely — four tracked
        # applications are based outside the US and two were actually applied
        # to. A Canada-based role scoring 92 was kept out of this list only
        # because its posting happened to close; had it still been open it
        # would have led the queue.
        #
        # The stored location is checked as well as the live posting, because
        # the check has to hold for a record whose job has left the pool —
        # which is exactly the case where `job` is None and a live check
        # silently passes.
        if _foreign_location({"location": record.get("location") or ""}):
            continue
        if job and check_eligibility(job, profile).get("verdict") == "INELIGIBLE":
            continue

        age_days = None
        stamp = record.get("createdAt") or record.get("updatedAt")
        if stamp:
            try:
                started = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                age_days = round((now_ts - started).total_seconds() / 86400, 1)
            except ValueError:
                age_days = None

        fit = record.get("rawFitScore") or 0
        pr = priority(job, fit, True) if job else {}

        rows.append({
            "jobId": job_id,
            # `company` on an application record is an object, not a string.
            "company": (record.get("company") or {}).get("name", ""),
            "title": record.get("title"),
            "applyUrl": record.get("applyUrl") or (job or {}).get("applyUrl"),
            "fitScore": fit,
            "resumeScore": record.get("resumeScore"),
            "daysWaiting": age_days,
            "aging": age_days is not None and age_days >= STALE_AFTER_DAYS,
            "platform": (job or {}).get("atsPlatform"),
            "estimatedMinutes": (pr.get("friction") or {}).get("minutes"),
            "priorityScore": pr.get("score"),
            # `next_action` carries routine pipeline prompts as well as real
            # blockers. "Review and approve" sits on every ready application,
            # so treating any note as a warning painted all 23 rows amber and
            # taught the colour to mean nothing. Same distinction alerts.py
            # already makes.
            "blocked": _is_blocking(record.get("nextAction")),
            "note": record.get("nextAction") or "",
            "frictionNote": (pr.get("friction") or {}).get("note", ""),
            # Whether the posting is still in the fetched pool at all.
            "live": job is not None,
        })

    rows.sort(key=lambda r: (
        0 if r["aging"] else 1,
        -(r["priorityScore"] or 0),
        -(r["fitScore"] or 0),
    ))

    total_minutes = sum(r["estimatedMinutes"] or 10 for r in rows)
    return {
        "queue": rows,
        "total": len(rows),
        "aging": sum(1 for r in rows if r["aging"]),
        "estimatedMinutes": total_minutes,
        "staleAfterDays": STALE_AFTER_DAYS,
        "note": (
            "Ordered by what is going stale first, then by what is worth doing "
            "next. Every row opens the employer's own form pre-filled — "
            "CareerOS does not submit."
        ),
    }


@app.post("/api/jobs/{job_id}/prefill")
async def prefill_application(job_id: str) -> dict[str, Any]:
    """Open the employer's form in a visible browser with the answers filled in.

    This runs `scripts/prefill_apply.py`, which refuses to click any control
    matching `SUBMIT_PATTERNS`. Nothing here weakens that: the endpoint only
    starts the script and reports what it filled.

    It opens a window on the machine running this API, so it is local-only by
    nature — a remote deployment would silently do nothing useful, which is why
    failure is reported rather than swallowed.
    """
    import asyncio
    import sys
    from pathlib import Path

    await _job_or_404(job_id)  # 404 early rather than launching a browser

    script = Path(__file__).resolve().parent.parent / "scripts" / "prefill_apply.py"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script), job_id,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(
            status_code=504,
            detail="The form took too long to load. Try opening it directly.",
        ) from None

    text = (out or b"").decode(errors="replace")
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=text.strip()[-400:] or "prefill failed")

    return {
        "jobId": job_id,
        "report": text.strip()[-4000:],
        "note": "Review every answer, then submit it yourself. Nothing was sent.",
    }


class FromUrlRequest(BaseModel):
    url: str


@app.post("/api/jobs/from-url")
async def job_from_url(req: FromUrlRequest) -> dict[str, Any]:
    """Turn a pasted posting link into a scored, tailorable job.

    Blocked hosts return `blocked: true` rather than an error — the caller is
    expected to offer the paste-the-description path, which reaches the same
    scoring code and is the honest way to handle a source we may not fetch.
    """
    from .imported import import_jobs
    from .job_urls import UnresolvableURL, blocked_reason, resolve

    reason = blocked_reason(req.url)
    if reason:
        from .job_urls import _host

        return {"blocked": True, "host": _host(req.url), "reason": reason}

    try:
        job = await resolve(req.url)
    except UnresolvableURL as exc:
        return {"blocked": False, "unresolved": True, "reason": str(exc)}
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach the job board: {exc}"
        ) from exc

    if not (job.get("title") and job.get("description")):
        return {
            "blocked": False,
            "unresolved": True,
            "reason": "That posting came back empty — it may have been taken down.",
        }

    import_jobs([job], source=job.get("source") or "pasted-link", live=True)
    # Make it visible now. A full forced refetch would re-hit five job boards
    # for thousands of postings to surface this one, and timed out doing so.
    await fetch_all_jobs()
    add_to_cache(job)

    p = _profile()
    score = score_job(job, p)
    return {
        "jobId": job["id"],
        "title": job["title"],
        "company": job["company"]["name"],
        "location": job.get("location"),
        "applyUrl": job.get("applyUrl"),
        "rawFitScore": score["rawFitScore"],
        "eligibility": score.get("eligibility"),
    }


@app.get("/api/jobs/{job_id}/referral-strategy")
async def referral_strategy_for_job(job_id: str) -> dict[str, Any]:
    """Who to approach at this employer, and in what order.

    Finding an address is the easy half. This ranks the people already found
    by how much reason they have to reply, and sequences the approach so a
    referral is never the opening request.
    """
    from .referral import referral_strategy

    p = _profile()
    job = await _job_or_404(job_id)
    saved = [c for c in list_contacts() if c.get("jobId") == job_id]
    if not saved:
        return {
            "available": False,
            "reason": "no_contacts",
            "detail": (
                "No contacts saved for this job yet. Look them up first, or add "
                "one manually — the strategy ranks people, it does not find them."
            ),
        }
    return {"available": True, **referral_strategy(saved, job, p)}


@app.get("/api/jobs/{job_id}/contacts")
async def job_contacts(job_id: str) -> dict[str, Any]:
    """Look up real recruiter contacts at the employer behind this posting."""
    job = await _job_or_404(job_id)
    domain = company_domain(job["company"]["name"], job.get("applyUrl", ""))
    if not domain:
        # The outreach path has always fallen back to Hunter's company→domain
        # lookup here, and this endpoint gave up instead — so the same employer
        # resolved fine in one place and reported "no domain" in another. A
        # Figma posting on job-boards.greenhouse.io was unreachable from the
        # contacts screen while Hunter answers figma.com on request.
        domain = await resolve_domain_by_company(job["company"]["name"])
    if not domain:
        return {
            "available": False,
            "reason": "no_domain",
            "detail": (
                "This posting is hosted on an ATS domain and the employer's own "
                "mail domain could not be resolved from the company name either. "
                "Add a contact manually."
            ),
            "contacts": [],
        }
    result = await lookup_contacts(domain)

    # Rank before returning. A provider hands back everyone it can find — ten
    # people at Figma, eight of them recruiters — and an unranked list of ten
    # is the same problem as no list: the candidate still has to work out who
    # to write to. `referral.rank_paths` already scores by title against the
    # role, seniority and genuinely shared background, so use it here rather
    # than making every screen re-derive an order.
    from .referral import rank_paths

    found = result.get("contacts") or []
    if found:
        ranked = rank_paths(found, job, _profile())
        by_email = {c.get("email"): c for c in found}
        result["contacts"] = [
            {**(by_email.get(r["email"]) or {}), "rank": i + 1,
             "rankScore": r["score"], "rankWhy": r["why"]}
            for i, r in enumerate(ranked)
            if by_email.get(r["email"])
        ]
        # Say plainly how many are worth writing to. Everything a provider
        # returns is not a lead.
        result["worthContacting"] = sum(
            1 for c in result["contacts"] if c.get("rankScore", 0) >= 70
        )

    # Persist what the lookup found against this job. Without this the referral
    # strategy had nothing to rank — looking contacts up and then being told
    # "no contacts saved for this job" is the same flow contradicting itself.
    # Only on an explicit lookup, which the candidate initiates; nothing is
    # stored for the thousands of jobs nobody has asked about.
    for person in result.get("contacts") or []:
        save_contact({**person, "jobId": job_id, "company": job["company"]["name"]})

    result["jobId"] = job_id
    result["company"] = job["company"]["name"]
    return result


@app.get("/api/contacts")
async def contacts() -> dict[str, Any]:
    return {"contacts": list_contacts(), "lookupEnabled": hunter_key() is not None}


class ContactPayload(BaseModel):
    id: str | None = None
    jobId: str | None = None
    company: str
    name: str
    title: str | None = None
    email: str | None = None
    emailVerified: bool = False
    linkedinUrl: str | None = None
    confidence: int = 0
    provider: str = "manual"
    whySelected: str | None = None
    status: str = "not_started"


@app.post("/api/contacts")
async def create_contact(payload: ContactPayload) -> dict[str, Any]:
    return save_contact(payload.model_dump())


class ContactStatus(BaseModel):
    status: str


@app.post("/api/contacts/{contact_id}/status")
async def contact_status(contact_id: str, req: ContactStatus) -> dict[str, Any]:
    if not get_contact(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    set_contact_status(contact_id, req.status)
    return get_contact(contact_id) or {}


# ------------------------------------------------------------------ outreach
@app.get("/api/outreach")
async def outreach_list() -> dict[str, Any]:
    """Outreach drafts, each carrying the person it is addressed to.

    The stored record keeps only `contactId`, so the client had a draft and no
    way to address it: no name, no email, no LinkedIn. Sending one meant opening
    Contacts in another tab, finding the row and copying the address by hand —
    every time, for every draft. That is why thirteen of them were never sent.

    Joined here rather than in the client so there is one round trip and one
    definition of which contact a draft belongs to.
    """
    from .contacts import get_contact
    from .outreach_store import list_outreach

    items = list_outreach()
    cache: dict[str, dict[str, Any] | None] = {}
    for item in items:
        contact_id = item.get("contactId")
        if not contact_id:
            continue
        if contact_id not in cache:
            cache[contact_id] = get_contact(contact_id)
        contact = cache[contact_id]
        if not contact:
            continue
        item["contactName"] = contact.get("name")
        item["contactTitle"] = contact.get("title")
        item["contactEmail"] = contact.get("email")
        item["contactLinkedin"] = contact.get("linkedinUrl")
        # Surfaced so an unverified address is visible at the moment of
        # sending, not buried in the contact record it came from.
        item["contactEmailVerified"] = bool(contact.get("emailVerified"))
    return {"outreach": items}


class OutreachAction(BaseModel):
    action: str  # "sent" | "replied" | "unreplied"


@app.post("/api/outreach/{outreach_id}/status")
async def outreach_status(outreach_id: str, req: OutreachAction) -> dict[str, Any]:
    from .automation import get_rules
    from .outreach_store import get_outreach, mark_replied, mark_sent, unmark_replied

    if not get_outreach(outreach_id):
        raise HTTPException(status_code=404, detail="Outreach not found")
    if req.action == "sent":
        return mark_sent(outreach_id, get_rules()["followUpDelayBusinessDays"]) or {}
    if req.action == "replied":
        return mark_replied(outreach_id) or {}
    if req.action == "unreplied":
        # Undo. Marking a thread replied cancels its follow-up, so a mis-click
        # silently drops the reminder to chase someone who never answered.
        return unmark_replied(outreach_id, get_rules()["followUpDelayBusinessDays"]) or {}
    raise HTTPException(
        status_code=400, detail="action must be sent|replied|unreplied"
    )


class ClaimPayload(BaseModel):
    claim: str
    employer_or_project: str
    classification: str
    skills: list[str] = []
    industry: str = ""
    date_range: str = ""
    evidence_source: str = ""
    project: str = ""
    approved_for_resume: bool = False


class ClaimPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str | None = None
    employer_or_project: str | None = None
    classification: str | None = None
    skills: list[str] | None = None
    industry: str | None = None
    date_range: str | None = None
    evidence_source: str | None = None
    project: str | None = None
    approved_for_resume: bool | None = None
    # Required to move designed work to delivered. Not a formality: it changes
    # what every future resume is allowed to assert.
    confirmDelivered: bool = False


@app.get("/api/evidence")
async def evidence_list() -> dict[str, Any]:
    from .evidence import CLASSIFICATIONS, list_claims

    claims = list_claims()
    return {
        "claims": claims,
        "classifications": list(CLASSIFICATIONS),
        "approvedForResume": sum(1 for c in claims if c.get("approved_for_resume")),
    }


@app.post("/api/evidence")
async def evidence_add(payload: ClaimPayload) -> dict[str, Any]:
    from .evidence import EvidenceError, add_claim

    try:
        return add_claim(payload.model_dump())
    except EvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/evidence/{claim_id}")
async def evidence_update(claim_id: str, patch: ClaimPatch) -> dict[str, Any]:
    from .evidence import EvidenceError, update_claim

    try:
        return update_claim(claim_id, patch.model_dump(exclude_none=True))
    except EvidenceError as exc:
        # 409 for the promotion guard: the request is well-formed, it just
        # asserts something the candidate has not confirmed.
        code = 409 if "delivered" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.delete("/api/evidence/{claim_id}")
async def evidence_retire(claim_id: str) -> dict[str, Any]:
    """Retire, never delete — the record of real work is not ours to destroy."""
    from .evidence import EvidenceError, retire_claim

    try:
        return retire_claim(claim_id)
    except EvidenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/jobs/refresh")
async def refresh_jobs() -> dict[str, Any]:
    """Re-poll every source now, instead of waiting for the cache to expire.

    Discovery is lazy and cached for 15 minutes, so the pool only moved when
    someone opened the app or the 07:00 job ran. There was no way to say
    "look again" — which is the natural thing to want after pasting a link or
    hearing about a role.

    This fetches only. It scores nothing, tailors nothing, and queues nothing;
    `POST /api/automation/run` is the one that does that.
    """
    from .discovery import failed_sources, fetch_all_jobs, filter_jobs, source_counts

    jobs = await fetch_all_jobs(force=True)
    us = filter_jobs(jobs)
    return {
        "total": len(jobs),
        "unitedStates": len(us),
        "pasted": sum(1 for j in jobs if j.get("origin") == "pasted"),
        "sources": source_counts(),
        # A source that errors returns nothing, which looks exactly like a
        # quiet day on that board. Say so rather than let it read as no news.
        "failed": failed_sources(),
    }


@app.get("/api/jobs/{job_id}/interview-pack")
async def interview_pack(job_id: str) -> dict[str, Any]:
    """Everything known about this application, assembled for the interview.

    Contains no company research and no generic question bank — see
    interview.py for why inventing either would be worse than omitting it.
    """
    from .interview import build_pack
    from .recruiter_messages import list_messages

    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    resume = tailor_resume(job, s, p)
    record = get_application(f"app_{job_id}")
    messages = [
        m for m in list_messages()
        if (m.get("applicationId") or "").endswith(job_id)
    ]
    return build_pack(job, s, resume, p, record, messages)


@app.get("/api/skill-gaps")
async def skill_gap_report(minimumFit: int = 70) -> dict[str, Any]:
    """Which missing requirement costs the most across the roles worth applying to.

    Aggregated rather than per-job: one resume's gap list says what one employer
    wanted; across the whole target set it says what to go and learn.
    """
    from .prescreen import rank_for_scoring
    from .priority import skill_gaps

    p = _profile()
    jobs = filter_jobs(await fetch_all_jobs())
    candidates, _ = rank_for_scoring(jobs, p, SCORE_BUDGET)

    scored = []
    for job in candidates:
        s = score_job(job, p)
        if s["rawFitScore"] >= minimumFit:
            scored.append((job, s))

    return {
        "gaps": skill_gaps(scored),
        "consideredJobs": len(scored),
        "minimumFit": minimumFit,
        "note": (
            "Ranked by how many target roles ask for it, weighted by how well you "
            "otherwise fit those roles. No time-to-learn estimate is given — that "
            "number would be invented."
        ),
    }


@app.get("/api/alerts")
async def alerts() -> dict[str, Any]:
    """What is outstanding — things written, approved or received and not acted on.

    The inverse of /api/follow-ups, which only knows about things that already
    happened. Reads stored state only; facts that live in Gmail are written in
    by scripts/check_replies.py.
    """
    from .alerts import build_alerts, funnel

    items = build_alerts()
    return {
        "alerts": items,
        "high": sum(1 for a in items if a["severity"] == "high"),
        # Counts, not rates. Everything downstream of outcome data needs these
        # to grow first, and an unexplained absence reads as a missing feature.
        "funnel": funnel(),
    }


@app.get("/api/follow-ups")
async def followups() -> dict[str, Any]:
    from .outreach_store import list_followups

    return {"followUps": list_followups()}


# ------------------------------------------------------------ saved searches
class SavedSearchPayload(BaseModel):
    label: str
    filters: dict[str, Any] = {}


@app.get("/api/saved-searches")
async def saved_searches() -> dict[str, Any]:
    from .outreach_store import list_searches

    return {"searches": list_searches()}


@app.post("/api/saved-searches")
async def create_saved_search(payload: SavedSearchPayload) -> dict[str, Any]:
    from .outreach_store import save_search

    return save_search(payload.label, payload.filters)


@app.delete("/api/saved-searches/{search_id}")
async def remove_saved_search(search_id: str) -> dict[str, Any]:
    from .outreach_store import delete_search

    delete_search(search_id)
    return {"deleted": search_id}


@app.post("/api/saved-searches/{search_id}/toggle")
async def toggle_saved_search(search_id: str) -> dict[str, Any]:
    from .outreach_store import list_searches, toggle_search

    toggle_search(search_id)
    return next((s for s in list_searches() if s["id"] == search_id), {})


# --------------------------------------------------------------- automation
@app.get("/api/automation")
async def automation_status() -> dict[str, Any]:
    from .automation import get_rules, is_running, latest_run

    run = latest_run()
    return {
        "running": is_running(),
        "rules": get_rules(),
        "lastRun": run,
        "note": (
            "A run discovers, scores and tailors. It never submits an "
            "application or sends a message — everything lands in Approvals."
        ),
    }


class RunRequest(BaseModel):
    maxTailor: int | None = None


@app.post("/api/automation/run")
async def automation_run(req: RunRequest) -> dict[str, Any]:
    from .automation import run_autopilot

    return await run_autopilot(req.maxTailor)


class RulesPayload(BaseModel):
    minimumFitToTailor: int | None = None
    minimumResumeScore: int | None = None
    maxApplicationsPerDay: int | None = None
    submissionMode: str | None = None
    emailMode: str | None = None
    jobRecencyDays: int | None = None
    autoRejectBelowFit: int | None = None
    recruiterConfidenceMinimum: int | None = None
    followUpDelayBusinessDays: int | None = None
    targetQueries: list[str] | None = None


@app.patch("/api/automation/rules")
async def automation_rules(payload: RulesPayload) -> dict[str, Any]:
    from .automation import save_rules

    return save_rules(payload.model_dump(exclude_none=True))


@app.get("/api/approvals")
async def approvals() -> dict[str, Any]:
    """Pending approvals, each with the criteria that decide it.

    The queue has always run on launch-commit logic — one disqualifying fact
    stops an application regardless of a good score elsewhere — and never
    showed it. A card said "Ready to apply" with a fit number while the system
    separately knew the eligibility verdict, whether the posting had closed,
    and whether required skills had evidence. Those are gathered here, at the
    point the decision is actually made.

    Computed at read time rather than stored, because a posting closing after
    the approval was raised has to change the call.
    """
    from .commit_criteria import commit_call, criteria_for

    items = list_approvals()
    if not items:
        return {"approvals": items}

    p = _profile()
    pool = {j["id"]: j for j in await fetch_all_jobs()}
    apps = {a["jobId"]: a for a in list_applications()}

    out = []
    for item in items:
        job = pool.get(item.get("jobId"))
        record = apps.get(item.get("jobId")) or {}
        closed = "closed" in (record.get("nextAction") or "").lower()

        score = score_job(job, p) if job else None
        eligibility = check_eligibility(job, p) if job else None
        criteria = criteria_for(
            job,
            score,
            item.get("resumeScore") or record.get("resumeScore"),
            eligibility,
            posting_closed=closed,
        )
        out.append({**item, "criteria": criteria, "commit": commit_call(criteria)})

    return {"approvals": out}


@app.post("/api/approvals/clear-held")
async def clear_held_approvals() -> dict[str, Any]:
    """Resolve every approval a commit criterion is holding.

    The board already says which cannot proceed; without this the candidate
    still dismisses six cards one at a time, which is the kind of busywork that
    makes a queue stop being read.

    Only NO-GO items are touched. A caution is a fact worth stating, not a
    reason to clear something on the candidate's behalf — and nothing here
    decides that a role is unwanted, only that it cannot proceed as it stands.
    Rejected rather than deleted, so the decision stays on the record.
    """
    from .commit_criteria import commit_call, criteria_for

    items = list_approvals()
    if not items:
        return {"cleared": 0, "items": []}

    p = _profile()
    pool = {j["id"]: j for j in await fetch_all_jobs()}
    apps = {a["jobId"]: a for a in list_applications()}

    cleared = []
    for item in items:
        job = pool.get(item.get("jobId"))
        record = apps.get(item.get("jobId")) or {}
        closed = "closed" in (record.get("nextAction") or "").lower()
        criteria = criteria_for(
            job,
            score_job(job, p) if job else None,
            item.get("resumeScore") or record.get("resumeScore"),
            check_eligibility(job, p) if job else None,
            posting_closed=closed,
        )
        call = commit_call(criteria)
        if call["verdict"] != "nogo":
            continue
        resolve_approval(item["id"], "rejected")
        cleared.append({
            "company": item.get("companyName"),
            "title": item.get("jobTitle"),
            "heldBy": call["heldBy"],
            "why": call["summary"],
        })

    return {"cleared": len(cleared), "items": cleared}


class ApprovalAction(BaseModel):
    action: str  # "approved" | "rejected"


@app.post("/api/approvals/{approval_id}")
async def approval_action(approval_id: str, req: ApprovalAction) -> dict[str, Any]:
    if req.action not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="action must be approved|rejected")
    resolve_approval(approval_id, req.action)

    # Approving is the signal that this job is worth real effort, so the
    # recruiter research runs here rather than on every job the autopilot
    # touches. Rejecting triggers nothing.
    outreach_result = None
    if req.action == "approved":
        job_id = approval_id.replace("appr_application_", "", 1)
        if job_id != approval_id:
            outreach_result = await _draft_outreach_once(job_id)

    return {"id": approval_id, "status": req.action, "outreach": outreach_result}
