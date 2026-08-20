# Technical Interview Lab Design

**Date:** 2026-08-20  
**Status:** Approved design  
**Repositories:** `careeros-api`, `careeros-web`

## Purpose

CareerOS already prepares behavioural interview answers against the candidate's
evidence. The Technical Interview Lab extends that preparation into the skills
the target roles test directly: SQL, statistics, analytical cases, KPI design,
data interpretation, Python/Pandas, data modelling, ETL and data-quality
debugging, dashboard design, and role-specific mixed interviews.

The product must teach as well as assess. Guided practice explains a concept,
shows a worked example, asks the learner to retrieve and apply it, gives a
debrief, and then checks transfer on a different data shape. Interview mode
removes help, freezes a timed question manifest, and reveals grading only after
the complete round is submitted or expires.

## Product decisions

- The primary layout is a **Guided Learning Path**: Concept → Example → Practice
  → Review.
- The first release includes all three content groups:
  - analytics core;
  - data stack; and
  - role-specific question banks.
- SQL uses CodeMirror 6.
- Graded curriculum uses versioned synthetic datasets.
- A private CareerOS snapshot is available as an optional, ungraded sandbox.
- Interview rounds are selectable at 30, 45, or 60 minutes.
- Interview feedback appears only after the full round.
- Guided hints unlock progressively:
  - a conceptual nudge after one failed check;
  - a relevant pattern after two failed checks; and
  - a worked solution only after an explicit reveal.
- Revealing a solution prevents that attempt from awarding independent mastery.
- Python/Pandas runs in a browser Web Worker through Pyodide.
- SQL runs in a disposable backend worker, never in the FastAPI process.

## Research basis

The learning design uses retrieval practice, explanatory feedback, transfer
problems, and interleaved mixed rounds. Classroom research found that retrieval
practice improved later performance and that interleaved retrieval produced an
additional benefit over blocked quizzes. Undergraduate problem-solving research
also found stronger performance on novel, harder problems after interleaved
practice, despite learners believing the method was less effective.

Challenge-based gamification has produced positive learning effects in
statistics and digital-literacy experiments. CareerOS therefore uses meaningful
missions, visible mastery, personal bests, debugging challenges, and unlockable
scenarios. It deliberately avoids public leaderboards, points for mere activity,
punishing streak loss, and random rewards.

Primary sources:

- Sana and Yan, *Interleaving Retrieval Practice Promotes Science Learning*
  (2022): https://pubmed.ncbi.nlm.nih.gov/35436145/
- Samani and Pan, *Interleaved practice enhances memory and problem-solving
  ability in undergraduate physics* (2021):
  https://pubmed.ncbi.nlm.nih.gov/34772951/
- Legaki et al., *The effect of challenge-based gamification on learning: An
  experiment in the context of statistics education* (2020):
  https://pmc.ncbi.nlm.nih.gov/articles/PMC7293851/
- Alnuaim, *The Impact and Acceptance of Gamification by Learners in a Digital
  Literacy Course at the Undergraduate Level* (2024):
  https://pmc.ncbi.nlm.nih.gov/articles/PMC11363743/

## Curriculum

### Track 1: Analytics Core

1. SQL Expeditions
   - filtering and projection;
   - joins and grain;
   - aggregation;
   - subqueries and CTEs;
   - window functions;
   - dates and cohorts;
   - nulls, duplicates, ties, and edge cases;
   - query explanation and debugging.
2. Experiment Lab
   - descriptive statistics;
   - sampling and bias;
   - confidence intervals;
   - hypothesis tests;
   - A/B-test design;
   - power, novelty, and guardrail metrics.
3. Metric Forge
   - north-star and input metrics;
   - KPI trees;
   - denominator and grain mistakes;
   - guardrails and unintended incentives;
   - financial and revenue metrics.
4. Signal Room
   - chart interpretation;
   - anomalies and seasonality;
   - cohort and funnel diagnosis;
   - choosing an action under uncertainty.

### Track 2: Data Stack

1. Python Workshop
   - Python data structures;
   - Pandas filtering, grouping, joining, reshaping, and dates;
   - debugging incorrect transformations;
   - data validation and tests.
