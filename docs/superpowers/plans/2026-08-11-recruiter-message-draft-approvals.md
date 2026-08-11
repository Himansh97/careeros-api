# Recruiter Message Draft Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface every confident recruiter email and editable suggested response in CareerOS, then create one unsent Gmail draft after explicit in-app approval.

**Architecture:** Add recruiter-message and reply-draft persistence plus review endpoints to `careeros-api`, and add a recruiter inbox/detail workflow to `careeros-web`. Extend the existing Codex heartbeat through a narrow stdin/stdout queue CLI so the authenticated Gmail connector—not the web server—creates drafts. Approval only queues draft creation and never sends email.

**Tech Stack:** Python 3, FastAPI, Pydantic, SQLite, unittest, Next.js 16, React 19, TypeScript, TanStack Query, Gmail connector, Codex heartbeat.

## Global Constraints

- Every confident relevant email receives one CareerOS event and suggested reply.
- App approval creates an unsent Gmail draft on a later heartbeat; it never sends.
- The user can edit recipients, subject, and body before approval.
- Persist no incoming full body, raw headers, attachment content, Gmail credentials, or OAuth tokens.
- Never infer corporate email patterns or invent experience, availability, contacts, or application facts.
- Excluded and ambiguous email creates no event or draft.
- Gmail message ID and one active reply draft per message are unique.
- Queue claim and completion transitions are transactional and monotonic.
- The UI and timeline must never describe a Gmail draft as sent mail.
- Preserve the existing five-minute monitor, notification stages, checkpoint, and twelve-hour audit design.

---

## File Structure

### `careeros-api`

- Create: `app/recruiter_messages.py` — schema, serialization, CRUD, approval, queue claiming, completion, and failure transitions.
- Create: `scripts/recruiter_message_queue.py` — JSON-lines stdin/stdout bridge for heartbeat upsert/claim/complete/fail operations.
- Create: `tests/test_recruiter_messages.py` — isolated SQLite tests for persistence and state transitions.
- Create: `tests/test_recruiter_message_api.py` — FastAPI endpoint contract tests.
- Modify: `app/main.py` — recruiter-message request models and endpoints.

### `careeros-web`

- Create: `src/types/recruiter-message.ts` — event, draft, status, and mutation types.
- Create: `src/lib/api/recruiter-messages.ts` — live API functions.
- Create: `src/components/recruiter-messages/message-card.tsx` — compact inbox/application card.
- Create: `src/components/recruiter-messages/draft-review.tsx` — editable fields, confirmation, dismiss/retry, and Gmail links.
- Create: `src/app/(app)/recruiter-messages/page.tsx` — combined recruiter-message inbox.
- Create: `src/app/(app)/recruiter-messages/[messageId]/page.tsx` — message and draft review page.
- Modify: `src/app/(app)/applications/[applicationId]/page.tsx` — application-scoped recruiter-message section.
- Modify: `src/config/nav.ts` — recruiter inbox navigation entry.

### Managed runtime

- Modify: `careeros-recruiter-reply-monitor` — event upsert, suggested-reply generation, and approved-draft processing.
- Modify: `careeros.db` through schema initialization and the GitLab backfill; never modify monitor operational rows to fake success.

## Task 1: Recruiter Message Persistence and State Machine

**Files:**
- Create: `app/recruiter_messages.py`
- Create: `tests/test_recruiter_messages.py`

**Interfaces:**
- Produces `upsert_message(payload: dict) -> dict`, `list_messages(application_id: str | None = None) -> list[dict]`, `get_message(message_id: str) -> dict | None`, `update_draft(message_id: str, patch: dict) -> dict`, `approve_draft(message_id: str) -> dict`, `dismiss_draft(message_id: str) -> dict`, `retry_draft(message_id: str) -> dict`, `claim_approved_draft() -> dict | None`, `mark_draft_created(message_id: str, gmail_draft_id: str) -> dict`, and `mark_draft_failed(message_id: str, code: str, message: str) -> dict`.

- [ ] **Step 1: Write isolated schema and upsert tests**

Create `tests/test_recruiter_messages.py` using `unittest`, `tempfile.TemporaryDirectory`, and `unittest.mock.patch.object(app.store, "DB_PATH", temp_path / "careeros.db")`. Assert that `upsert_message()` creates both rows, `get_message()` returns camelCase fields, and this SQL finds no incoming-body column:

```python
columns = {r[1] for r in conn.execute("PRAGMA table_info(recruiter_messages)")}
self.assertFalse({"body", "raw_headers", "attachments"} & columns)
```

Use a fixture with Gmail ID `msg_gitlab_handoff`, application ID
`app_gh_gitlab_8616308002`, Izzy/Gabe recipients, and status
`awaiting_approval`.

- [ ] **Step 2: Run the new test and confirm failure**

