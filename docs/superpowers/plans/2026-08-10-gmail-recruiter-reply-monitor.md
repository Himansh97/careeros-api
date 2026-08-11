# Gmail Recruiter Reply Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a five-minute Codex heartbeat that detects Gmail replies tied to CareerOS applications, labels confident matches, and notifies the candidate without otherwise changing mail or application state.

**Architecture:** A chat-attached Codex heartbeat uses the Gmail connector for search, message reads, and one allowed mutation: applying `CareerOS/Recruiter Reply`. Each run reads the local CareerOS SQLite application list when available and falls back to recruiting-language classification when it is not. The dedicated label and the heartbeat's retained task context prevent duplicate notifications; no repository daemon or Gmail credential storage is introduced.

**Tech Stack:** Codex heartbeat automation, Gmail connector, Gmail search syntax, local SQLite (`careeros.db`), existing CareerOS application records.

## Global Constraints

- Run every five minutes.
- The first scan covers the preceding seven days.
- Never send, draft, reply, forward, archive, delete, mark read, star, or change Gmail importance/category state.
- Never open attachments.
- Never change CareerOS application status automatically.
- Notifications include only sender, subject, matched company, likely role, classification, received time, and a short non-sensitive synopsis.
- A failed Gmail read or label operation must not be treated as a successful processed message.
- Local CareerOS matching requires the computer on, the desktop app running, and `/Users/himanshusrivastava/careeros-api` available; Gmail-only classification is the fallback.

---

## File Structure

- No production source files are created or modified.
- Managed automation: `CareerOS Recruiter Reply Monitor` — owns scheduling, classification instructions, deduplication, labeling, and notification behavior.
- Existing local data: `/Users/himanshusrivastava/careeros-api/careeros.db` — read-only source of company, role, and application status.
- Existing design: `docs/superpowers/specs/2026-08-10-gmail-recruiter-reply-monitor-design.md` — behavioral source of truth.

### Task 1: Verify Gmail Access and Establish a Dry Baseline

**Files:**
- Read: `/Users/himanshusrivastava/careeros-api/careeros.db`
- Read: `docs/superpowers/specs/2026-08-10-gmail-recruiter-reply-monitor-design.md`

**Interfaces:**
- Consumes: Gmail connector profile/search/read operations and SQLite application rows.
- Produces: a reviewed set of likely recruiter/application replies from the last seven days; makes no Gmail changes.

- [ ] **Step 1: Confirm the authenticated Gmail account**

Call `gmail_get_profile` and verify it returns the candidate's expected account. Do not print the full profile into a repository file.

- [ ] **Step 2: Read tracked applications without modifying them**

Run:

```bash
sqlite3 -json careeros.db "SELECT job_id, company, title, status, updated_at FROM applications WHERE status IN ('applied','submitted','ready') ORDER BY updated_at DESC;"
```

Expected: valid JSON containing the current CareerOS companies and roles.

- [ ] **Step 3: Search the seven-day candidate window**

Use `gmail_search_email_ids` with:

```text
newer_than:7d -in:sent -in:spam -in:trash -category:promotions -label:"CareerOS/Recruiter Reply"
```

Read candidate messages in batches with `gmail_batch_read_email`. Do not open attachments.

- [ ] **Step 4: Classify without labeling**

Require at least one strong application link (company, distinctive role, application-thread subject, or hiring platform naming a tracked application) and either (a) a recruiting-intent signal (interview, scheduling, assessment, information request, recruiter outreach, decision, offer, rejection, or explicit status update), or (b) an actionable company handoff that names a specific person/address to contact or gives an explicit next step. Include out-of-office replies only when they contain such an actionable handoff.

Exclude newsletters, job recommendations, job-board digests, marketing, receipts, and messages authored by the candidate. Record only a transient review summary; do not write email content to disk.

- [ ] **Step 5: Review the baseline result**

Expected: likely replies are listed with sender, subject, company/role match, and classification; obvious digests and promotions are absent. If uncertain messages exist, leave them unlabeled.

### Task 2: Create and Verify the Dedicated Gmail Label

**Files:**
- None.

**Interfaces:**
- Consumes: one message ID confirmed as a recruiter/application reply in Task 1, when available.
- Produces: Gmail label `CareerOS/Recruiter Reply`; verifies label-only mutation behavior.

- [ ] **Step 1: Create the label idempotently**

Call `gmail_create_label` with:

```json
{
  "name": "CareerOS/Recruiter Reply",
  "label_list_visibility": "labelShowIfUnread",
  "message_list_visibility": "show"
}
```

Expected: the label is returned whether newly created or already present.

- [ ] **Step 2: Apply the label to one confirmed message when the baseline contains one**

