# Gmail Recruiter Reply Monitor — Design

## Goal

Continuously check the candidate's Gmail account for replies connected to
CareerOS applications. Confident matches receive a Gmail label and a Codex
notification. The monitor is read-mostly: it may add its own label, but it
never sends, drafts, forwards, archives, deletes, marks read, stars, or changes
an application's status.

## Runtime

Use a Codex heartbeat attached to the current task. Run every five minutes so
monitoring continues while the CareerOS API and web app are closed. Gmail is
accessed through the installed Gmail connector; no OAuth token or Gmail
password is stored in a CareerOS repository.

The first successful run scans the preceding seven days. Later runs search
from the last successful checkpoint with a small overlap so boundary-time
messages cannot be missed. Gmail message IDs provide deduplication. A failed
Gmail read does not advance the checkpoint.

## Inputs

The monitor reads:

- Gmail messages received since the search checkpoint, excluding Sent, Spam,
  Trash, and promotional mail;
- the applied and active application records in `careeros-api/careeros.db`,
  including company, role, source, and application time; and
- previously processed Gmail message IDs stored in the automation's durable
  state or task history.

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

For each confident match:

1. Apply the Gmail label `CareerOS/Recruiter Reply`, creating it if necessary.
2. Emit one Codex notification with sender, subject, matched company, likely
   role, message classification, received time, and a short non-sensitive
   synopsis.
3. Record the Gmail message ID as processed so later runs do not alert again.

Do not modify Gmail's read, archive, star, importance, or category state. Do
not change CareerOS application status automatically. Do not create or send a
reply.

## Failure Handling

- Gmail unavailable or authentication expired: fail the run visibly, preserve
  the previous checkpoint, and retry on the next heartbeat.
- Label application fails after classification: notify that the reply was
  detected but labeling failed; do not mark the message processed until a
  later run applies the label successfully.
- CareerOS database unavailable: continue with email-only classification and
  identify the missing application match in the notification.
- Duplicate or overlapping search results: deduplicate by Gmail message ID
  before reading, labeling, or notifying.

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
4. Enable labeling and confirm one test message receives the dedicated label
   without changes to read/archive state.
5. Run the same window again and confirm no duplicate notification is emitted.
6. Simulate a Gmail-read failure and confirm the checkpoint does not advance.

## Success Criteria

- A new confident recruiter or application reply is labeled and reported
  within approximately five minutes.
- The same Gmail message generates at most one notification.
- No email is sent, drafted, archived, deleted, marked read, or otherwise
  changed beyond the dedicated label.
- Monitoring continues when the CareerOS API and web app are not running.