2. Warehouse Architect
   - grain;
   - facts and dimensions;
   - slowly changing dimensions;
   - star schemas;
   - incremental models.
3. Broken Pipeline
   - late and duplicate data;
   - schema drift;
   - idempotency;
   - reconciliation;
   - observability and quality checks.
4. Dashboard Review
   - audience and decision;
   - metric definition;
   - visual hierarchy;
   - misleading encodings;
   - freshness and trust.

### Track 3: Role Missions

Mixed scenarios target:

- Data Analyst;
- Business Analyst;
- Product Analyst;
- Revenue or Financial Analyst; and
- Analytics Engineer.

Each role mission combines skills from the first two tracks in a realistic
scenario. A mission is not a trivia bank: it asks the learner to produce and
defend an analysis, identify ambiguity, and explain trade-offs.

## Learning loop

Each concept follows:

1. **Brief** — why the skill matters in the target roles.
2. **Worked example** — a narrated solution with decisions made explicit.
3. **Challenge** — unaided retrieval and application.
4. **Debrief** — correctness plus the reason the solution works or fails.
5. **Transfer** — the same concept on different tables or a different business
   context.
6. **Boss round** — interleaved, timed application with other concepts.

An optional daily challenge selects a due retrieval item or transfer problem.
Missing a day has no penalty. Personal bests measure demonstrated improvement,
not time spent in the app.

## Architecture

The feature is a bounded domain inside the existing modular monolith.

### Backend components

- `app/technical_learning/curriculum.py`
  - loads and validates versioned curriculum definitions;
  - serves public manifests without expected answers;
  - freezes drill and dataset versions for attempts and sessions.
- `app/technical_learning/datasets.py`
  - creates deterministic synthetic datasets;
  - refreshes the optional private CareerOS sandbox;
  - maps allow-listed dataset IDs to server-owned paths.
- `app/technical_learning/query_supervisor.py`
  - launches a disposable SQL worker;
  - sends one compact JSON request over stdin;
  - reads one bounded JSON response;
  - enforces a wall-clock deadline and kills overruns.
- `app/technical_learning/sql_worker.py`
  - runs in a separate process;
  - installs CPU, memory, and output limits;
  - opens one allow-listed database read-only and immutable;
  - installs a SQLite authorizer that denies writes, attachment, pragmas,
    extension loading, and unsupported operations;
  - uses SQLite's progress handler as an instruction deadline;
  - returns capped result rows and exits.
- `app/technical_learning/grading.py`
  - compares ordered or unordered result multisets;
  - handles nulls, duplicate rows, numeric tolerances, and acceptable variants;
  - evaluates written responses against explicit rubric elements;
  - never uses an opaque model verdict as the source of mastery.
- `app/technical_learning/sessions.py`
  - owns guided attempts and timed-round transitions;
  - freezes interview manifests;
  - delays all interview correctness until the round ends.
- `app/technical_learning/progress.py`
  - computes drill clearance, concept mastery, transfer, personal bests, and
    next recommendations.
- `app/technical_learning/router.py`
  - exposes explicit Pydantic request and response models.

### Frontend components

- `/prep/technical` — mission map, due practice, mastery, and role readiness.
- `/prep/technical/[track]/[concept]` — Guided Learning Path.
- `/prep/technical/interview` — duration and role configuration plus timed
  workspace.
- `/prep/technical/results/[sessionId]` — scorecard, debrief, and review queue.
- CodeMirror 6 for SQL.
- Pyodide inside a dedicated Web Worker for Python/Pandas.
- Shared schema browser, result grid, hint ladder, case-response, rubric,
  debrief, mastery, and scorecard components.

## Execution boundary

FastAPI never executes user SQL. It sends only a dataset ID and SQL text to the
query supervisor. The supervisor resolves the dataset itself, launches a worker,
and accepts capped JSON rows back. The worker exits after one query.

The friendly SQL guard remains for clear error messages, but the SQLite
authorizer is the structural policy. Dataset paths are never accepted from the
browser. The worker receives no application object and imports no CareerOS
storage code.