Run: `python -m unittest tests.test_recruiter_messages -v`

Expected: import failure for `app.recruiter_messages`.

- [ ] **Step 3: Implement additive schema and serializers**

Define `RECRUITER_MESSAGE_SCHEMA` with the two tables and exact columns from the approved design. Store recipient lists with `json.dumps`; normalize with lowercase trimming and stable order. Compute `content_fingerprint` on approval as SHA-256 of canonical JSON containing `to`, `cc`, `bcc`, `subject`, and `body`.

`upsert_message()` must use `INSERT ... ON CONFLICT(gmail_message_id) DO UPDATE` for event metadata, then `INSERT OR IGNORE` for the draft. It must never overwrite an existing draft body or recipients.

- [ ] **Step 4: Write transition and concurrency tests**

Add tests asserting:

```python
self.assertEqual(approve_draft(mid)["draft"]["status"], "approved")
self.assertIsNotNone(approve_draft(mid)["draft"]["contentFingerprint"])
self.assertEqual(claim_approved_draft()["draft"]["status"], "creating")
self.assertIsNone(claim_approved_draft())
self.assertEqual(mark_draft_created(mid, "draft_123")["draft"]["status"], "created")
```

Also assert incomplete recipients/subject/body raise `ValueError`, edits are rejected for `approved`, `creating`, `created`, and `dismissed`, retry changes only `failed -> approved`, and duplicate upsert preserves candidate edits.

- [ ] **Step 5: Implement transactional state transitions**

Use `BEGIN IMMEDIATE` for claim, approval, completion, failure, dismissal, and retry. `claim_approved_draft()` selects the oldest approved row, updates it conditionally with `WHERE status='approved'`, appends `Draft creation started` to the application timeline, and returns the joined event/draft record.

- [ ] **Step 6: Run persistence tests**

Run: `python -m unittest tests.test_recruiter_messages -v`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/recruiter_messages.py tests/test_recruiter_messages.py
git commit -m "Add recruiter message draft state"
```

## Task 2: Review and Approval API

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_recruiter_message_api.py`

**Interfaces:**
- Consumes Task 1 store functions.
- Produces list/detail/update/approve/dismiss/retry endpoints under `/api/recruiter-messages`.

- [ ] **Step 1: Write endpoint contract tests**

Use `fastapi.testclient.TestClient`, patch `app.store.DB_PATH` to a temporary database, seed one message, and assert:

- `GET /api/recruiter-messages` returns `{ "messages": [...] }` newest first;
- `GET /api/recruiter-messages?applicationId=app_gh_gitlab_8616308002` filters correctly;
- detail returns 404 for unknown IDs;
- `PUT .../draft` edits only allowed fields;
- approve returns status `approved` and does not contain `sent`, `sentAt`, or `gmailMessageId` for outgoing mail;
- dismiss/retry enforce valid source states; and
- malformed recipient addresses return 422 without changing the row.

- [ ] **Step 2: Run API tests and confirm failure**

Run: `python -m unittest tests.test_recruiter_message_api -v`

Expected: endpoint 404 failures.

- [ ] **Step 3: Add request models and endpoints**

Add `RecruiterDraftUpdate` with recipient lists, non-empty subject, and non-empty
body. Validate addresses with a shared Pydantic `field_validator` using
`email.utils.parseaddr`, requiring one `@`, no whitespace, and an exact parsed
address match; do not add a new dependency. Map store `KeyError` to 404 and
`ValueError`/invalid transitions to 409; Pydantic field errors remain 422.
Approval performs no Gmail operation.

- [ ] **Step 4: Run API and regression tests**

Run:

```bash
python -m unittest tests.test_recruiter_message_api tests.test_recruiter_messages -v
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/main.py tests/test_recruiter_message_api.py
git commit -m "Add recruiter draft approval API"
```

## Task 3: Heartbeat Queue CLI

**Files:**
- Create: `scripts/recruiter_message_queue.py`
- Create: `tests/test_recruiter_message_queue.py`

**Interfaces:**
- Consumes one JSON object per stdin line with `action` equal to `upsert`, `claim`, `created`, `failed`, or `requeue_stale`.
- Produces one JSON object per stdout line with `ok`, `result`, and sanitized `error`; logs no request payload.

- [ ] **Step 1: Write CLI protocol tests**

Import `handle(command: dict) -> dict` directly. Assert `upsert` returns the stored message, two consecutive claims return a record then `None`, `created` requires a non-empty Gmail draft ID, and `failed` stores only code plus a message truncated to 300 characters.

- [ ] **Step 2: Run CLI tests and confirm failure**

Run: `python -m unittest tests.test_recruiter_message_queue -v`

Expected: import failure for `scripts.recruiter_message_queue`.

