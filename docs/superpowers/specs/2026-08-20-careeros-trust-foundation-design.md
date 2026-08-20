# CareerOS Trust Foundation Design

**Date:** 2026-08-20

**Status:** Approved direction; implementation specification for tranche one

## Purpose

CareerOS must never turn an infrastructure failure into a candidate decision.
The current discovery cache protects a long-running API process, but a scheduled
run starts cold. If sources fail during that cold run, their missing postings
can flow into liveness checks and be recorded as closed. Persistence is also
initialized independently by many modules, foreign keys are disabled, status
values are free text, and repair evidence is incomplete.

This tranche establishes the trustworthy system-of-record layer required by
every later improvement. It covers durable discovery observations, evidence-
backed liveness, numbered database migrations, canonical application states,
structured blockers, integrity repair, and backup/restore tooling.

It does not build the Today cockpit, change navigation, generate TypeScript
clients, add authentication, or split all routers. Those are later tranches and
must consume the interfaces defined here.

## Product invariants

1. Absence from a failed or degraded source is never evidence that a posting
   closed.
2. A posting closes only after two successful observations by its own source
   confirm absence, or a direct posting check returns explicit closure.
3. Every liveness transition records the observation that justified it.
4. Application state changes follow one canonical transition graph. Unknown
   state strings are rejected at the API boundary.
5. Legacy state remains readable during migration, but new writes use canonical
   values only.
6. A blocker is structured state, not a sentence hidden in `next_action`.
7. Database upgrades are numbered, transactional, idempotent, and backed up
   before any destructive repair.
8. Nothing auto-submits, fabricates candidate data, or suppresses discovery or
   tailoring because a queue is large.
9. Existing untracked files and private candidate data are never copied into
   git or test fixtures.

## Architecture

### 1. Database runtime

Add `app/db.py` as the only module responsible for opening the CareerOS SQLite
database. It exposes:

- `connect(*, read_only=False)`: a context manager that always closes the
  connection and configures row factories, busy timeouts, and foreign keys.
- `transaction(mode="DEFERRED")`: a context manager supporting `DEFERRED`,
  `IMMEDIATE`, or `EXCLUSIVE` transactions.
- `initialize()`: applies pending migrations exactly once per process startup.
- `integrity_report()`: returns machine-readable integrity, foreign-key, orphan,
  and migration status without mutating data.

Writable connections use WAL mode, `foreign_keys=ON`, and a 5,000 ms busy
timeout. Read-only connections use SQLite URI mode and never create a missing
database. Application modules may retain repository-specific query functions,
but they no longer execute schema DDL during ordinary reads or writes.

### 2. Numbered migrations

Add `app/migrations/` with an immutable migration registry. Migration `0001`
captures the schema currently assembled by module-level `CREATE TABLE`
statements. Later migrations add the trust tables and constraints. A
`schema_migrations(version, name, applied_at, checksum)` table records applied
files; startup refuses a checksum mismatch instead of silently accepting edited
history.

Migration execution follows this order:

1. Acquire an exclusive migration transaction.
2. Read the current migration ledger.
3. Verify checksums for already-applied migrations.
4. Apply each pending migration in version order.
5. Run `PRAGMA foreign_key_check` and `PRAGMA integrity_check`.
6. Commit only if every check passes.

Existing databases are baselined by inspecting their schema before inserting
the `0001` ledger row. A new database is created entirely through migrations.
No request handler performs `ALTER TABLE`.

### 3. Durable source generations

Each discovery refresh creates a `source_generation` record per configured
source:

- `id`
- `source_key`
- `started_at`
- `finished_at`
- `state`: `healthy`, `degraded`, or `unavailable`
- `job_count`
- `error_code`
- `error_summary`

Normalized job observations live in `job_observation`:

- `generation_id`
- `source_key`
- `job_id`
- `observed_at`
- `payload_json`
- `payload_hash`

