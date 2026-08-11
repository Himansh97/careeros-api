# Gmail Monitor Efficiency and Notifications — Design

## Goal

Keep the CareerOS recruiter-reply monitor at a five-minute cadence while
reducing routine prompt, Gmail-read, and reasoning cost. Confident matches and
operational failures must still surface through the Codex app; quiet successful
runs produce no user-facing output.

## Current Baseline

The existing heartbeat is active and attached to the CareerOS task. Its first
observed run advanced durable state and notified one GitLab actionable handoff.
A later overlap scan completed without a duplicate notification. The existing
SQLite message stages and checkpoint remain authoritative and must be migrated
in place, never reset.

## Runtime Strategy

The heartbeat continues every five minutes but uses two paths:

1. **Fast path on every run.** Load the checkpoint and only durable rows at
   `classified` or `labeled`. Search Gmail from ten minutes before the last
   successful checkpoint through a run-start upper bound. Read only returned
   message IDs, deduplicate them, classify them, and advance the checkpoint
   after every required search page and read succeeds.
2. **Audit path every twelve hours.** In addition to the fast path, paginate all
   messages carrying `CareerOS/Recruiter Reply`. Reclassify labeled IDs absent
   from durable state and retry unfinished stages. Store a singleton
   `last_successful_audit_epoch` so ordinary runs can skip this full label scan.

The first run after migration performs an audit only when no successful audit
timestamp exists. The existing scan checkpoint continues unchanged.

## Token and Read Efficiency

- Replace the verbose automation prompt with a compact runbook that retains all
  safety, classification, retry, and checkpoint rules without explanatory
  repetition.
- Query Gmail for message IDs first and read bodies only for IDs in the current
  overlap window or unfinished durable stages.
- Do not reread already-notified messages during the fast path.
- Load only `company`, `title`, and `status` for active CareerOS applications.
- Keep summaries short and emit nothing on a successful no-match run.
- Preserve the heartbeat's existing model and reasoning settings; prompt and
  Gmail-read reduction provide the optimization. Do not introduce a repository
  daemon, Gmail credentials, or a second monitor.

## Classification and Actions

Classification behavior does not change. A confident match needs a strong
tracked-application link plus recruiting intent or an actionable company
handoff. Actionable out-of-office handoffs remain included. Marketing,
receipts, newsletters, generic job alerts, digests, candidate-authored mail,
and ambiguous messages remain excluded.

For each confident Gmail message ID, stages move monotonically from
`classified` to `labeled` to `notified`. The only Gmail mutation is adding
`CareerOS/Recruiter Reply`. No attachments are opened and no message is sent,
drafted, forwarded, archived, deleted, marked read, starred, or recategorized.
CareerOS application status is never changed by the monitor.

## App Notifications

The heartbeat remains attached to the current Codex task and uses the app's
normal notification policy, so a `NOTIFY` result becomes an app notification.
Each match notification contains sender, subject, matched company, likely role,
classification, received time, and a short non-sensitive synopsis. Failures in
authentication, Gmail reads, labeling, state updates, or notification delivery
also produce a visible alert.

Codex configuration cannot guarantee a macOS banner when operating-system
notifications are disabled. Verification therefore includes confirming that
notifications are allowed for the installed ChatGPT/Codex desktop application
in macOS System Settings. Gmail labeling remains a secondary visible indicator,
not proof that the app notification succeeded.

## Durable State Migration

Add audit state transactionally without changing or deleting existing rows:

- retain `checkpoint.last_successful_scan_epoch`;
- retain `messages` and all current stage timestamps; and
- add a singleton `last_successful_audit_epoch`, either as a new table or a
  backward-compatible column/table chosen during implementation.

If the existing database cannot be read, migrated, or updated, fail visibly.
Never recreate it or guess a checkpoint.

## Failure Handling

A Gmail search/read or state failure preserves the previous scan and audit
checkpoints. Label and notification failures leave their current durable stage
for the next fast-path retry. A failed twelve-hour audit does not block safe
incremental scanning, but it leaves the prior audit timestamp unchanged and
alerts the user.

## Verification

1. View the saved automation and confirm active status, five-minute recurrence,
   current-task attachment, compact prompt, and notification policy.
2. Run a fast path with no new matching mail; confirm zero notification, no
   reread of notified IDs, and a successfully advanced scan checkpoint.
3. Run an audit path; confirm full label pagination and an updated audit
   checkpoint without duplicate notification.
4. Confirm a known excluded job digest remains unlabeled and a known actionable
   handoff remains classified correctly.
5. Confirm a pending durable stage is retried without a full audit.
6. Confirm a simulated/read-only failure leaves the relevant checkpoint
   unchanged and produces a visible app alert.
7. Confirm the automation card is visible in the Codex app and perform one safe
   notification test after macOS notifications are enabled.

## Success Criteria

- Quiet five-minute runs inspect only the overlap window and pending stages.
- Full Gmail-label reconciliation occurs no more than once per twelve hours
  unless its previous attempt failed.
- Confident matches appear in the Codex app within approximately five minutes
  while the computer, app, scheduler, and Gmail connector are available.
- Each Gmail message produces at most one successful notification.
- Existing durable state and all Gmail/CareerOS safety boundaries are preserved.
