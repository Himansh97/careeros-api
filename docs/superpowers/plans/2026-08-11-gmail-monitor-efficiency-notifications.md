# Gmail Monitor Efficiency and Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the five-minute CareerOS Gmail monitor's routine token and Gmail-read cost while retaining exact-once state, twelve-hour reconciliation, and visible Codex app notifications.

**Architecture:** Keep one task-attached Codex heartbeat and its existing SQLite state. Every run uses a checkpointed ten-minute-overlap fast path; a durable audit timestamp enables full Gmail-label reconciliation only once per twelve hours. The implementation changes managed automation/state only and records operational evidence in repository documentation.

**Tech Stack:** Codex heartbeat automation, Gmail connector, SQLite, CareerOS SQLite application data, macOS notifications.

## Global Constraints

- Preserve the active five-minute recurrence and current task attachment.
- Preserve every existing checkpoint, message ID, stage, and stage timestamp.
- Store no sender, subject, synopsis, message body, attachment, or credential in durable state.
- The only Gmail mutation is adding `CareerOS/Recruiter Reply`.
- Never send, draft, reply, forward, archive, delete, mark read, star, change importance/category, open attachments, or alter CareerOS application status.
- Quiet successful runs emit no notification.
- Gmail search/read or state failures preserve the relevant previous checkpoint and alert visibly.
- Do not create a second automation, repository daemon, or Gmail credential store.

---

## File and State Structure

- Modify: managed automation `careeros-recruiter-reply-monitor` — compact execution runbook and unchanged five-minute schedule.
- Modify: `/Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite` — add singleton audit checkpoint without changing existing data.
- Read: `/Users/himanshusrivastava/careeros-api/careeros.db` — company/title/status matching source.
- Modify: `docs/superpowers/specs/2026-08-10-gmail-recruiter-reply-monitor-design.md` — link the efficiency amendment.
- Modify: `docs/superpowers/plans/2026-08-10-gmail-recruiter-reply-monitor.md` — record supersession of the verbose prompt.

### Task 1: Migrate and Verify Durable Audit State

**Files:**
- Modify: `/Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite`

**Interfaces:**
- Consumes: existing `checkpoint` and `messages` tables.
- Produces: `audit_checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), last_successful_audit_epoch INTEGER NOT NULL)`; an absent row means the next run must audit.

- [ ] **Step 1: Capture the pre-migration state read-only**

Run:

```bash
sqlite3 -readonly -header -column /Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite \
  "PRAGMA integrity_check; SELECT * FROM checkpoint; SELECT message_id,stage,classified_at,labeled_at,notified_at FROM messages ORDER BY message_id;"
```

Expected: `integrity_check` is `ok`; the existing scan checkpoint and notified GitLab message are present.

- [ ] **Step 2: Apply the additive migration transactionally**

Run:

```bash
sqlite3 /Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite \
  "BEGIN IMMEDIATE; CREATE TABLE IF NOT EXISTS audit_checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), last_successful_audit_epoch INTEGER NOT NULL); COMMIT;"
```

Do not insert an audit row. Its initial absence deliberately schedules the first audit.

- [ ] **Step 3: Verify migration preservation**

Run the Step 1 query again, followed by:

```bash
sqlite3 -readonly /Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite \
  "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_checkpoint'; SELECT count(*) FROM audit_checkpoint;"
```

Expected: integrity is `ok`, existing rows are identical, the new table has the exact schema above, and its count is `0`.

- [ ] **Step 4: Commit no repository files**

This task changes managed operational state only. Confirm `git status --short` in `careeros-api` is unchanged.

### Task 2: Replace the Verbose Heartbeat Prompt In Place

**Files:**
- Modify: managed automation `careeros-recruiter-reply-monitor`

**Interfaces:**
- Consumes: the automation ID, Gmail connector, `careeros.db`, `checkpoint`, `audit_checkpoint`, and `messages`.
- Produces: the same active task-attached heartbeat with the exact prompt below and `FREQ=MINUTELY;INTERVAL=5`.

- [ ] **Step 1: View and record the current automation fields**