The payload contains the normalized public posting already returned by source
adapters. It contains no candidate profile, resume, application answer, or
contact data. Repeated observations may share a payload hash, but every source
generation remains auditable.

A refresh publishes a new durable generation even when degraded. Consumers
request a `DiscoverySnapshot` containing jobs plus per-source health. For a
failed source, the current view carries forward its latest healthy jobs and
marks them stale; it never presents the failed source as an empty healthy set.

Retention keeps the latest 30 generations per source plus every generation
referenced by a liveness event. Cleanup is transactional and never removes
evidence needed to explain a state transition.

### 4. Evidence-backed liveness

Replace set-only liveness input with a source-aware structure:

```text
LivenessEvidence
  job_id
  source_key
  observation_kind: present | absent | direct_live | direct_closed | unknown
  generation_id
  source_state
  observed_at
  detail_code
```

Decision rules:

- `present` in any successful source generation means live.
- `direct_live` means live.
- `direct_closed` means closed immediately because it is explicit evidence.
- `absent` counts only when the job's own source generation is healthy.
- Two consecutive qualifying `absent` observations from separate generations
  mean closed.
- `degraded`, `unavailable`, rate-limited, timed-out, parsing-failed, and missing
  source ownership all mean unknown.
- Imported/manual postings require a direct check; pool absence is irrelevant.

`liveness_event` records every applied transition with application ID, prior
verdict, new verdict, evidence kind, generation ID, reason code, and timestamp.
The mutable application projection is updated in the same transaction.

### 5. Canonical application state machine

Create `app/application_states.py` with string enums and transition rules.
Canonical states are:

```text
discovered -> qualified -> tailoring -> draft -> ready -> submitted
submitted -> recruiter_contacted -> screening -> interview -> offer
submitted|recruiter_contacted|screening|interview -> rejected|withdrawn
```

Legacy `applied` and `interviewing` remain accepted only during migration and
read normalization:

- `applied` maps to `submitted`
- `interviewing` maps to `interview`

Transitions may be idempotent. Backward transitions require a dedicated repair
operation with an audit reason; the generic advance endpoint cannot perform
them. Terminal states cannot be left by normal workflow. Tailoring an already
submitted application may update its resume projection but cannot rewind its
application state.

All API request models use the enum. Analytics, automation, STATE generation,
and frontend contract generation consume the same canonical labels.

### 6. Structured blockers

Add `application_blocker` with:

- `id`
- `application_id`
- `kind`
- `owner`: `candidate`, `system`, or `external`
- `severity`: `info`, `warning`, or `blocking`
- `state`: `open` or `resolved`
- `detected_at`
- `resolved_at`
- `source`
- `evidence_json`
- `summary`

Machine decisions use `kind`, `severity`, and `state`; `summary` is display
copy only. Known legacy `next_action` phrases migrate to structured blockers.
Unrecognized text is preserved as an informational legacy note and never
silently interpreted as a blocker. `next_action` remains readable until the
frontend and STATE generator migrate, then becomes a compatibility projection.

### 7. Repair and operational tooling

Add explicit CLI commands:

- `scripts/db_status.py`: read-only integrity, migration, orphan, index, and
  foreign-key report.
- `scripts/db_backup.py`: SQLite online backup to a timestamped destination;
  refuses overwrite.
- `scripts/db_restore.py`: restores only after validating the backup and making
  a safety backup of the current database.
- `scripts/db_repair.py --dry-run`: reports legacy states, orphan rows, and
  outage-derived closure flags.
- `scripts/db_repair.py --apply`: requires an explicit backup path, repairs in
  one transaction, and writes repair events.

The repair migration handles the currently observed three orphan timeline rows
and any approval rows whose application is absent. It never invents a parent.
Orphans are copied to a quarantine table with their original values and a
reason before removal from active projections.

Outage repair clears a closure only when the posting is present in a later
healthy generation or a direct check proves it live. Ambiguous closures remain
flagged for human review.

