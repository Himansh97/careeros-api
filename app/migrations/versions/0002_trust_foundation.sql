CREATE TABLE source_generation (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL CHECK (state IN ('healthy', 'degraded', 'unavailable')),
    job_count INTEGER NOT NULL DEFAULT 0 CHECK (job_count >= 0),
    error_code TEXT,
    error_summary TEXT
);

CREATE INDEX idx_source_generation_source_finished
ON source_generation(source_key, finished_at DESC);

CREATE TABLE job_observation (
    generation_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    job_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (generation_id, job_id),
    FOREIGN KEY (generation_id) REFERENCES source_generation(id) ON DELETE RESTRICT
);

CREATE INDEX idx_job_observation_job
ON job_observation(job_id, observed_at DESC);

CREATE TABLE liveness_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    prior_verdict TEXT,
    new_verdict TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    generation_id TEXT,
    reason_code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE RESTRICT,
    FOREIGN KEY (generation_id) REFERENCES source_generation(id) ON DELETE RESTRICT
);

CREATE INDEX idx_liveness_event_application
ON liveness_event(application_id, created_at DESC);

CREATE TABLE application_blocker (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('candidate', 'system', 'external')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'blocking')),
    state TEXT NOT NULL CHECK (state IN ('open', 'resolved')),
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    source TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE RESTRICT
);

CREATE INDEX idx_application_blocker_open
ON application_blocker(application_id, state, severity);

CREATE TABLE quarantined_orphan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);

CREATE TABLE repair_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
