# careeros-api

> **Part of CareerOS** — **careeros-api** (backend) · [careeros-web](https://github.com/Himansh97/careeros-web) (frontend)

Backend for CareerOS: live job discovery, evidence-based fit scoring, resume
tailoring, and recruiter outreach drafting. FastAPI, SQLite, no ORM.

## Two rules this is built around

**Nothing auto-submits.** The API prepares an application and stops. The browser
automation in `app/prefill.py` drives a *visible* window and is structurally
incapable of pressing submit — `SUBMIT_PATTERNS` names the controls that send an
application and there is no flag that overrides it. This is not caution for its
own sake: Indeed's terms prohibit automating their Apply flow, Greenhouse
prohibits automated access and defends its boards with invisible reCAPTCHA, and
Dice permits ordinary browsers as "Approved Devices" — which is exactly what a
headed window a person drives is. A tool that quietly crossed that line would be
worth less than no tool.

**No claim without evidence.** Every line of a generated resume must trace to a
claim in an evidence file, and generated prose is discarded *whole* if any
sentence introduces a figure, a proper noun or a seniority verb its source does
not support (`app/compose.py`, `app/containment.py`). An unrecognised requirement
is reported as a gap, never assumed as a pass — a mortgage compliance posting
once scored 98/100 with "no gaps" precisely because the requirements the system
did not understand were invisible rather than unmet.

The candidate's own data lives in a separate private repository and never enters
this one.

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

## Technical Interview Lab

The lab at `/api/prep/technical` provides a versioned curriculum across SQL,
statistics, metrics, interpretation, Python/Pandas, modelling, ETL quality,
dashboard design, and five analyst role missions. Guided attempts use progressive
hints and require an unaided practice plus a different-shape transfer to establish
mastery. Timed 30/45/60-minute sessions freeze their question manifest and reveal
grading only after submission or authoritative server expiry.

SQL executes in a disposable subprocess against server-owned deterministic
SQLite fixtures. Python executes in a browser Web Worker; the API receives only
bounded normalized output. Neither path grades against live CareerOS data. The
optional `build_private_snapshot` helper creates an ungraded aggregate-only local
sandbox and refuses to copy identifiers, companies, roles, contacts, or free text.

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python scripts/verify_technical_lab.py
```

To extend the curriculum, add an immutable versioned JSON manifest in
`app/technical_learning`, register its dataset version, and keep public manifests
free of expected answers, rubrics, and solutions.
