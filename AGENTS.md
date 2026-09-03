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
3. **PII never enters git.** Resume text is derived from `career_evidence.json`, which
   `careeros` gitignores as personal data — so it must not be inlined in Python either.
   Per-job overrides live in `~/careeros/overrides/<job_id>.json` (gitignored) and are
   applied by the generic `scripts/apply_overrides.py`. `.gitignore` blocks
   `scripts/align_*.py` and `scripts/import_*.py` so a one-off cannot reintroduce this.
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
- Nothing that ships to an employer carries a toolchain fingerprint. No em or
  en dashes, no smart quotes, and `/Producer` and `/Creator` are blank.
