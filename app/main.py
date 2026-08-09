"""CareerOS API — live job discovery, evidence-based scoring, tailoring, outreach."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import ALLOWED_ORIGINS, GREENHOUSE_COMPANIES
from .contacts import (
    company_domain,
    get_contact,
    hunter_domain_search,
    hunter_key,
    list_contacts,
    save_contact,
    set_contact_status,
)
from .discovery import fetch_all_jobs, filter_jobs, source_counts
from .outreach import build_outreach
from .profile import ProfileNotFound, load_profile
from .scoring import score_job
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
from .tailor import tailor_resume

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


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sources": ["Greenhouse", "Ashby", "The Muse", "Arbeitnow", "RemoteOK"],
        "greenhouseCompanies": GREENHOUSE_COMPANIES,
        "lastFetchCounts": source_counts(),
        "contactLookup": {
            "provider": "hunter.io",
            "enabled": hunter_key() is not None,
            "note": (
                "Set HUNTER_API_KEY to enable live recruiter lookup (free tier: "
                "25 domain searches/month). Manual contact entry always works."
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


@app.post("/api/jobs/search")
async def search(req: SearchRequest) -> dict[str, Any]:
    p = _profile()
    all_jobs = await fetch_all_jobs()
    matched = filter_jobs(all_jobs, req.query, req.location, req.workArrangements)

    stamped = datetime.now(timezone.utc).isoformat()
    scored: list[dict[str, Any]] = []
    # Scoring reads full descriptions, so cap the working set for responsiveness.
    for job in matched[: max(req.limit * 3, 60)]:
        s = score_job(job, p)
        if req.minimumFit is not None and s["rawFitScore"] < req.minimumFit:
            continue
        scored.append({**job, **s, "discoveredAt": stamped, "applicationStatus": "discovered"})

    scored.sort(key=lambda j: -j["rawFitScore"])
    return {
        "jobs": scored[: req.limit],
        "total": len(matched),
        "scored": len(scored),
        "sources": ["Greenhouse"],
    }


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str) -> dict[str, Any]:
    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    return {
        **job,
        **s,
        "discoveredAt": datetime.now(timezone.utc).isoformat(),
        "applicationStatus": "discovered",
    }


@app.post("/api/jobs/{job_id}/tailor")
async def tailor(job_id: str) -> dict[str, Any]:
    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    resume = tailor_resume(job, s, p)
    resume["updatedAt"] = datetime.now(timezone.utc).isoformat()

    record = upsert_application(job, s)
    if record:
        set_resume_score(record["id"], resume["resumeScore"])
        add_approval(
            "application",
            job_id,
            {
                "jobTitle": job["title"],
                "companyName": job["company"]["name"],
                "title": "Application ready to review",
                "whatCareerOSWantsToDo": (
                    f"Submit an application to {job['company']['name']} for "
                    f"{job['title']} using the tailored resume "
                    f"(score {resume['resumeScore']})."
                ),
                "whyApprovalRequired": (
                    "Submitting is irreversible and happens under your name, so it "
                    "always waits for your review."
                ),
                "rawFitScore": s["rawFitScore"],
                "resumeScore": resume["resumeScore"],
                "applyUrl": job.get("applyUrl"),
            },
        )
    return resume


@app.post("/api/jobs/{job_id}/outreach")
async def outreach(job_id: str) -> dict[str, Any]:
    p = _profile()
    job = await _job_or_404(job_id)
    s = score_job(job, p)
    return build_outreach(job, s, p)


@app.get("/api/applications")
async def applications() -> dict[str, Any]:
    return {"applications": list_applications()}


@app.get("/api/applications/{app_id}")
async def application_detail(app_id: str) -> dict[str, Any]:
    record = get_application(app_id)
    if not record:
        raise HTTPException(status_code=404, detail="Application not found")
    return record


class AdvanceRequest(BaseModel):
    status: str
    note: str | None = None


@app.post("/api/applications/{app_id}/advance")
async def application_advance(app_id: str, req: AdvanceRequest) -> dict[str, Any]:
    if not get_application(app_id):
        raise HTTPException(status_code=404, detail="Application not found")
    advance(app_id, req.status, req.note or f"Moved to {req.status}")
    return get_application(app_id)


@app.get("/api/jobs/{job_id}/contacts")
async def job_contacts(job_id: str) -> dict[str, Any]:
    """Look up real recruiter contacts at the employer behind this posting."""
    job = await _job_or_404(job_id)
    domain = company_domain(job["company"]["name"], job.get("applyUrl", ""))
    if not domain:
        return {
            "available": False,
            "reason": "no_domain",
            "detail": (
                "This posting is hosted on an ATS domain, so the employer's own "
                "mail domain can't be derived from it. Add a contact manually."
            ),
            "contacts": [],
        }
    result = await hunter_domain_search(domain)
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


@app.get("/api/approvals")
async def approvals() -> dict[str, Any]:
    return {"approvals": list_approvals()}


class ApprovalAction(BaseModel):
    action: str  # "approved" | "rejected"


@app.post("/api/approvals/{approval_id}")
async def approval_action(approval_id: str, req: ApprovalAction) -> dict[str, Any]:
    if req.action not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="action must be approved|rejected")
    resolve_approval(approval_id, req.action)
    return {"id": approval_id, "status": req.action}