Call the Codex automation manager in `view` mode for `careeros-recruiter-reply-monitor`. Record its name, status, kind, target task, schedule, and notification policy. Do not infer values from repository documentation.

- [ ] **Step 2: Update the existing automation with the complete compact prompt**

Use update mode with the existing ID and preserve all fields from Step 1. Set the prompt exactly to:

```text
Monitor Gmail for CareerOS recruiter/application replies. Run quietly unless notifying or reporting failure.

STATE=/Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite. It has checkpoint(id=1,last_successful_scan_epoch), audit_checkpoint(id=1,last_successful_audit_epoch), and messages(message_id PRIMARY KEY,stage classified|labeled|notified,classified_at,labeled_at,notified_at). Use transactions; stages only advance. Store no email content or credentials. If state is unreadable/unwritable, fail visibly; never reset or guess.

Capture upper=current UTC epoch. Read company,title,status for applied/submitted/ready from /Users/himanshusrivastava/careeros-api/careeros.db read-only when available.

FAST PATH: Load pending classified/labeled IDs. Set lower=(last successful scan minus 600), or upper-604800 if absent. Fully paginate Gmail IDs matching after:<lower> before:<upper> -in:sent -in:spam -in:trash -category:promotions -label:"CareerOS/Recruiter Reply". Deduplicate with pending IDs. Read only those exact IDs, no attachments.

AUDIT PATH: Run when audit timestamp is absent or upper-audit>=43200. Fully paginate label:"CareerOS/Recruiter Reply" -in:spam -in:trash. For labeled IDs absent from state, read exact IDs and reclassify; confident matches enter at labeled. On successful audit, transactionally set audit timestamp=upper. Audit failure alerts and preserves its timestamp; a separately successful fast path may still advance its scan checkpoint.

CONFIDENT = strong tracked company/role/thread/platform link AND recruiting intent (interview, scheduling, assessment, information request, recruiter outreach, decision, offer, rejection, explicit status) OR actionable company handoff naming a contact/address or explicit next step. Include actionable out-of-office handoffs. Exclude candidate-authored mail, newsletters, marketing, receipts, recommendations, alerts/digests, and ambiguity.

For each confident ID: persist classified; apply only Gmail label CareerOS/Recruiter Reply (create if missing), then persist labeled; emit one Codex notification with sender, subject, company, likely role, classification, received time, and brief non-sensitive synopsis, then persist notified only after notification success. Retry pending stages each run. A Gmail label never proves notification.

Advance scan checkpoint to upper only after every fast-path page/read and required insert succeeds. Gmail auth/search/read or state failure alerts and preserves scan checkpoint. Label/notification failure alerts and leaves its pending stage.

Never send/draft/reply/forward/archive/delete/mark read/star/change importance or category, alter CareerOS status, or open attachments. No matches or failures: DONT_NOTIFY.
```

- [ ] **Step 3: View the saved automation**

Call view mode again. Expected: same ID, active status, heartbeat kind, current task attachment, five-minute recurrence, preserved notification policy, and exact replacement prompt.

- [ ] **Step 4: Verify scheduler continuity**

Read the app's automation record read-only. Expected: `last_run_at` remains populated and `next_run_at` is in the future; there is still exactly one active automation with this ID.

### Task 3: Verify the Fast Path and Deduplication

**Files:**
- Read: managed state and CareerOS databases.

**Interfaces:**
- Consumes: updated heartbeat and existing notified message ID.
- Produces: observed quiet fast-path run, advanced scan checkpoint, and no duplicate notification.

- [ ] **Step 1: Capture the pre-run checkpoint and stages**

Run:

```bash
sqlite3 -readonly -header -column /Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite \
  "SELECT * FROM checkpoint; SELECT * FROM audit_checkpoint; SELECT message_id,stage FROM messages ORDER BY message_id;"
```

- [ ] **Step 2: Observe one scheduled run**

Wait for the existing heartbeat rather than creating a duplicate or changing its frequency. If no new confident email exists, expected result is `DONT_NOTIFY`.

- [ ] **Step 3: Verify fast-path evidence**

