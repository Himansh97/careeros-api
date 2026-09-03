# AGENTS.md — careeros-api

Canonical brief for any coding agent working in this repo. Codex reads this file
directly; `CLAUDE.md` imports it so Claude Code reads the same content. **Edit this
file, never `CLAUDE.md`.**

## Part of CareerOS

| Repo | Owns |
| --- | --- |
| [careeros](https://github.com/Himansh97/careeros) | Candidate data, docs, **`docs/STATE.md` — current state, read it first** |
| **careeros-api** (this one) | Discovery, scoring, tailoring, documents, outreach |
| [careeros-web](https://github.com/Himansh97/careeros-web) | Next.js frontend, talks to this on :8000 |

The candidate's real data lives in `~/careeros/` and is **gitignored there**. This repo
reads it through `app/profile.py`.

## Non-negotiables

1. **No fabrication.** Every resume claim must trace to a claim in `career_evidence.json`.
   A change that inflates a fit score without evidence is a correctness bug. The system's
   whole value is that its numbers can be trusted.
2. **An unrecognised requirement is a gap, never a pass.** Not knowing what a term means
   is not a reason to assume the candidate meets it.
3. **PII never enters git. This repo is public.** `careeros-api` and `careeros-web` are
   public on GitHub; only `careeros`, which holds the candidate's data, is private. Assume
   anything committed here is readable by a recruiter who finds the portfolio and clicks
   through, because that is the actual threat model.

   Resume text is derived from `career_evidence.json`, which `careeros` gitignores as
   personal data, so it must not be inlined in Python either. Per-job overrides live in
   `~/careeros/overrides/<job_id>.json` (gitignored) and are applied by the generic
   `scripts/apply_overrides.py`. `.gitignore` blocks `scripts/align_*.py` and
   `scripts/import_*.py` so a one-off cannot reintroduce this.

   Three places this leaks that are easy to miss:

   - **Tests.** A test asserting `GPA 3.6` or a graduation date puts that value in a
     public repo. Read expected values out of the loaded profile instead. The test is
     better for it: it then checks the pipeline rather than restating a constant.
   - **Comments.** A comment explaining a layout fix by quoting the string that broke it
     publishes the string.
   - **Commit messages.** They are as public as the diff and are not covered by
     `.gitignore`. A message reasoning about the candidate's employment gap, tenure
     arithmetic, or grades is a durable public record of the weakest part of their
     resume. Explain the engineering reason; leave the personal specifics out.

   Employer names, job titles and the mortgage domain are already public here and on the
   candidate's own portfolio, so those are not the concern. Grades, gap analysis, salary,
   contact details and anything derived from `career_evidence.json` are.
4. **Never add a `Co-Authored-By: Claude` trailer to commits.**
5. **Nothing auto-submits.** The API prepares and stops. `app/prefill.py` drives a
   *visible* browser and is structurally incapable of submitting — `SUBMIT_PATTERNS`
   names the controls that send an application and there is no flag that overrides it.
   This is not caution for its own sake: Indeed's terms prohibit "any automation,
   scripting, or bots to automate the Indeed Apply process", Greenhouse prohibits
   automated means to access its services and defends its boards with invisible
   reCAPTCHA, and Dice permits ordinary browsers as "Approved Devices" — which is
   exactly what a headed window the candidate drives is.

## How to run it

```bash
./.venv/bin/uvicorn app.main:app --port 8000     # API
./.venv/bin/python tests/test_overrides.py       # containment tests
./.venv/bin/python tests/test_distribution.py    # resume shape tests
./.venv/bin/python scripts/build_packets.py      # regenerate application packets
./.venv/bin/python scripts/apply_overrides.py --all --dry-run   # verify tailored bullets
./.venv/bin/python scripts/prefill_apply.py <job_id> --dry-run  # plan an application
./.venv/bin/python scripts/prefill_apply.py <job_id>            # open it pre-filled
./.venv/bin/python scripts/check_replies.py --sent s.json --inbox i.json
./.venv/bin/python scripts/prune_approvals.py --dry-run
./.venv/bin/python scripts/sync_notion.py --setup <page_id>  # once, builds the database
./.venv/bin/python scripts/sync_notion.py --write            # mirror applications to Notion
```

## Job sources: the line between an API and a crawl

Nine direct board readers plus one aggregator. The rule that decides whether a
source gets implemented is whether the operator publishes a way in: a
documented API, published rate limits, an issued key. Greenhouse, Ashby, Lever,
Workday, SmartRecruiters and JobDataLake all offer that bargain, so they are
read.

**LinkedIn, Indeed, Wellfound and Naukri are refused, and the gap is reported
rather than papered over** — `sources.NOT_COVERED` carries the reason for each
and the UI shows it. Naukri is the least ambiguous of them: its `robots.txt`
sets `Disallow: /` for a named list of AI crawlers that includes `claudebot`,
`Claude-User` and `Claude-SearchBot`, and its search pages do not answer a
non-browser client at all. Do not implement it, and do not implement it through
a third-party scraping service either — relocating the request does not change
whose terms are breached, and most of those services want session cookies from
the candidate's own account.

**JobDataLake costs two calls per job, and that is deliberate.** Its search
endpoint returns no description. `score_job` on an empty description returns
**85 with no gaps** -- above a real posting the candidate fits worse, because
there are no stated requirements to miss -- so a job with no description must
never enter the pool. Each row is hydrated from `/v1/jobs/:handle` and dropped
if it cannot be. Do not "optimise" that away.

**Work rights are per country, in `work_rights` on the profile.** The
`work_location` blocker used to reason from the US-shaped `work_authorization`
string alone, which treated every non-US country identically and ruled out all
127 India postings while the candidate holds Indian citizenship. An absent
country means no recorded right and still blocks: a data gap must not become a
green light on the one question where being wrong is expensive. Dublin and
Sao Paulo still block, and `tests/test_work_rights.py` asserts that removing
the recorded India right restores the India block.

India coverage comes from JobDataLake, not Naukri. `JOBDATALAKE_API_KEY` in
`.env` turns it on; without it the India region reports itself unconfigured and
refuses to be selected, because the direct readers are US employers and serving
them under an India heading is the plausible-wrong-answer this codebase refuses
everywhere else.

## The Notion mirror is one way, and must stay that way

`app/notion.py` publishes applications to Notion. It is off unless `NOTION_TOKEN`
and `NOTION_DATA_SOURCE_ID` are in `.env`, and off is a normal state — CareerOS
works identically without it. The daily job runs it and logs a skip line when it
is not configured.

**CareerOS owns every column it writes; Notion owns only `Notes`, which the sync
never touches.** This asymmetry is the whole design. Two systems that can both
write a status disagree silently — that is not hypothetical here, it is what
`_COMMITTED_STATUSES` saying "applied" while the UI wrote "submitted" already
cost: six applications the candidate had sent were rewound and offered back to
them to send again. A second system with its own opinion of what "applied" means
is that same failure with a network in the middle.

`_Client` therefore has exactly three public methods and no way to read pipeline
state beyond the row keys it needs to update rather than duplicate.
`tests/test_notion_mirror.py` fails if a fourth appears.

`NOTION_VERSION` is pinned in `config.py` and must stay pinned. Notion shipped at
least five breaking versions in H1 2026, and `2025-09-03` is the one that split a
database into a container holding data sources — an unpinned client does not fail
loudly, it starts writing to the wrong shape.

## Refresh the Gmail snapshot when you have the connector

The daily fetch reconciles what was actually sent — but it cannot read the
mailbox. launchd has no Gmail credentials and this API deliberately holds none,
so the only thing that can look is an agent session with the Gmail connector.

**If you are such a session, refresh the snapshot before finishing.** It is two
searches and it keeps the 07:00 run useful:

```text
search_threads("in:sent newer_than:30d")   -> ~/careeros/gmail-snapshot/sent.json
search_threads("in:inbox newer_than:30d")  -> ~/careeros/gmail-snapshot/inbox.json
```

`daily_fetch.py` consumes them, marks recruiter replies that went out, flags
applications blocked on a step, and skips entirely if the snapshot is over
36 hours old rather than reconciling against a mailbox that has moved on.
The directory is gitignored — it is raw mailbox content.

`prefill_apply.py` needs `requirements-dev.txt` plus `playwright install chromium`.
It is local-only and never runs in CI.

Use `./.venv/bin/python`, not bare `python3` — dependencies (`fastapi`, `httpx`,
`reportlab`, `python-docx`, `pypdf`, `dotenv`) live in the venv. API keys go in a
gitignored `.env`, never in code or chat.

## Architecture

```text
app/
  main.py        FastAPI routes
  sources.py     Greenhouse / Ashby / Muse / Arbeitnow / RemoteOK — public APIs only
  discovery.py   fetch + 15-min cache + filtering
  prescreen.py   cheap title ranking, run BEFORE full scoring
  skills.py      requirement extraction (TWO layers — see below)
  scoring.py     deterministic fit scoring against evidence. No LLM, by design.
  eligibility.py hard knockouts: citizenship, clearance, ITAR, sponsorship
  tailor.py      bullet selection, ordering, summary, projects
  phrasing.py    rule-based synonym substitution (REPLACE / AUGMENT)
  overrides.py   hand-written per-job bullets + candidate edits
  documents.py   PDF (reportlab) + DOCX (python-docx), ATS-safe
  resume_qa.py   recruiter-style checks on the resume and the rendered PDF
  outreach.py    email + LinkedIn drafts
  contacts.py    Hunter → Apollo → Tomba → Anymail failover
```

## Design decisions that cost real debugging — do not undo

**Requirement extraction is two-layer** (`skills.py`). A canonical vocabulary *plus*
open-vocabulary detection of requirement-shaped terms it does not recognise. With only the
closed list, anything outside it was not merely unscored but *unseen*, so it could never
be reported as a gap — and the bias was worst on specialised roles. A mortgage compliance
posting requiring HMDA and LOS extracted five generic requirements, matched all five, and
scored **98/100 with "no gaps"** for a job the candidate was not qualified for.

**Aliases must match in both directions.** They are used to find a requirement in a
posting *and* in the evidence. One-directional matching filed a JD's "risk mitigation"
under the canonical "Risk management", then scored evidence declaring that exact phrase as
a gap.

**Bullet selection is marginal coverage, not per-bullet score** (`tailor.py`). Ranking
bullets independently kept picking near-duplicates — two bullets both answering
"requirements gathering" while the only domain-specific evidence on the page was dropped.
**Rarity weighting was tried and reverted**: it made a requirement *less* valuable the more
evidence backed it, so importing fifteen genuine LOS claims demoted every LOS bullet.
Do not re-add it.

**Override containment is deliberately asymmetric** (`overrides.py`). `verify_override`
rejects the assistant's rewrites that introduce figures, mid-sentence proper nouns or
padding — but for `author="user"` the same failures are **warnings, not vetoes**. The
candidate knows things the evidence file does not record yet; refusing their own history
would be wrong. The text saves, marked unverified, and its source line says so.

**Word-boundary matching everywhere** (`scoring._contains`). Plain substring matching had
"Go" matching "goals" and "R" matching almost everything. Short aliases like `los` will
fire on "close" and "Los Angeles" — every alias for a short acronym must be unambiguous.

**Prescreen before scoring** (`prescreen.py`). Full scoring parses whole descriptions
(~3.5ms each), so it cannot run on ~3,000 jobs. Search used to score the first 120 *in
fetch order* and sort those by fit — a ranked list that was not a ranking. A 98-scoring
role sat at position 452, unscored, while sales roles led.

**Scoring is memoised per profile object** (`scoring.score_job_cached`). Tied to the
loaded profile instance, so editing the evidence file drops every stale score.

**A saved summary or headline edit pins that field forever.** It wins over the generated
one by design, which is right for a deliberate rewrite — but it does not benefit from
later generator improvements. One saved before the summary learned to name the MS in
Business Analytics silently kept dropping that credential from the highest-scoring
resume. If a stored edit only *matches* what the generator would produce, delete it
rather than keep it.

## Verification before calling anything done

- Both test files pass.
- The rendered PDF is checked, not assumed: `resume_qa.check_pdf` for extractable text,
  section order, contact survival, page count.
- Re-extract the PDF text and confirm claimed keywords are present **and
  unclaimed ones are absent**. Bugs here are invisible in JSON.
- A resume is **one page** — `documents.build_pdf` enforces it, and
  `tests/test_resume_one_page.py` renders real documents through the real
  pipeline to check. `MAX_PAGES` was 2 and nothing asserted it, so generated
  documents ran to two pages while the hand-cut versions fit on one.
- Nothing that ships to an employer carries a toolchain fingerprint.
  `resume_qa.check_fingerprints` is the check: typography no one types by hand,
  a tool name in the file properties, generated-prose vocabulary, and structural
  uniformity. It must return `[]` for every generated resume.
- **Uniformity is the one worth understanding.** No local check predicts what a
  third-party AI detector will say, and none should claim to. What is checkable
  is the property those tools are built around: a page where every bullet is the
  same length and built the same way reads as machine-made to a person long
  before any tool is involved. The first draft of the composed STAR bullets put
  a semicolon in 18 of 19 — every fact true, every source cited, and it still
  read as generated. Vary the construction.
- Employment bullets are composed situation-action-result, three per role, from
  `~/careeros/star_bullets.json`. Each names the claims it draws on and
  `star.load` verifies it against their union through `verify_override`. Never
  loosen that gate to fit a phrasing; rewrite the phrasing in the claims' own
  vocabulary, which is what the three rejected ones needed.