- [ ] **Step 3: Implement the JSON-lines bridge**

Read `sys.stdin` line-by-line, call `handle`, and write compact JSON with `flush=True`. Reject unknown actions. Never echo raw input or tracebacks. `requeue_stale` inspects `creating` rows older than ten minutes and returns them for Gmail-draft reconciliation without changing them to approved.

- [ ] **Step 4: Run CLI and regression tests**

Run:

```bash
python -m unittest tests.test_recruiter_message_queue -v
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/recruiter_message_queue.py tests/test_recruiter_message_queue.py
git commit -m "Add recruiter draft queue bridge"
```

## Task 4: Recruiter Message Frontend Data Layer

**Files:**
- Create: `src/types/recruiter-message.ts`
- Create: `src/lib/api/recruiter-messages.ts`

**Interfaces:**
- Produces `RecruiterMessage`, `RecruiterReplyDraft`, `RecruiterDraftStatus`, `listRecruiterMessages(applicationId?)`, `getRecruiterMessage(id)`, `updateRecruiterDraft(id, patch)`, `approveRecruiterDraft(id)`, `dismissRecruiterDraft(id)`, and `retryRecruiterDraft(id)`.

- [ ] **Step 1: Define exact TypeScript contracts**

Use the API camelCase fields from Task 1. `RecruiterDraftStatus` is the exact union `"awaiting_approval" | "approved" | "creating" | "created" | "dismissed" | "failed"`.

- [ ] **Step 2: Implement API functions**

Use `apiFetch`, `encodeURIComponent`, and JSON bodies. Preserve the existing `ApiResult<T>` pattern and return `not_connected` when live API configuration is absent; do not seed fabricated recruiter-message mock data.

- [ ] **Step 3: Typecheck and lint**

Run:

```bash
npx tsc --noEmit
npm run lint
```

Expected: both exit 0.

- [ ] **Step 4: Commit Task 4**

```bash
git add src/types/recruiter-message.ts src/lib/api/recruiter-messages.ts
git commit -m "Add recruiter message frontend API"
```

## Task 5: Recruiter Inbox and Draft Review UI

**Files:**
- Create: `src/components/recruiter-messages/message-card.tsx`
- Create: `src/components/recruiter-messages/draft-review.tsx`
- Create: `src/app/(app)/recruiter-messages/page.tsx`
- Create: `src/app/(app)/recruiter-messages/[messageId]/page.tsx`
- Modify: `src/config/nav.ts`

**Interfaces:**
- Consumes Task 4 API/types.
- Produces `/recruiter-messages` inbox and `/recruiter-messages/{gmailMessageId}` review route.

- [ ] **Step 1: Build the message card**

Show company, role, sender, subject, received time, classification, synopsis, and a status badge. Link the whole card to the encoded message detail route. Never render an incoming body field.

- [ ] **Step 2: Build the editable draft review**

Use controlled inputs for comma-separated To/CC/BCC, subject, and body. Save with `PUT`. Before approval, show a dialog containing the exact recipients, subject, and complete body plus this copy:

```text
This creates an unsent Gmail draft. It does not send email.
```

Buttons are `Approve Gmail draft`, `Dismiss`, `Retry`, `Open original in Gmail`, and, only for `created`, `Open Gmail draft`. Never label approval as Send.

- [ ] **Step 3: Build inbox and detail routes**

Use TanStack Query keys `['recruiter-messages', applicationId ?? 'all']` and `['recruiter-messages', 'detail', messageId]`. Show loading skeletons, not-connected state, empty state, mutation errors, and success toasts. Refetch detail after every mutation.

- [ ] **Step 4: Add navigation**

Add `{ title: "Recruiter Messages", href: "/recruiter-messages", icon: MailCheck }` after Applications in the Main section. Keep Approvals in the mobile primary nav; recruiter messages remains available under More.

- [ ] **Step 5: Validate frontend**

Run:

```bash
npx tsc --noEmit
npm run lint
npm run build
```

