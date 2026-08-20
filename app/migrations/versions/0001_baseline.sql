CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    source TEXT,
    status TEXT NOT NULL,
    raw_fit_score INTEGER,
    resume_score INTEGER,
    apply_url TEXT,
    next_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    first_response_at TEXT,
    outcome TEXT,
    outcome_at TEXT,
    timestamps_inferred INTEGER NOT NULL DEFAULT 0,
    outcome_reason TEXT,
    outcome_stage TEXT
);

CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    label TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    job_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE contacts (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    company TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    linkedin_url TEXT,
    confidence INTEGER DEFAULT 0,
    provider TEXT NOT NULL,
    why_selected TEXT,
    status TEXT DEFAULT 'not_started',
    created_at TEXT NOT NULL
);

CREATE TABLE imported_jobs (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE outreach (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    contact_id TEXT,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    email_subject TEXT,
    email_draft TEXT,
    linkedin_draft TEXT,
    sent_at TEXT,
    replied_at TEXT,
    followup_due_at TEXT,
    created_at TEXT NOT NULL,
    approval_status TEXT,
    approved_at TEXT,
    gmail_draft_id TEXT,
    gmail_sent_message_id TEXT,
    attachment_paths TEXT,
    last_error_message TEXT
);

CREATE TABLE saved_searches (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    filters TEXT NOT NULL,
    auto_rerun INTEGER DEFAULT 0,
    last_run_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    stats TEXT,
    nodes TEXT
);

CREATE TABLE automation_rules (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL
);

CREATE TABLE document_edits (
    job_id TEXT NOT NULL,
    field TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, field)
);

CREATE TABLE bullet_overrides (
    job_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    text TEXT NOT NULL,
    rationale TEXT,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'system',
    warnings TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    verdict TEXT,
    original_text TEXT,
    PRIMARY KEY (job_id, claim_id, author)
);

CREATE TABLE recruiter_messages (
    gmail_message_id TEXT PRIMARY KEY,
    application_id TEXT,
    sender_name TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    classification TEXT NOT NULL,
    synopsis TEXT NOT NULL,
    gmail_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE recruiter_reply_drafts (
    id TEXT PRIMARY KEY,
    gmail_message_id TEXT NOT NULL UNIQUE,
    to_addresses TEXT NOT NULL,
    cc_addresses TEXT NOT NULL,
    bcc_addresses TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'awaiting_approval', 'approved', 'creating', 'created', 'dismissed', 'failed'
    )),
    approved_at TEXT,
    gmail_draft_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content_fingerprint TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    sent_at TEXT,
    gmail_sent_message_id TEXT,
    attachment_paths TEXT
);

CREATE TABLE job_flags (
    job_id TEXT PRIMARY KEY,
    saved INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE interview_intel (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role_family TEXT NOT NULL,
    payload TEXT NOT NULL,
    sources TEXT NOT NULL,
    researched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    job_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    detail TEXT
);

CREATE INDEX idx_llm_usage_at ON llm_usage(at);

CREATE TABLE compose_openings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    opening TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE practice_attempts (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    kind TEXT NOT NULL,
    job_id TEXT,
    answer_text TEXT NOT NULL,
    spoken INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    findings TEXT NOT NULL,
    critique TEXT,
    scores TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_attempts_question ON practice_attempts(question_id);
CREATE INDEX ix_attempts_created ON practice_attempts(created_at);

CREATE TABLE question_research (
    question_id TEXT PRIMARY KEY,
    shape TEXT NOT NULL,
    sources TEXT NOT NULL,
    researched_at TEXT NOT NULL
);

CREATE TABLE technical_attempts (
    id TEXT PRIMARY KEY,
    curriculum_version TEXT NOT NULL,
    drill_id TEXT NOT NULL,
    dataset_version TEXT,
    answer_json TEXT NOT NULL,
    passed INTEGER NOT NULL,
    score REAL NOT NULL,
    summary TEXT NOT NULL,
    differences_json TEXT NOT NULL,
    hints_unlocked INTEGER NOT NULL DEFAULT 0,
    solution_revealed INTEGER NOT NULL DEFAULT 0,
    cleared INTEGER NOT NULL DEFAULT 0,
    runtime_ms INTEGER,
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_technical_attempts_drill
ON technical_attempts(drill_id, created_at);

CREATE TABLE technical_sessions (
    id TEXT PRIMARY KEY,
    curriculum_version TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    role TEXT,
    state TEXT NOT NULL,
    public_manifest_json TEXT NOT NULL,
    grading_manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    expires_at TEXT,
    completed_at TEXT,
    completion_reason TEXT,
    scorecard_json TEXT
);

CREATE TABLE technical_session_answers (
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    answer_json TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (session_id, question_id)
);
