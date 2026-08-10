# careeros-api

> **Part of CareerOS** — [careeros](https://github.com/Himansh97/careeros) (data + docs + state) · **careeros-api** (backend) · [careeros-web](https://github.com/Himansh97/careeros-web) (frontend)
>
> Current state: [`careeros/docs/STATE.md`](https://github.com/Himansh97/careeros/blob/main/docs/STATE.md)


Backend for CareerOS. Live job discovery, evidence-based scoring, resume
tailoring, and outreach drafting.

## What is actually live

- **Job discovery** — real, unauthenticated Greenhouse job-board APIs across
  the companies in `app/config.py`. Every posting returned is a real open
  role with a working apply URL. Coverage is limited to those boards; that
  limit is reported by `/api/health` rather than hidden.
- **Candidate data** — read from the `careeros` repo
  (`candidate_master_profile.json`, `career_evidence.json`, and the YAML
  preference files). Nothing is duplicated or invented here.
- **Scoring and tailoring** — deterministic. Requirements are extracted from
  the job description against a skill vocabulary that deliberately includes
  skills the candidate lacks, so true gaps surface instead of every job
  scoring perfectly. A resume bullet can only be a verbatim claim from
  `career_evidence.json`.

## What is deliberately not automated

- **No sending.** Outreach returns drafts and a `mailto:` link. The candidate
  reviews and sends.
- **No recruiter invention.** Public job APIs expose no recruiter identity,
  so `contact` is null. Guessing a name or an email pattern would be
  fabrication.
- **No LLM.** Scoring and tailoring are rule-based, which is both free and
  structurally incapable of inventing experience. `app/scoring.py` and
  `app/tailor.py` are the seams if an LLM is added later.

## Run

```bash
python3 -m venv .venv
./.venv/bin/pip install fastapi "uvicorn[standard]" httpx pyyaml
./.venv/bin/uvicorn app.main:app --port 8000
```

Then point the frontend at it via `NEXT_PUBLIC_API_URL=http://localhost:8000`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status and source coverage |
| GET | `/api/profile` | Candidate profile + evidence library |
| POST | `/api/jobs/search` | Live discovery, scored and ranked |
| GET | `/api/jobs/{id}` | Job detail with requirement matrix |
| POST | `/api/jobs/{id}/tailor` | Generate tailored resume + audit |
| POST | `/api/jobs/{id}/outreach` | Generate outreach drafts |
| GET | `/api/applications` | Pipeline, persisted in SQLite |
| POST | `/api/applications/{id}/advance` | Move pipeline stage |
| GET | `/api/approvals` | Items awaiting human review |
| POST | `/api/approvals/{id}` | Approve or reject |
