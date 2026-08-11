# Recruiter Message Draft Approvals — Design

## Goal

Show every confident recruiter or application email in CareerOS, prepare a
natural suggested response for each one, and let the candidate approve an
unsent Gmail draft from the CareerOS interface. Approval never sends email.

## Architecture

Use CareerOS as the review and approval system and the existing five-minute
Codex heartbeat as the Gmail bridge. The heartbeat already has authenticated
Gmail access; the CareerOS API does not gain Gmail credentials or OAuth token
storage.

The monitor records a minimal recruiter-message event and suggested reply in
`careeros.db`. The web application displays and edits the suggestion. Approval
sets a durable queue state. A later heartbeat creates an unsent Gmail draft and
records the returned Gmail draft ID. The candidate opens Gmail to perform the
final send.

This is an additive extension to the efficient-monitor design. Incremental
scanning, twelve-hour reconciliation, Gmail labeling, Codex notifications, and
exact-once notification state remain intact.

## Relevant Messages

Every confident message identified by the monitor receives a CareerOS event and
a suggested reply. This includes interviews, scheduling, assessments,
information requests, recruiter or hiring-manager outreach, decisions, offers,
rejections, status updates, and actionable company handoffs. An actionable
out-of-office handoff is included; a directionless out-of-office message is
not.

Excluded newsletters, marketing, receipts, recommendations, job alerts,
job-board digests, candidate-authored messages, and ambiguous mail create
neither events nor drafts.

For messages where a reply is usually optional, such as a rejection, CareerOS
still prepares a short courteous response because the candidate explicitly
selected draft coverage for every relevant email. The draft remains editable
and may be dismissed instead of approved.

## Data Model

Add a `recruiter_messages` table keyed by Gmail message ID:

- `gmail_message_id` — immutable unique key;
- `application_id` — matched CareerOS application, nullable only when no local
  application match is available;
- `sender_name` and `sender_email`;
- `subject` and `received_at`;
- `classification` and short non-sensitive `synopsis`;
- `gmail_url` for opening the original message;
- `created_at` and `updated_at`.

Add a `recruiter_reply_drafts` table with one active draft per recruiter
message:

- `id` and unique `gmail_message_id`;
- `to_addresses`, `cc_addresses`, and `bcc_addresses` as normalized JSON lists;
- `subject` and editable `body`;
- `status`: `awaiting_approval`, `approved`, `creating`, `created`, `dismissed`,
  or `failed`;
- `approved_at`, `gmail_draft_id`, `created_at`, and `updated_at`;
- `content_fingerprint`, computed from normalized recipients, subject, and body
  when approval occurs; and
- `last_error_code` and a short sanitized `last_error_message` for visible retry
  failures.

Do not store the incoming full body, raw headers, attachments, credentials, or
Gmail tokens. Suggested outgoing draft content may be stored because it must be
editable and reviewable. Existing monitor state continues to store only Gmail
IDs and operational stages.

## Draft Generation

Drafts must sound natural, concise, and specific to the message and application.
They may use the received message, matched company and role, candidate profile,
and explicit next steps while the heartbeat is processing the email. They must
not invent experience, availability, relationships, or recruiter details.

The generated reply includes:

- verified recipients from the incoming message or an explicit handoff;
- a thread-appropriate subject, usually preserving `Re:` context;
- a direct acknowledgement;
- the requested information or next action when known; and
- the candidate's normal signature.

When a message names a handoff, recipients follow the handoff rather than
blindly replying to an unavailable sender. For Matthew Macfarlane's GitLab
message, the suggested draft addresses Izzy Chu at `ichu@gitlab.com`, copies
Gabe Weaver at `gweaver@gitlab.com`, and references the Senior Revenue
Analytics Analyst application. Matthew is not included unless the candidate
adds him during review.

## API

Add endpoints under `/api/recruiter-messages`:

- `GET /api/recruiter-messages` — list newest first with application summary and
  draft status;
- `GET /api/recruiter-messages/{gmail_message_id}` — retrieve event and editable
  draft details;
- `PUT /api/recruiter-messages/{gmail_message_id}/draft` — update recipients,
  subject, or body only while status is `awaiting_approval` or `failed`;
- `POST /api/recruiter-messages/{gmail_message_id}/approve` — validate complete
  recipients/subject/body and atomically set `approved`;
- `POST /api/recruiter-messages/{gmail_message_id}/dismiss` — set `dismissed`
  without creating a Gmail draft;
- `POST /api/recruiter-messages/{gmail_message_id}/retry` — move `failed` back to
  `approved` after candidate review.

Approval is idempotent. It never invokes Gmail directly and never returns a
sent status. Editing a `created`, `creating`, or `dismissed` draft is rejected
unless a future explicit replacement workflow is designed.

