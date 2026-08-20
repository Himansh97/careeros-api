from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app import store as app_store


SCHEMA = """
CREATE TABLE IF NOT EXISTS technical_attempts (
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
CREATE INDEX IF NOT EXISTS idx_technical_attempts_drill
ON technical_attempts(drill_id, created_at);

CREATE TABLE IF NOT EXISTS technical_sessions (
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
CREATE TABLE IF NOT EXISTS technical_session_answers (
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    answer_json TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (session_id, question_id)
);
"""


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    app_store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(app_store.DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
