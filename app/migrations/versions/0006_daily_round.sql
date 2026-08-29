-- One round a day, and whether it was finished.
--
-- The three items are not stored. They are re-derived from the date and the
-- current pipeline, so the round follows what is actually staged rather than a
-- snapshot taken when the row was written — and a job submitted this morning
-- stops being studied for this afternoon.
--
-- What is worth keeping is the outcome: that a day was completed, and how it
-- went. The streak reads back from here.

CREATE TABLE daily_round (
    day          TEXT PRIMARY KEY,    -- YYYY-MM-DD, UTC
    -- The terms as actually served, so a completed day can be shown back
    -- faithfully even after the pipeline moves underneath it.
    items        TEXT NOT NULL,
    scored       INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT
);

CREATE INDEX idx_daily_round_completed ON daily_round(completed_at);