Expected: all exit 0 and both new routes appear in generated route output.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/components/recruiter-messages src/app/'(app)'/recruiter-messages src/config/nav.ts
git commit -m "Add recruiter draft approval inbox"
```

## Task 6: Application Detail Integration

**Files:**
- Modify: `src/app/(app)/applications/[applicationId]/page.tsx`

**Interfaces:**
- Consumes `listRecruiterMessages(applicationId)` and `MessageCard`.
- Produces an application-scoped `Recruiter messages` section.

- [ ] **Step 1: Query application messages independently**

Add a TanStack Query keyed by application ID. An email API error must not hide the application header or timeline.

- [ ] **Step 2: Render the section above Timeline**

Show up to the five newest cards and `View all recruiter messages`. When empty, show `No recruiter replies detected for this application.` without a fabricated count.

- [ ] **Step 3: Validate and commit**

Run `npx tsc --noEmit && npm run lint && npm run build`, then:

```bash
git add 'src/app/(app)/applications/[applicationId]/page.tsx'
git commit -m "Show recruiter replies on applications"
```

## Task 7: Extend the Live Heartbeat

**Files:**
- Modify: managed automation `careeros-recruiter-reply-monitor`

**Interfaces:**
- Consumes Gmail matches, queue CLI, Gmail draft create/list/read capabilities, and the existing operational state.
- Produces CareerOS event upserts and Gmail draft creation for approved rows.

- [ ] **Step 1: Preserve and extend the compact prompt**

Append rules requiring each confident message to generate a concise natural response and upsert via a long-running `python scripts/recruiter_message_queue.py` process using stdin JSON. The prompt must prohibit command-line or temporary-file email content, sending, and overwriting candidate edits.

- [ ] **Step 2: Add approved queue processing**

After scan/notification work, call `claim`. For a claimed ordinary reply, call Gmail draft creation with `reply_message_id`; for a handoff, create a new draft using approved To/CC/BCC. On connector success, call `created` with the returned draft ID. On failure, call `failed` and emit a visible Codex alert.

- [ ] **Step 3: Add stale-creating reconciliation**

Call `requeue_stale`; list Gmail drafts and compare normalized To/CC/BCC, subject, and SHA-256 body fingerprint. Exactly one match becomes `created`; zero or multiple matches becomes `failed` for review. Never create blindly from a stale `creating` row.

- [ ] **Step 4: View and verify the saved automation**

Confirm the same ID, active status, five-minute recurrence, target task, notification policy, compact scan/audit rules, event upsert, queue claim, reconciliation, and no-send boundary.

## Task 8: Backfill and Verify the GitLab Handoff

**Files:**
- Modify: `careeros.db` through the tested queue CLI.

**Interfaces:**
- Consumes Gmail message `19fed3719aa45574` and the GitLab application record.
- Produces one awaiting-approval CareerOS event and draft.

- [ ] **Step 1: Read the Gmail message by exact ID**

Read without opening attachments and verify Matthew's sender, subject, Izzy/Gabe
handoff, and GitLab role context. Match it to application
`app_gh_gitlab_8616308002`, not the separate GitLab backend-engineer application.

- [ ] **Step 2: Upsert this exact suggested draft**

Use To `ichu@gitlab.com`, CC `gweaver@gitlab.com`, no BCC, and subject `Senior Revenue Analytics Analyst — AI in the loop`. Use body:

```text
Hi Izzy,

Matthew Macfarlane directed me to you while he is on parental leave. I recently applied for GitLab's Senior Revenue Analytics Analyst — AI in the loop role and wanted to introduce myself and reiterate my interest.

The opportunity to support revenue analytics while helping teams use AI thoughtfully in their decision-making is especially compelling to me. If you are the right person to speak with, I would appreciate any guidance on next steps. If not, I would be grateful if you could point me in the right direction.

Best,
Himanshu Srivastava
```

Leave status `awaiting_approval`. Do not create a Gmail draft during backfill.

- [ ] **Step 3: Verify through API and frontend**

Confirm the message appears once in the combined inbox and GitLab application detail, the original Gmail link opens the source, the exact draft is editable, and approval warning says it does not send.

- [ ] **Step 4: User performs the approval test**

Have the candidate click `Approve Gmail draft`. Observe the next heartbeat and confirm exactly one unsent Gmail draft is created with Izzy/Gabe recipients and CareerOS status changes to `created`. Do not click Send.

## Task 9: Full Verification and Handoff

**Files:**
- Modify: `/Users/himanshusrivastava/careeros/docs/STATE.md`

- [ ] **Step 1: Run backend verification**

Run `python -m unittest discover -s tests -v` in `careeros-api`. Expected: all pass.

- [ ] **Step 2: Run frontend verification**

Run `npx tsc --noEmit`, `npm run lint`, and `npm run build` in `careeros-web`. Expected: all exit 0.

- [ ] **Step 3: Perform browser verification**

With API and frontend running, inspect desktop and mobile layouts for inbox, detail, approval dialog, application section, created state, and failures. Confirm no horizontal overflow and all buttons have visible focus and accessible names.

- [ ] **Step 4: Update operational state documentation**

Document the recruiter inbox, approval-to-unsent-draft semantics, five-minute processing expectation, Gmail connector dependency, and the fact that CareerOS does not track sent state yet. Regenerate the snapshot with `python3 scripts/snapshot_state.py` from the `careeros` repository.

- [ ] **Step 5: Commit documentation and verify clean worktrees**

Commit only `docs/STATE.md` in `careeros`, then run `git status --short` in `careeros-api`, `careeros-web`, and `careeros`. Expected: all clean.