Call `gmail_apply_labels_to_emails` with `message_ids` set to the exact
confirmed Gmail message ID returned in Task 1, `add_label_names` set to
`["CareerOS/Recruiter Reply"]`, and `create_missing_labels` set to `false`.

If Task 1 contains no confirmed reply, skip mutation and verify the label exists with `gmail_list_labels`; do not label an unrelated message for testing.

- [ ] **Step 3: Verify no unrelated Gmail state changed**

Read the confirmed message again. Expected: `CareerOS/Recruiter Reply` is present and its prior UNREAD/INBOX/STARRED/IMPORTANT state is unchanged.

### Task 3: Create the Five-Minute Heartbeat

**Files:**
- Managed automation: `CareerOS Recruiter Reply Monitor`

**Interfaces:**
- Consumes: Gmail connector, `careeros.db` when locally available, and the dedicated Gmail label.
- Produces: a chat-attached heartbeat scheduled with `FREQ=MINUTELY;INTERVAL=5` and notifications on confident matches or failed runs.

- [ ] **Step 1: Create the heartbeat automation**

Use the Codex automation manager in heartbeat mode, attached to this task, active, with notification policy `all`, and schedule:

```text
FREQ=MINUTELY;INTERVAL=5
```

Use this exact automation prompt:

```text
Monitor Gmail for recruiter or application replies connected to CareerOS.

Search messages received in the last seven days using:
newer_than:7d -in:sent -in:spam -in:trash -category:promotions -label:"CareerOS/Recruiter Reply"

When local files are available, read /Users/himanshusrivastava/careeros-api/careeros.db read-only and load company/title/status from applications where status is applied, submitted, or ready. Never modify the database. If it is unavailable, continue with email-only recruiting classification and say that no CareerOS record was matched.

Read candidate Gmail messages without opening attachments. A confident match requires (1) a strong application link: company, distinctive role title, application-thread subject, or hiring platform naming a tracked company/role; and either (2a) recruiting intent: interview, scheduling, assessment, information request, recruiter/hiring-manager outreach, decision, offer, rejection, or explicit application-status update; or (2b) an actionable company handoff that names a specific person/address to contact or gives an explicit next step. Include an out-of-office reply when it contains such an actionable handoff; exclude out-of-office replies with no actionable direction.

Exclude newsletters, marketing, receipts, generic job recommendations, job-board digests, and messages authored by the candidate. Do not label ambiguous messages.

For every confident unprocessed match, apply only the Gmail label CareerOS/Recruiter Reply, creating it if missing. Do not send, draft, reply, forward, archive, delete, mark read, star, change importance/category, or alter CareerOS status. Never open attachments.

Notify once per newly labeled message with sender, subject, matched company, likely role, classification, received time, and a short non-sensitive synopsis. Never reproduce the full body. Treat the Gmail message ID as the deduplication key and retain processed IDs in task context; the label is the durable second guard. If labeling fails, report failure and do not record the message as processed. If Gmail read/authentication fails, fail the run visibly and retry next cycle.

When there are no new confident matches, complete silently.
```

- [ ] **Step 2: View the saved automation**

Use the automation manager's view mode. Expected: name `CareerOS Recruiter Reply Monitor`, heartbeat kind, active status, current-task target, Gmail-capable prompt, and five-minute recurrence.

### Task 4: Verify Deduplication and Failure Boundaries

**Files:**
- None.

**Interfaces:**
- Consumes: the active heartbeat and any confirmed baseline message.
- Produces: evidence that a match alerts once and the monitor preserves unrelated state.

- [ ] **Step 1: Trigger or wait for the first heartbeat run**

Expected: any new confident match is labeled and summarized once; a no-match run is silent.

- [ ] **Step 2: Inspect the automation run result**

Verify that the run searched Gmail, did not open attachments, and either matched a CareerOS row or explicitly used the email-only fallback.

- [ ] **Step 3: Verify deduplication on the next run**

Allow the same search window to run again. Expected: previously labeled message IDs produce no second notification.

- [ ] **Step 4: Verify preserved Gmail state**

Read one labeled message. Expected: only the dedicated label differs from its pre-monitor state.

- [ ] **Step 5: Record operational limits in the handoff**

Update the manual section of `/Users/himanshusrivastava/careeros/docs/STATE.md` to state that the monitor is active, runs every five minutes, modifies only its dedicated label, and requires the desktop app/computer for local CareerOS matching. Regenerate the snapshot with:

```bash
cd /Users/himanshusrivastava/careeros
python3 scripts/snapshot_state.py
```

- [ ] **Step 6: Commit the handoff update**

```bash
cd /Users/himanshusrivastava/careeros
git add docs/STATE.md
git commit -m "Document recruiter reply monitor"
```
