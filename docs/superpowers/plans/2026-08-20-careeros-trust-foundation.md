# CareerOS Trust Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CareerOS persistence and job liveness evidence-backed so a cold scheduled run, source outage, invalid state write, or legacy orphan cannot silently corrupt the candidate pipeline.

**Architecture:** A single SQLite runtime applies immutable numbered migrations and owns connection policy. Discovery records source generations and durable observations, liveness consumes source-aware evidence, and application state/blocker policy is centralized above repository queries. Operational scripts expose backup, restore, status, and conservative repair without persisting or printing candidate content.

**Tech Stack:** Python 3.13, FastAPI 0.141, Pydantic 2.13, stdlib `sqlite3`, `unittest`/pytest-compatible tests.

**Spec:** `docs/superpowers/specs/2026-08-20-careeros-trust-foundation-design.md`

## Global Constraints

- No auto-submit and no fabricated candidate data.
- Private candidate data and the live `careeros.db` never enter git or fixtures.
- Existing endpoint paths remain unchanged.
- Writable connections use WAL, `foreign_keys=ON`, and a 5,000 ms busy timeout.
- Read-only connections use SQLite URI mode and refuse a missing database.
- Migrations are numbered, transactional, idempotent, and checksum-verified.
- Legacy `applied` and `interviewing` remain readable as `submitted` and `interview`; new writes reject them.
- A failed/degraded source is never equivalent to an empty healthy source.
- Two separate healthy generations, or one explicit direct closure, are required to close a posting.
- Repair defaults to dry-run and never prints candidate content.

---

### Task 1: Central SQLite runtime and immutable migration ledger