## Data flow

```text
source adapter
  -> generation recorder
  -> normalized durable observations
  -> source-aware current snapshot
  -> discovery/search consumers
  -> liveness evidence builder
  -> transition policy
  -> application projection + liveness event (one transaction)
```

The daily script and API-triggered refresh call the same service. Neither passes
a raw set of currently fetched IDs into liveness. Scheduled processes therefore
receive the same durable fallback behavior as a warm API process.

## Error handling and visibility

- Source adapters emit stable error codes such as `timeout`, `rate_limited`,
  `auth`, `parse`, and `network`; raw exception strings are not persisted.
- A failed generation remains visible and cannot overwrite the last healthy
  snapshot for that source.
- Migration or integrity failure makes readiness fail while process liveness
  remains healthy.
- A liveness decision with insufficient evidence returns `unknown` and performs
  no mutation.
- Repair commands default to dry-run and print counts, never candidate content.
- Health output reports current migration version, integrity status, last
  healthy generation by source, and stale-source counts.

## Compatibility and rollout

1. Add the database runtime and migration tests while existing repositories
   still work.
2. Baseline the live schema and add trust tables.
3. Move discovery to durable generations behind the current discovery service
   interface.
4. Change liveness callers to evidence objects and run repair in dry-run mode.
5. Introduce canonical writes while preserving legacy reads.
6. Migrate blockers and update API projections.
7. Run a verified backup, integrity check, and repair against the live local
   database.
8. Remove request-time schema DDL only after every module uses `app/db.py`.

At every step, the current frontend remains usable. Existing endpoint paths do
not change in this tranche.

## Testing strategy

### Unit tests

- Migration order, checksum refusal, rollback, and idempotence.
- Connection closure, foreign keys, busy timeout, WAL, and read-only refusal.
- Source health classification and carry-forward semantics.
- Two-generation closure threshold and direct-check override.
- Canonical transition acceptance, rejection, idempotence, and terminal-state
  behavior.
- Blocker migration and legacy-note preservation.

### Integration tests

- A fresh scheduled process can recover the latest healthy source snapshot.
- A total source outage cannot close an application.
- One healthy absence cannot close an application; the second can.
- A later healthy presence retracts a previous closure with an event.
- Migration from a representative legacy database preserves applications,
  approvals, outreach, recruiter drafts, attempts, and timelines.
- Foreign-key violations fail without partial writes.
- Backup and restore reproduce the same integrity report.

### Live verification

- Run backup and dry-run repair against the local database.
- Confirm the report accounts for every orphan and legacy state without showing
  PII.
- Force a synthetic degraded generation and verify no live application changes.
- Run the full backend suite and the discovery/liveness verification script.
- Exercise search, dashboard, applications, apply queue, and automation pages
  against the migrated database.

## Acceptance criteria

This tranche is complete only when:

1. A cold scheduled run preserves jobs from failed sources without relying on
   process memory.
2. No application can close from absence in a degraded generation.
3. Every closure and retraction points to durable evidence.
4. All writes use canonical states and illegal transitions return a stable API
   error.
5. Foreign keys are enabled on every writable connection.
6. `PRAGMA integrity_check` and `foreign_key_check` pass after repair.
7. The three known orphan timeline rows are quarantined or attached only with
   real evidence; none are silently deleted.
8. Schema DDL no longer runs on normal repository connections.
9. Backup, restore, status, and repair commands are tested.
10. The full existing backend suite plus new migration/liveness tests pass.

## Later tranches

After this foundation lands, the program continues in this order:

1. Full CI, browser workflows, accessibility checks, and self-hosted fonts.
2. Today execution cockpit with no automatic queue cap.
3. Durable automation leases and operational health dashboard.
4. Domain router/service/repository decomposition.
5. OpenAPI-generated frontend contracts and local mutation security.
6. Today/Pipeline/Evidence navigation consolidation with optional companion
   mode and preserved deep links.

