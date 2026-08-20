ALTER TABLE applications ADD COLUMN liveness_verdict TEXT;
ALTER TABLE applications ADD COLUMN liveness_checked_at TEXT;
ALTER TABLE applications ADD COLUMN liveness_reason_code TEXT;
ALTER TABLE liveness_event ADD COLUMN source_key TEXT;

CREATE INDEX idx_liveness_event_source_evidence
ON liveness_event(application_id, source_key, evidence_kind, id DESC);