Python has a different boundary. It executes through Pyodide inside a browser
Web Worker, with only the exercise fixtures explicitly passed to it. The browser
sends normalized output to the trusted backend grader. The browser cannot award
itself progress.

## Curriculum and progress model

Curriculum is declarative, versioned data committed with the backend. Each drill
defines:

- ID, track, concept, difficulty, and prerequisites;
- dataset and dataset version;
- prompt and public schema;
- expected result or explicit rubric;
- ordering and tolerance rules;
- progressive hints;
- worked solution and debrief;
- transfer group; and
- interview eligibility.

Each attempt stores:

- curriculum, drill, and dataset versions;
- submitted answer;
- bounded execution metadata;
- deterministic grade and difference;
- hints unlocked and whether the solution was revealed;
- runtime and truncation state; and
- timestamp.

A drill clears only after an unaided pass. A concept reaches mastery after its
core drills and a transfer drill on a different data shape. The private sandbox
never contributes to mastery.

Timed sessions freeze a complete manifest at creation. Their state machine is:

`created → running → submitted | expired → graded`

Answers autosave, but grades remain hidden until submission or expiry. Server
time is authoritative. Old scorecards remain reproducible when the curriculum
changes.

## Error handling

- Syntax and type errors are ordinary learning results and appear inline.
- Wrong results show the first useful deterministic difference.
- Timeouts kill the worker and cannot award mastery.
- Truncated output cannot pass a drill requiring a complete result.
- Worker crashes leave the attempt retryable and ungraded.
- Missing frozen dataset versions produce a recoverable system error rather
  than silently switching versions.
- Pyodide failures preserve the answer and permit runtime restart.
- Interview expiry submits all autosaved answers together.
- Guided drafts are locally recoverable after network loss.
- API errors return sanitized codes and never worker internals or query text.

## Privacy and security

- Synthetic datasets are the only graded datasets.
- The private snapshot excludes email bodies, recruiter drafts, profile
  evidence, phone numbers, street addresses, and other unnecessary text.
- Saved query text is private and excluded from logs.
- SQL workers have independent CPU, memory, output, and wall-clock limits.
- Reference answers are not included in initial browser manifests.
- All progress writes happen after untrusted execution has ended.

## Verification

### Backend

- guard and authorizer bypass attempts;
- worker resource limits and process termination;
- deterministic dataset generation;
- ordered and unordered grading with nulls and duplicates;
- curriculum validation and version immutability;
- hint, solution, mastery, and transfer rules;
- timed-session transitions, expiry, autosave, and delayed grading;
- retry behavior after worker and dataset failures;
- private-snapshot exclusion; and
- migrations and API contracts.

### Frontend

- CodeMirror editing and keyboard execution;
- accessible schema navigation and result tables;
- progressive hints;
- Pyodide loading, execution failure, and restart;
- autosave and network recovery;
- 30-, 45-, and 60-minute rounds;
- no interview correctness leakage;
- expiry and scorecard behavior;
- mission unlocks and mastery; and
- mobile, reduced-motion, keyboard, and screen-reader use.

### End to end

CI completes one guided SQL drill, one Python/Pandas drill, one written
analytics case, and one timed mixed interview using synthetic fixtures. A
separate local verification command tests the optional private sandbox.

## Success criteria

- No user query can modify or inspect live CareerOS state.
- A failed or malicious query cannot stall the API.
- Old scorecards remain reproducible after curriculum changes.
- Mastery requires unaided success plus transfer.
- Interview mode reveals no correctness before the round ends.
- Every technical track has at least one complete
  learn–practice–transfer–interview path.
- The feature is enjoyable because challenges create meaningful progress, not
  because activity is converted into arbitrary points.

## Delivery shape

Implementation should proceed in vertical slices:

1. domain skeleton, migrations, curriculum validation, and dataset versioning;
2. disposable SQL worker and security tests;
3. SQL guided path;
4. timed SQL interview and scorecard;
5. Python/Pandas browser worker;
6. statistics, metric, case, modelling, ETL, and dashboard question types;
7. role missions, mixed interviews, mission map, and spaced recommendations;
8. accessibility, end-to-end verification, and private sandbox.

Each slice must be usable and tested before the next expands the surface.
