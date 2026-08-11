# Gmail Recruiter Reply Monitor — Design

## Goal

Continuously check the candidate's Gmail account for replies connected to
CareerOS applications. Confident matches receive a Gmail label and a Codex
notification. The monitor is read-mostly: it may add its own label, but it
never sends, drafts, forwards, archives, deletes, marks read, stars, or changes
an application's status.

## Runtime

Use an active Codex heartbeat attached to the current task and configured with
a five-minute recurrence. This describes saved scheduler configuration, not an
observed execution cadence. Gmail is accessed through the installed Gmail
connector; no OAuth token or Gmail password is stored in a CareerOS repository.

The first successful run scans the preceding seven days. Later runs search
from the last successful checkpoint with a ten-minute overlap so boundary-time
messages cannot be missed. The run captures its scan upper bound before it
starts Gmail reads and follows every Gmail search page token until exhausted.
Only after every search page and required message read succeeds may the
checkpoint advance to that captured upper bound.

Durable operational state lives outside the CareerOS repositories in
`/Users/himanshusrivastava/.codex/automations/careeros-recruiter-reply-monitor/state.sqlite`.
It stores only a successful scan checkpoint plus Gmail message IDs and their
notification stages; it never stores message bodies or credentials. A failed
Gmail read or failed state transaction does not advance the checkpoint.

## Inputs

The monitor reads:

- Gmail messages received since the search checkpoint, excluding Sent, Spam,
  Trash, and promotional mail;
- the applied and active application records in `careeros-api/careeros.db`,
  including company, role, source, and application time; and
- the durable scan checkpoint and per-message stages (`classified`, `labeled`,
  or `notified`) from the monitor state database; and
- all messages already carrying `CareerOS/Recruiter Reply`, used to reconcile
  labeled messages whose notification stage was never completed.

Reading application state is best effort. If the local database is unavailable,
the monitor may classify using recruiting language and known hiring-platform
signals, but the alert must state that no CareerOS application was matched.

## Classification

A message is a confident recruiter/application reply when it has at least one
strong application link and either a recruiting-intent signal or an actionable
handoff from a person at the tracked company.

Strong application links include:

- an exact or close company-name match;
- an exact or distinctive role-title match;
- an existing application-thread subject; or
- a recognized hiring platform message naming a tracked company or role.

Recruiting-intent signals include interview or scheduling requests,
assessments, requests for information, recruiter or hiring-manager outreach,
application decisions, offers, rejections, and explicit application-status
updates.

An actionable handoff names a specific person or address to contact, or gives
an explicit next step connected to the tracked company or application. This
includes an out-of-office response when it supplies named alternate contacts;
an out-of-office response with no actionable direction remains excluded.

Newsletters, job recommendations, marketing mail, receipts, generic job-board
digests, and candidate-authored messages are excluded. Ambiguous messages are
not labeled; they may be mentioned only in a low-confidence run summary when
human review would materially help tune the classifier.

## Actions

For each confident match, use the Gmail message ID as the durable key and move
it forward monotonically:

1. Persist `classified` before attempting a Gmail mutation.
2. Apply the Gmail label `CareerOS/Recruiter Reply`, creating it if necessary.
   After Gmail reports success, persist `labeled`.
3. Emit one Codex notification with sender, subject, matched company, likely
   role, message classification, received time, and a short non-sensitive
   synopsis. Only after notification success may the stage become `notified`.

Every run reconciles both unfinished durable stages and messages already
carrying the dedicated label before it scans for new mail. A labeled message
that is absent from durable state is read and reclassified; a confident match
is inserted at `labeled` and notified. Therefore the label is evidence of a
completed Gmail mutation, not evidence that notification already happened.
The candidate scan may exclude the dedicated label only because this separate
reconciliation pass includes it explicitly and paginates it fully.

Do not modify Gmail's read, archive, star, importance, or category state. Do
not change CareerOS application status automatically. Do not create or send a
reply.

## Failure Handling

- Gmail unavailable, authentication expired, an incomplete search page, or a
  failed message read: fail the run visibly, preserve the previous checkpoint,
  and retry on the next heartbeat.
- Durable-state initialization or transaction fails: fail visibly and do not
  scan from a guessed or reset checkpoint.
- Label application fails after classification: report the failure and leave
  the durable stage at `classified` so a later run retries it.
- Notification fails after labeling: report the failure and leave the durable
  stage at `labeled`; reconciliation retries notification even though the
  dedicated Gmail label is already present.
- CareerOS database unavailable: continue with email-only classification and
  identify the missing application match in the notification.
- Duplicate, paginated, or overlapping search results: deduplicate by Gmail
  message ID before reading, labeling, or notifying.

## Privacy and Safety

Notifications contain only the minimum useful synopsis and never reproduce a
full email body or attachment. Attachments are not opened during monitoring.
Gmail credentials and message contents are never written to git. The monitor's
only Gmail mutation is adding its dedicated label.

## Verification

Before enabling the heartbeat:

1. Confirm the Gmail connector can read the authenticated account.
2. Run a seven-day dry classification and review likely matches without
   applying labels.
3. Verify known recruiting replies are detected and newsletters or job alerts
   are excluded.
4. Enable labeling and confirm one approved real message receives the dedicated
   label without changes to read/archive state. Do not mutate unrelated mail to
   manufacture a test.
5. Observe a scheduler run and a second run, then confirm the first notification
   and second-run silence for the same Gmail message ID.
6. Observe or safely induce a Gmail-read failure without mutating mail and
   confirm the checkpoint does not advance.
7. Confirm a durable `labeled` message is reconciled to `notified`; configuration
   inspection alone is not proof of this runtime behavior.

Until the scheduler, notification, repeat-run, and failure evidence exists,
runtime verification remains open even when the saved automation is active.

## Success Criteria

- After runtime verification, a new confident recruiter or application reply
  is labeled and reported within approximately five minutes while the Codex
  scheduler is available.
- The same Gmail message generates at most one notification.
- No email is sent, drafted, archived, deleted, marked read, or otherwise
  changed beyond the dedicated label.
- Monitoring continues when the CareerOS API and web app are not running.