Repeat Step 1. Expected: `last_successful_scan_epoch` increased, all prior stages remained monotonic, and the existing GitLab message remained `notified` without another notification.

- [ ] **Step 4: Verify excluded-overlap behavior**

Inspect only IDs returned during the overlap scan. Confirm the known Parkland job digest and FundPulse marketing message, if returned again, remain absent from `messages` and do not carry `CareerOS/Recruiter Reply`.

### Task 4: Verify the Twelve-Hour Audit and App Notification Path

**Files:**
- Read/modify: managed state only through transactional test setup and restoration.

**Interfaces:**
- Consumes: empty/expired `audit_checkpoint`, labeled Gmail IDs, Codex app automation card, and macOS notification setting.
- Produces: audit evidence, restored production state, and verified app notification visibility.

- [ ] **Step 1: Confirm macOS notification permission manually**

Open `System Settings > Notifications > ChatGPT` (or `Codex` if that is the displayed app name). Enable Allow Notifications, Banners or Alerts, and Sounds. This is a user-visible setting; do not claim it from automation configuration alone.

- [ ] **Step 2: Observe the first audit**

Because Task 1 leaves `audit_checkpoint` empty, wait for the next scheduled heartbeat. Expected: it fully paginates the dedicated Gmail label, does not duplicate the already-notified GitLab alert, and inserts one audit timestamp after successful reconciliation.

- [ ] **Step 3: Verify audit throttling**

Observe the following heartbeat. Expected: it runs the fast path, leaves `last_successful_audit_epoch` unchanged, and does not perform another full label reconciliation before 43,200 seconds have elapsed.

- [ ] **Step 4: Verify visible notifications without sending email**

Temporarily replace only the automation prompt with the following one-run test:

```text
Notification delivery test only. Do not access or modify Gmail, CareerOS, or local state. Return NOTIFY with: "CareerOS monitor test: desktop notifications are working."
```

Wait for exactly one scheduled run, confirm the Codex app displays the test
notification, and immediately restore the exact Task 2 prompt. The test must
not read or write production state and must not be left active for a second run.

- [ ] **Step 5: Confirm restoration and health**

View the automation and observe the next run. Expected: exact Task 2 prompt restored, active five-minute schedule intact, successful scan checkpoint advancement resumed, and no duplicate message notifications.

### Task 5: Update Repository Handoff Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-gmail-recruiter-reply-monitor-design.md`
- Modify: `docs/superpowers/plans/2026-08-10-gmail-recruiter-reply-monitor.md`

**Interfaces:**
- Consumes: verified runtime evidence from Tasks 1–4.
- Produces: documentation pointing operators to the efficiency amendment and this implementation plan.

- [ ] **Step 1: Add an amendment link to the original design**

Add directly below its title:

```markdown
> Efficiency and app-notification behavior is amended by
> [Gmail Monitor Efficiency and Notifications](2026-08-11-gmail-monitor-efficiency-notifications-design.md).
```

- [ ] **Step 2: Mark the original verbose prompt as superseded**

Add directly below the old implementation-plan title:

```markdown
> The heartbeat prompt in Task 3 is superseded by
> [Gmail Monitor Efficiency and Notifications Implementation Plan](2026-08-11-gmail-monitor-efficiency-notifications.md).
```

- [ ] **Step 3: Validate documentation and repository scope**

Run:

```bash
rg -n "Efficiency and app-notification|superseded by" docs/superpowers
git diff --check
git status --short
```

Expected: only the two documentation files above are modified.

- [ ] **Step 4: Commit the handoff update**

Run:

```bash
git add docs/superpowers/specs/2026-08-10-gmail-recruiter-reply-monitor-design.md docs/superpowers/plans/2026-08-10-gmail-recruiter-reply-monitor.md
git commit -m "Document efficient Gmail monitor operations"
```

- [ ] **Step 5: Final verification**

View the automation, query both state checkpoints read-only, and run `git status --short`. Expected: active five-minute heartbeat, future `next_run_at`, populated scan/audit checkpoints, only monotonic message stages, and a clean worktree.
