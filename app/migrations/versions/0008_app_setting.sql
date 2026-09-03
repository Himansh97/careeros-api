-- Settings that survive a restart, one row per key.
--
-- Added for the job-market region. It lived in a module-level list, so every
-- restart or --reload silently put discovery back on the United States while
-- the candidate was working through India postings. The argument against
-- storing it was that a stale row would silently serve the wrong market -- but
-- the market is displayed in the toggle on the page it filters, so a stored
-- value is the opposite of silent. The in-memory version was the silent one.
--
-- Deliberately a narrow key/value table rather than a settings object: each
-- setting is written and read independently, and a single JSON blob would make
-- two unrelated preferences contend on one row.

CREATE TABLE app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
