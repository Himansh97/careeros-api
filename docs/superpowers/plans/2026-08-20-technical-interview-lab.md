# Technical Interview Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Deliver a tested Technical Interview Lab that teaches and assesses SQL, analytics, data-stack, and role-specific interview skills through guided practice and timed mixed rounds.

**Architecture:** Add a bounded `app.technical_learning` domain to the FastAPI monolith. Declarative, versioned curriculum drives public manifests, deterministic graders, mastery, and frozen timed sessions. SQL runs only in a one-shot subprocess against server-owned synthetic databases; Python runs in a browser Web Worker and only normalized output is trusted by the backend grader. The Next.js app adds mission-map, guided-path, interview-workspace, and scorecard routes under `/prep/technical`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, unittest/pytest-style tests, Next.js 16 App Router, React 19, TypeScript, TanStack Query, CodeMirror 6, Pyodide Web Worker, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-20-technical-interview-lab-design.md`

## Global Constraints

- Preserve `app/learn_sql.py` until its behavior is covered; then keep it as a compatibility facade rather than deleting the user's work.
- Never execute candidate SQL in FastAPI, accept filesystem paths from the browser, or grade against live CareerOS data.
- Never put real candidate data or PII in committed curriculum, datasets, fixtures, snapshots, logs, or tests.
- Follow red-green-refactor for each behavior. A production function is added only after its focused test fails for the expected reason.
- Interview answers may autosave, but correctness and reference answers stay hidden until a session reaches `submitted` or `expired` and is graded.
- Frontend disconnected states must remain honest; never substitute mock mastery or attempts for unavailable API data.

---

### Task 1: Versioned curriculum and deterministic synthetic datasets

**Files:**
- Create: `app/technical_learning/__init__.py`
- Create: `app/technical_learning/models.py`
- Create: `app/technical_learning/curriculum.py`
- Create: `app/technical_learning/curriculum_v1.json`
- Create: `app/technical_learning/datasets.py`
- Create: `tests/test_technical_curriculum.py`
- Create: `tests/test_technical_datasets.py`

**Interfaces:**
- `load_curriculum(version: str = "2026.08.1") -> Curriculum`
- `public_curriculum(version: str = "2026.08.1") -> dict[str, Any]`
- `get_drill(drill_id: str, version: str) -> Drill`
- `ensure_dataset(dataset_id: str, version: str) -> Path`
- `dataset_schema(dataset_id: str, version: str) -> list[dict[str, Any]]`

**Steps:**
1. Write failing curriculum tests for unique IDs, valid prerequisites, hidden expected answers, one learn/practice/transfer/interview path per technical track, all five role missions, and immutable version lookup.
2. Run `./.venv/bin/python -m unittest tests.test_technical_curriculum -v` and confirm missing-module/behavior failures.
3. Implement Pydantic curriculum models, loader validation, and a v1 curriculum containing representative complete paths for SQL, statistics, metrics, chart interpretation, Python/Pandas, data modelling, ETL/data quality, dashboard design, and five role missions.
4. Write failing dataset tests for byte-stable deterministic generation, allow-listed IDs only, no PII columns, schema exposure, and version-specific paths.
5. Run `./.venv/bin/python -m unittest tests.test_technical_datasets -v` and confirm expected failures.
6. Implement compact deterministic `commerce` and `lending` datasets plus an explicitly ungraded, opt-in private snapshot builder that omits free text and contact data.
7. Run both focused suites and the existing backend suite.
8. Commit: `Build versioned technical curriculum and datasets`.

### Task 2: Disposable SQL execution boundary and deterministic grading

**Files:**
- Create: `app/technical_learning/sql_policy.py`
- Create: `app/technical_learning/sql_worker.py`
- Create: `app/technical_learning/query_supervisor.py`
- Create: `app/technical_learning/grading.py`
- Modify: `app/learn_sql.py`
- Create: `tests/test_technical_sql.py`
- Create: `tests/test_technical_grading.py`

**Interfaces:**
- `guard_sql(sql: str) -> str`
- Worker protocol: stdin `{datasetPath, sql, rowLimit, instructionLimit}`; stdout one bounded `{ok, columns, rows, rowCount, truncated, errorCode?, message?}` object.
- `run_sql(dataset_id: str, dataset_version: str, sql: str, timeout_s: float = 3.0) -> QueryResult`
- `grade_rows(expected, actual, *, ordered, numeric_tolerance) -> Grade`
- `grade_rubric(answer: dict | str, rubric: list[RubricElement]) -> Grade`

**Steps:**
1. Write failing policy/worker tests for empty input, stacked statements, comment bypasses, ATTACH/PRAGMA/writes, read-only authorizer denial, row caps, instruction timeout, wall timeout, invalid dataset IDs, and worker termination.
2. Run the focused test and confirm expected failures.
3. Implement friendly guard, worker resource limits, read-only immutable SQLite connection, authorizer, progress handler, bounded JSON protocol, and subprocess supervisor. Do not import CareerOS storage from the worker.
4. Write failing grader tests for order-sensitive and order-insensitive multisets, duplicates, nulls, numeric tolerance, truncation refusal, and explicit rubric feedback.
5. Implement the minimal deterministic graders and turn `app/learn_sql.py` into a compatibility facade over the new package.
6. Run focused and regression suites.
7. Commit: `Isolate and grade technical SQL safely`.

### Task 3: Attempts, mastery, hints, and frozen interview sessions

**Files:**
- Create: `app/technical_learning/store.py`
- Create: `app/technical_learning/progress.py`
- Create: `app/technical_learning/sessions.py`
- Create: `tests/test_technical_progress.py`
- Create: `tests/test_technical_sessions.py`

**Interfaces:**
- `submit_guided_attempt(drill_id, answer, *, hints_unlocked, solution_revealed, curriculum_version) -> AttemptResult`
- `progress_overview() -> ProgressOverview`
- `recommend_next(limit: int = 3) -> list[Recommendation]`
- `create_session(duration_minutes: Literal[30,45,60], role: str | None) -> Session`
- `start_session(session_id) -> Session`
- `save_answer(session_id, question_id, answer) -> SessionAnswer`
- `submit_session(session_id, now=None) -> Scorecard`
- `get_session(session_id, now=None) -> Session | Scorecard`

**Steps:**
1. Write failing progress tests for hint thresholds, explicit solution reveal, unaided clearance, different-shape transfer mastery, sandbox exclusion, personal best, and due recommendations without streak punishment.
2. Implement transactional SQLite tables and monotonic progress rules.
3. Write failing session tests for allowed durations, frozen manifests, state transitions, authoritative expiry, idempotent autosave, no pre-submit grading leakage, mixed question types, and reproducible scorecards.
4. Implement session lifecycle and delayed grading; store frozen public question and private grading snapshots separately.
5. Run focused suites plus the full backend suite.
6. Commit: `Persist technical mastery and timed interviews`.

### Task 4: FastAPI contracts and integration

**Files:**
- Create: `app/technical_learning/router.py`
- Modify: `app/main.py`
- Create: `tests/test_technical_api.py`

**Routes:**
- `GET /api/prep/technical`
- `GET /api/prep/technical/curriculum`
- `GET /api/prep/technical/drills/{drill_id}`
- `POST /api/prep/technical/run`
- `POST /api/prep/technical/attempts`
- `POST /api/prep/technical/sessions`
- `POST /api/prep/technical/sessions/{session_id}/start`
- `PATCH /api/prep/technical/sessions/{session_id}/answers/{question_id}`
- `POST /api/prep/technical/sessions/{session_id}/submit`
- `GET /api/prep/technical/sessions/{session_id}`

**Steps:**
1. Write failing TestClient contract tests for every route, validation errors, sanitized worker failures, hidden expected answers, and no interview feedback leakage.
2. Implement explicit Pydantic requests/responses and a small APIRouter; include it once from `app/main.py`.
3. Run `./.venv/bin/python -m unittest tests.test_technical_api -v` and the full backend suite.
4. Commit: `Expose the Technical Interview Lab API`.

### Task 5: Frontend domain client, mission map, and guided learning path

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `.gitignore`
- Create: `vitest.config.ts`
- Create: `src/lib/api/technical-learning.ts`
- Create: `src/lib/technical-learning/state.ts`
- Create: `src/lib/technical-learning/state.test.ts`
- Create: `src/components/technical-learning/mission-map.tsx`
- Create: `src/components/technical-learning/schema-browser.tsx`
- Create: `src/components/technical-learning/sql-editor.tsx`
- Create: `src/components/technical-learning/result-grid.tsx`
- Create: `src/components/technical-learning/hint-ladder.tsx`
- Create: `src/components/technical-learning/guided-path.tsx`
- Create: `src/app/(app)/prep/technical/page.tsx`
- Create: `src/app/(app)/prep/technical/[track]/[concept]/page.tsx`
- Modify: `src/app/(app)/prep/page.tsx`
- Modify: `src/config/nav.ts`

**Steps:**
1. Install CodeMirror, Vitest, jsdom, and Testing Library dependencies.
2. Write failing state-machine tests for Brief → Example → Practice → Review → Transfer, hint thresholds, reveal disqualification, and locally recoverable guided drafts.
3. Implement typed API contracts and reducer/state utilities.
4. Write failing component tests for accessible mission status, SQL keyboard execution, progressive hints, schema/result semantics, disconnected state, and no expected-answer leakage.
5. Implement the mission map and guided route with CodeMirror 6, result grid, debrief, transfer, personal best, and meaningful unlock states.
6. Link behavioural prep to Technical Prep and make the nav active for the whole `/prep` route family.
7. Run `npm test`, `npm run lint`, and `npx tsc --noEmit`.
8. Commit: `Build the guided Technical Interview Lab`.

### Task 6: Browser Python worker and structured technical cases

**Files:**
- Create: `public/workers/technical-python.worker.js`
- Create: `src/lib/technical-learning/python-runner.ts`
- Create: `src/lib/technical-learning/python-runner.test.ts`
- Create: `src/components/technical-learning/python-editor.tsx`
- Create: `src/components/technical-learning/case-response.tsx`
- Modify: `src/components/technical-learning/guided-path.tsx`

**Steps:**
1. Write failing runner tests for load, execution, normalized output, timeout/restart, and answer preservation.
2. Implement a dedicated Web Worker that loads Pyodide, receives only fixture data/code, captures bounded normalized output, and never awards progress.
3. Write failing structured-case tests for explicit rubric fields and completeness feedback.
4. Implement Python/Pandas and written-case guided experiences shared by statistics, metrics, modelling, ETL, dashboard, and role-mission drills.
5. Run unit, lint, and type checks.
6. Commit: `Add Python and structured case practice`.

### Task 7: Timed mixed interview workspace and delayed scorecard

**Files:**
- Create: `src/lib/technical-learning/interview-state.ts`
- Create: `src/lib/technical-learning/interview-state.test.ts`
- Create: `src/components/technical-learning/interview-config.tsx`
- Create: `src/components/technical-learning/interview-workspace.tsx`
- Create: `src/components/technical-learning/scorecard.tsx`
- Create: `src/app/(app)/prep/technical/interview/page.tsx`
- Create: `src/app/(app)/prep/technical/results/[sessionId]/page.tsx`

**Steps:**
1. Write failing tests for 30/45/60 configuration, server-derived countdown, autosave retry, expiry, no correctness before completion, and graded scorecards.
2. Implement the configuration and mixed-question workspace using frozen manifests and server time.
3. Implement scorecards with per-skill feedback, review queue, personal best, and next recommended practice; do not use a public leaderboard or activity points.
4. Run frontend tests, lint, typecheck, and build.
5. Commit: `Ship timed technical interviews and scorecards`.

### Task 8: End-to-end verification, accessibility, and documentation

**Files:**
- Create: `scripts/verify_technical_lab.py`
- Create: `tests/test_technical_e2e.py`
- Create: `careeros-web/e2e/technical-learning.spec.ts` only if the existing environment supports browser tests; otherwise keep deterministic browser verification in the in-app browser and document it.
- Modify: `README.md`
- Modify: `careeros-web/README.md`

**Steps:**
1. Write a failing backend end-to-end test that completes one SQL guided challenge and transfer, one normalized Python challenge, one written case, and one timed mixed interview.
2. Implement the local verification script using only synthetic fixtures.
3. Run the complete backend suite, focused security tests, frontend tests, lint, typecheck, and production build.
4. Start API and web locally; verify the real flow at desktop and mobile widths, keyboard-only operation, focus visibility, reduced motion, no horizontal overflow, disconnected states, autosave recovery, expiry, and delayed grading.
5. Verify malicious SQL cannot write, attach, inspect arbitrary files, stall FastAPI, or leak paths/query text in API errors.
6. Update READMEs with setup, runtime boundaries, curriculum extension, and verification commands.
7. Review `git diff --check`, status, and committed files for PII, secrets, generated databases, browser artifacts, placeholders, and accidental `.superpowers` files.
8. Commit: `Verify and document the Technical Interview Lab`.

## Final Verification Commands

```bash
cd /Users/himanshusrivastava/careeros-api
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python scripts/verify_technical_lab.py
git diff --check

cd /Users/himanshusrivastava/careeros-web
npm test
npm run lint
npx tsc --noEmit
npm run build
git diff --check
```

## Plan Self-Review

- Every approved curriculum group and each of the five role missions has a declared implementation path.
- SQL, Python, and written-case execution/grading boundaries are explicit and type-consistent.
- Guided hints/mastery and timed-session delayed grading are covered at domain, API, UI, and end-to-end levels.
- Security, privacy, accessibility, disconnected-state honesty, and reproducibility are verification gates rather than follow-up work.
- No task relies on real candidate data, external model judgment, or browser-owned mastery.