**Files:**
- Create: `app/db.py`
- Create: `app/migrations/__init__.py`
- Create: `app/migrations/registry.py`
- Create: `app/migrations/versions/0001_baseline.sql`
- Test: `tests/test_db_runtime.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `connect(*, read_only: bool = False, path: Path | None = None) -> ContextManager[sqlite3.Connection]`
- Produces: `transaction(mode: str = "DEFERRED", *, path: Path | None = None) -> ContextManager[sqlite3.Connection]`
- Produces: `initialize(*, path: Path | None = None) -> MigrationStatus`
- Produces: `integrity_report(*, path: Path | None = None) -> dict[str, object]`
- Produces: immutable `Migration(version: int, name: str, sql: str, checksum: str)` registry entries.

- [ ] **Step 1: Write failing runtime tests**

  Add tests that create a temporary path, assert read-only open refuses a missing file, writable connections report `foreign_keys=1`, `busy_timeout=5000`, and `journal_mode=wal`, and verify a connection cannot be used after its context exits.

- [ ] **Step 2: Run the focused tests and confirm failure**

  Run: `./.venv/bin/python -m pytest tests/test_db_runtime.py -q`

  Expected: import failure for `app.db`.

- [ ] **Step 3: Implement the connection and transaction context managers**

  `connect` resolves `path or config.DB_PATH`, creates only the parent for writable mode, uses `file:<quoted-path>?mode=ro` for read-only mode, sets `sqlite3.Row`, `PRAGMA busy_timeout=5000`, and `PRAGMA foreign_keys=ON`; writable mode additionally requests WAL. `transaction` validates the mode against `{"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}`, executes `BEGIN <mode>`, commits on success, rolls back on exception, and always closes.

- [ ] **Step 4: Write failing migration tests**

  Cover a fresh database, an existing representative legacy database, repeat initialization, rollback of a deliberately failing migration, out-of-order registry rejection, and refusal when an applied migration checksum no longer matches.

- [ ] **Step 5: Implement the migration registry and baseline**

  `0001_baseline.sql` contains the current production table/index definitions assembled from repository modules. `initialize` creates `schema_migrations(version, name, applied_at, checksum)`, baselines an existing schema only when its required table signatures match, applies pending SQL inside one exclusive transaction, then requires `PRAGMA foreign_key_check` to return no rows and `PRAGMA integrity_check` to return exactly `ok`.

- [ ] **Step 6: Run focused tests**

  Run: `./.venv/bin/python -m pytest tests/test_db_runtime.py tests/test_migrations.py -q`

  Expected: PASS.

- [ ] **Step 7: Commit**

  `git add app/db.py app/migrations tests/test_db_runtime.py tests/test_migrations.py && git commit -m "Add the CareerOS database runtime"`

### Task 2: Trust schema, canonical states, and structured blockers

**Files:**
- Create: `app/migrations/versions/0002_trust_foundation.sql`
- Create: `app/application_states.py`
- Create: `app/blockers.py`
- Test: `tests/test_application_states.py`
- Test: `tests/test_blockers.py`

**Interfaces:**
- Produces: `ApplicationState(str, Enum)`, `normalize_legacy_state(value: str) -> ApplicationState`, `validate_transition(current, target, *, repair=False) -> ApplicationState`.
- Produces: `list_blockers(application_id: str) -> list[dict]`, `open_blocker(...)->dict`, and `resolve_blocker(blocker_id: str)->dict`.
- Migration creates `source_generation`, `job_observation`, `liveness_event`, `application_blocker`, `quarantined_orphan`, and `repair_event` with foreign keys and supporting indexes.

- [ ] **Step 1: Write state-machine tests**

  Test every allowed edge from the spec, idempotence, legacy read normalization, rejection of legacy values on new writes, backward-transition refusal, terminal-state refusal, and repair-only backward movement with a non-empty reason.

- [ ] **Step 2: Implement canonical state policy**

  Define only the canonical values `discovered, qualified, tailoring, draft, ready, submitted, recruiter_contacted, screening, interview, offer, rejected, withdrawn`. Keep the transition adjacency map explicit and return the normalized target; raise `InvalidApplicationState` or `InvalidApplicationTransition` with stable `code` values.

- [ ] **Step 3: Write blocker migration/repository tests**

  Build temporary legacy rows whose `next_action` values include a known closure, known approval action, and unknown prose. Assert known phrases become structured blockers, unknown prose becomes an informational `legacy_note`, duplicate runs are idempotent, and resolved blockers stamp `resolved_at`.

- [ ] **Step 4: Implement trust migration and blocker repository**

  Use foreign keys with `ON DELETE RESTRICT` for evidence records. Store blocker evidence as canonical JSON with sorted keys. Treat `summary` as copy only; all decisions filter on `kind`, `severity`, and `state`.

- [ ] **Step 5: Run focused tests**

  Run: `./.venv/bin/python -m pytest tests/test_application_states.py tests/test_blockers.py tests/test_migrations.py -q`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add app/application_states.py app/blockers.py app/migrations/versions/0002_trust_foundation.sql tests/test_application_states.py tests/test_blockers.py tests/test_migrations.py && git commit -m "Add canonical states and structured blockers"`

### Task 3: Move repositories off request-time schema DDL

**Files:**
- Modify: `app/store.py`
- Modify: `app/automation.py`
- Modify: `app/compose.py`
- Modify: `app/contacts.py`
- Modify: `app/imported.py`
- Modify: `app/interview_intel.py`
- Modify: `app/interview_practice.py`
- Modify: `app/outreach_store.py`
- Modify: `app/overrides.py`
- Modify: `app/recruiter_messages.py`
- Modify: `app/technical_learning/store.py`
- Modify: `app/usage.py`
- Modify: `app/main.py`
- Test: `tests/test_repository_connections.py`

**Interfaces:**
- Consumes: `app.db.connect`, `app.db.transaction`, and `app.db.initialize`.
- Produces: existing repository function signatures unchanged.

- [ ] **Step 1: Write a failing repository policy test**

  Parse/import production modules and assert they contain no `CREATE TABLE`, `ALTER TABLE`, or direct connection to the CareerOS database. Exempt `app/technical_learning/datasets.py`, which creates isolated practice datasets rather than the system database.

- [ ] **Step 2: Run the policy test and confirm current violations**

  Run: `./.venv/bin/python -m pytest tests/test_repository_connections.py -q`

- [ ] **Step 3: Replace module-local connection/schema helpers**

  Import the shared runtime in each repository module, remove module-level DDL and opportunistic column migration, and retain transaction boundaries at the existing write-call level. Call `initialize()` once from FastAPI lifespan before serving requests and once at the start of CLI entry points that write.

- [ ] **Step 4: Run repository and existing subsystem tests**

  Run: `./.venv/bin/python -m pytest tests/test_repository_connections.py tests/test_recruiter_messages.py tests/test_outreach_drafts.py tests/test_pipeline_signals.py tests/test_usage.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add app tests/test_repository_connections.py && git commit -m "Route persistence through the shared database runtime"`

### Task 4: Durable source generations and carry-forward snapshots

**Files:**
- Create: `app/discovery_store.py`
- Modify: `app/discovery.py`
- Modify: `app/sources.py`
- Test: `tests/test_discovery_generations.py`
- Modify: `tests/test_discovery_cache.py`

**Interfaces:**
- Produces: `SourceResult(source_key: str, state: SourceState, jobs: tuple[dict, ...], error_code: str | None)`.
- Produces: `DiscoverySnapshot(jobs: tuple[dict, ...], sources: tuple[SourceHealth, ...], generated_at: str)`.
- Produces: `record_generation(result: SourceResult) -> SourceGeneration`, `current_snapshot() -> DiscoverySnapshot`, and `prune_generations(keep=30) -> int`.
- Keeps: `fetch_all_jobs(force=False) -> list[dict]` as a compatibility projection over the snapshot.

- [ ] **Step 1: Write failing generation tests**

  Test normalized payload hashing, healthy persistence, degraded persistence with a stable error code, cold-process recovery from the latest healthy generation, stale marking, 30-generation retention, and preservation of generations referenced by `liveness_event`.

- [ ] **Step 2: Implement discovery persistence**

  Record each configured adapter result separately. Persist only normalized public posting fields. A failed source records a generation with zero fresh observations, then `current_snapshot` carries forward that source's latest healthy jobs with `stale=True` and reports its current state.

- [ ] **Step 3: Adapt source execution**

  Convert raw adapter exceptions to `timeout`, `rate_limited`, `auth`, `parse`, or `network`; do not persist exception text. Keep source fetch concurrency and the 15-minute in-process cache, but make the durable snapshot authoritative across process restarts.

- [ ] **Step 4: Run discovery tests**

  Run: `./.venv/bin/python -m pytest tests/test_discovery_generations.py tests/test_discovery_cache.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add app/discovery.py app/discovery_store.py app/sources.py tests/test_discovery_generations.py tests/test_discovery_cache.py && git commit -m "Persist source-aware discovery generations"`

### Task 5: Evidence-backed liveness and same-transaction projections

**Files:**
- Rewrite: `app/liveness_sync.py`
- Modify: `app/liveness.py`
- Modify: `scripts/check_liveness.py`
- Modify: `scripts/daily_fetch.py`
- Test: `tests/test_liveness_evidence.py`
- Modify: `tests/test_liveness_sync.py`
- Modify: `tests/test_closed_postings.py`

**Interfaces:**
- Produces: `LivenessEvidence(job_id, source_key, observation_kind, generation_id, source_state, observed_at, detail_code)`.
- Produces: `apply_evidence(evidence: Iterable[LivenessEvidence]) -> LivenessSummary`.
- Consumes: durable source ownership and generations from `app.discovery_store`.

- [ ] **Step 1: Write failing policy tests**

  Assert present/direct-live yields live; direct-closed closes immediately; one healthy absence does not close; a second separate healthy generation does; degraded/unavailable/rate-limited/timeout/parse/missing ownership yields unknown; manual/imported postings ignore pool absence; later healthy presence retracts closure and records an event.

- [ ] **Step 2: Implement evidence decision policy**

  Build evidence objects instead of a raw fetched-ID set. Count only consecutive `absent` observations from the posting's own healthy source. Apply an application projection update and its `liveness_event` in the same immediate transaction.

- [ ] **Step 3: Update scheduled and manual callers**

  `daily_fetch.py` and `check_liveness.py` consume the same durable snapshot service. Dry-run prints counts by verdict/reason only. Neither caller treats failed-source jobs as absent.

- [ ] **Step 4: Run liveness and daily-flow tests**

  Run: `./.venv/bin/python -m pytest tests/test_liveness_evidence.py tests/test_liveness_sync.py tests/test_closed_postings.py tests/test_autopilot_gate.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add app/liveness.py app/liveness_sync.py scripts/check_liveness.py scripts/daily_fetch.py tests && git commit -m "Require durable evidence for posting closure"`

### Task 6: Enforce canonical writes at API and automation boundaries

**Files:**
- Modify: `app/store.py`
- Modify: `app/main.py`
- Modify: `app/pipeline_signals.py`
- Modify: `app/automation.py`
- Modify: `app/notion.py`
- Test: `tests/test_application_state_api.py`
- Modify: `tests/test_pipeline_signals.py`
- Modify: `tests/test_notion_mirror.py`

**Interfaces:**
- Consumes: `ApplicationState` and `validate_transition`.
- Produces: generic advance endpoint returning stable 422 errors for invalid state and 409 errors for illegal transitions.
- Produces: `repair_application_state(app_id, target, reason)` as the only backward-transition API in the repository layer; it is not exposed as a normal workflow endpoint.

- [ ] **Step 1: Write failing API and signal tests**

  Send every canonical state, unknown text, legacy write text, a backward edge, and a terminal escape through the API. Assert response status and stable error `code`. Verify recruiter signals can advance but cannot regress.

- [ ] **Step 2: Replace free-text status handling**

  Type request models with `ApplicationState`, normalize legacy values only while reading projections, and route all writes through `validate_transition`. Preserve submitted/response/outcome timestamps and idempotence.

- [ ] **Step 3: Update automation, analytics, and Notion projections**

  Replace `applying`, `applied`, and `interviewing` writes with canonical values. Keep legacy reads normalized at the boundary so the current database and frontend remain usable during rollout.

- [ ] **Step 4: Run focused tests**

  Run: `./.venv/bin/python -m pytest tests/test_application_state_api.py tests/test_pipeline_signals.py tests/test_notion_mirror.py tests/test_outcomes.py tests/test_readiness.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add app tests && git commit -m "Enforce canonical application transitions"`

### Task 7: Blocker projections and compatibility reads

**Files:**
- Modify: `app/store.py`
- Modify: `app/main.py`
- Modify: `app/alerts.py`
- Modify: `app/automation.py`
- Modify: `scripts/prune_approvals.py`
- Test: `tests/test_blocker_api.py`
- Modify: `tests/test_autopilot_gate.py`
- Modify: `tests/test_closed_postings.py`

**Interfaces:**
- Consumes: `app.blockers` repository functions.
- Produces: application JSON field `blockers: list[ApplicationBlocker]` and compatibility `nextAction` derived from the highest-severity open blocker or workflow state.

- [ ] **Step 1: Write failing API/projection tests**

  Assert open blockers appear, resolved blockers remain auditable but do not gate automation, informational legacy notes never block, and closure blockers replace string matching in alerts/automation.

- [ ] **Step 2: Replace machine decisions on `next_action`**

  Alerts, autopilot, and approval pruning query structured open blockers by kind/severity/state. Keep `nextAction` readable for the existing frontend, but generate it from structured state rather than parsing it.

- [ ] **Step 3: Run blocker and compatibility tests**

  Run: `./.venv/bin/python -m pytest tests/test_blocker_api.py tests/test_blockers.py tests/test_autopilot_gate.py tests/test_closed_postings.py tests/test_readiness.py -q`

  Expected: PASS.

- [ ] **Step 4: Commit**

  `git add app scripts tests && git commit -m "Make blockers structured and auditable"`

### Task 8: Backup, restore, status, and conservative repair CLIs

**Files:**
- Create: `app/db_ops.py`
- Create: `scripts/db_status.py`
- Create: `scripts/db_backup.py`
- Create: `scripts/db_restore.py`
- Create: `scripts/db_repair.py`
- Test: `tests/test_db_ops.py`
- Test: `tests/test_db_repair.py`

**Interfaces:**
- Produces: `backup_database(source: Path, destination: Path) -> BackupReport`.
- Produces: `restore_database(backup: Path, destination: Path) -> RestoreReport`.
- Produces: `repair_report(path: Path) -> dict` and `apply_repairs(path: Path, backup_path: Path) -> dict`.

- [ ] **Step 1: Write failing backup/restore tests**

  Assert online backup consistency, overwrite refusal, invalid-backup refusal, mandatory safety backup before restore, and matching integrity reports after restore.

- [ ] **Step 2: Implement operational primitives and thin CLIs**

  Use `sqlite3.Connection.backup`, validate with read-only integrity/foreign-key checks, create timestamped destinations, and print JSON counts/paths only. Never print row payloads.

- [ ] **Step 3: Write failing repair tests**

  Seed exactly three orphan timeline rows, an orphan approval, legacy states, known/unknown `next_action`, and ambiguous/repairable outage closure evidence. Assert dry-run is mutation-free, apply requires an existing verified backup, each orphan is copied byte-for-byte to `quarantined_orphan` before deletion, legacy states normalize, and only evidence-backed outage closures retract.

- [ ] **Step 4: Implement conservative repair**

  Run all changes in one immediate transaction and write `repair_event` counts/reason codes. Never invent parent IDs. Leave ambiguous closures open with a human-review blocker.

- [ ] **Step 5: Run ops tests**

  Run: `./.venv/bin/python -m pytest tests/test_db_ops.py tests/test_db_repair.py tests/test_migrations.py -q`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add app/db_ops.py scripts/db_*.py tests/test_db_ops.py tests/test_db_repair.py && git commit -m "Add verified database operations and repair tooling"`

### Task 9: Health/readiness integration and end-to-end verification

**Files:**
- Modify: `app/main.py`
- Modify: `README.md`
- Create: `tests/test_trust_foundation_integration.py`

**Interfaces:**
- Consumes: `integrity_report`, discovery source health, migration status, canonical state and blocker projections.
- Produces: enriched `GET /api/health` with `migrationVersion`, `integrity`, `foreignKeys`, `lastHealthyGenerationBySource`, and `staleSourceCount`.

- [ ] **Step 1: Write end-to-end integration tests**

  Cover a fresh scheduled process recovering durable jobs, total outage preserving applications, first/second healthy absence behavior, later presence retraction, representative legacy migration preserving all existing subsystems, and foreign-key failure rolling back without partial writes.

- [ ] **Step 2: Add health/readiness projection**

  Keep process liveness reachable but report readiness degradation when migrations or integrity fail. Return counts and reason codes, never raw exception text or candidate content.

- [ ] **Step 3: Run the full backend suite**

  Run: `./.venv/bin/python -m pytest -q`

  Expected: all tests pass.

- [ ] **Step 4: Verify the live database without mutation**

  Run: `./.venv/bin/python scripts/db_status.py`

  Run: `./.venv/bin/python scripts/db_backup.py`

  Run: `./.venv/bin/python scripts/db_repair.py --dry-run`

  Expected: a verified backup path, an integrity report, and repair counts without PII.

- [ ] **Step 5: Apply live migration/repair only from the verified backup**

  Run: `./.venv/bin/python scripts/db_repair.py --apply --backup <verified-backup-path>`

  Then run: `./.venv/bin/python scripts/db_status.py`

  Expected: `integrity_check=ok`, no foreign-key violations, and every known orphan accounted for in quarantine.

- [ ] **Step 6: Smoke the existing API surfaces**

  Start: `./.venv/bin/uvicorn app.main:app --port 8000`

  Exercise health, search, dashboard/applications, apply queue, automation, and recruiter-message endpoints against the migrated local database. Confirm the current frontend on port 52399 still loads these surfaces.

- [ ] **Step 7: Update documentation and commit**

  Document migration startup, backup/restore/repair commands, source-health semantics, and canonical states in `README.md`.

  `git add app/main.py README.md tests/test_trust_foundation_integration.py && git commit -m "Verify the CareerOS trust foundation end to end"`

## Self-review

- Spec coverage: all eight architecture/rollout areas and all ten acceptance criteria map to Tasks 1-9.
- Placeholder scan: no TBD/TODO/implement-later instructions remain.
- Type consistency: the plan consistently uses `ApplicationState`, `SourceResult`, `DiscoverySnapshot`, `LivenessEvidence`, and the shared database runtime signatures defined above.
- Scope boundary: Today cockpit, navigation redesign, authentication, generated TypeScript clients, router decomposition, and automation leases remain explicitly outside this tranche.