The monitor uses narrow local store functions rather than an unauthenticated
HTTP callback: upsert a detected event/draft, atomically claim one `approved`
draft as `creating`, mark it `created` with its Gmail draft ID, or mark it
`failed` with sanitized error information.

## Heartbeat Queue Processing

After the Gmail scan and pending notification reconciliation, each heartbeat:

1. Upserts newly confident events and initial `awaiting_approval` suggestions.
2. Claims approved drafts atomically so overlapping runs cannot create the same
   Gmail draft twice.
3. Creates each Gmail draft with the Gmail connector. For ordinary replies it
   supplies the source message ID to preserve threading; for actionable
   handoffs to different recipients it creates a new message with the approved
   recipients and context.
4. Persists `created` only after Gmail returns a valid draft ID.
5. Persists `failed` and emits a visible Codex failure notification when Gmail
   draft creation fails.

A run that crashes after Gmail creates a draft but before CareerOS records the
draft ID has an unavoidable reconciliation risk. Before retrying a `creating`
row, the monitor searches Gmail drafts for the exact approved recipients,
subject, and body fingerprint. If exactly one matching draft exists, it records
that ID instead of creating another. If none or multiple match, it marks the
row failed for human review rather than creating blindly.

## CareerOS Interface

Add a recruiter-message inbox reachable from the main navigation and a
recruiter-message section on application detail pages.

Each inbox item shows company, role, sender, subject, received time,
classification, synopsis, and draft status. The detail view provides:

- **Open original in Gmail**;
- editable To, CC, subject, and body fields;
- **Approve Gmail draft**;
- **Dismiss**;
- **Retry** after a failure; and
- **Open Gmail draft** after creation.

The approval dialog repeats the exact recipients, subject, and complete
outgoing body and states: “This creates an unsent Gmail draft. It does not send
email.” The UI must not use “Send” for this action.

Application timelines receive events for reply detected, draft approved, Gmail
draft created, draft creation failed, and draft dismissed. Timeline wording
must distinguish a created draft from a sent email. CareerOS does not claim an
email was sent unless a later independent sent-message synchronization feature
is designed and verified.

## Safety and Privacy

- No automated sending under any condition.
- Approval creates an unsent draft only.
- The candidate can edit every field before approval.
- Recipient addresses must be syntactically valid and explicitly sourced from
  the email/handoff or candidate edits; no inferred corporate patterns.
- Full incoming bodies and attachments are not persisted in CareerOS.
- Gmail credentials remain in the connector and never enter either repository.
- Draft creation is idempotent and auditable by Gmail message ID and CareerOS
  draft ID.

## Failure Handling

- CareerOS database unavailable during email detection: retain the monitor's
  normal Gmail label/notification behavior, alert that the event could not be
  synchronized, and retry without changing its notification stage backward.
- Draft generation failure: create the event with `failed` draft status and a
  visible sanitized explanation; allow regeneration/retry.
- Approval validation failure: return field-specific errors and leave
  `awaiting_approval` unchanged.
- Gmail draft creation failure: set `failed`, retain the approved content, emit
  an app alert, and never mark it created.
- Duplicate Gmail event: update only mutable synopsis/application matching data;
  never replace candidate-edited or approved draft content.
- Unknown delivery/synchronization outcome: reconcile Gmail drafts before
  retrying, and surface uncertainty instead of creating blindly.

## Verification

1. Store and retrieve a recruiter event without persisting the incoming body.
2. Generate a suggested response for every supported relevant classification,
   including rejection and actionable handoff.
3. Verify excluded/ambiguous emails create no CareerOS rows.
4. Edit and approve a draft through the API; confirm no Gmail call occurs during
   approval.
5. Verify overlapping queue claims allow exactly one worker to create a draft.
6. Create one test Gmail draft and confirm recipients, subject, body, threading,
   and unsent state; delete no mail and send nothing.
7. Verify retry reconciliation finds an already-created draft instead of
   duplicating it.
8. Render and test the inbox, application detail section, edit form, explicit
   approval warning, dismissal, failure, retry, and Gmail links.
9. Backfill Matthew's GitLab handoff into CareerOS, generate the Izzy/Gabe
   response, and leave it awaiting approval until the candidate approves it in
   the app.

## Success Criteria

- Every confident relevant Gmail message appears once in CareerOS within the
  monitor's expected five-minute window.
- Every such message receives an editable suggested reply.
- CareerOS approval creates one unsent Gmail draft on a later heartbeat and
  never sends it.
- The UI and timeline always distinguish detected, approved, draft-created,
  dismissed, failed, and sent states.
- The GitLab handoff is visible with a natural Izzy/Gabe draft awaiting app
  approval.
