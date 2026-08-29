-- The first teaching pass of a lesson, and how far through the track you are.
--
-- The pass is stored because it costs money to write and nothing to re-read.
-- Storing it also makes a lesson revisable: prose that rewords itself on every
-- visit is prose you cannot go back to and find the sentence that helped.

CREATE TABLE lesson_pass (
    lesson_id  TEXT NOT NULL,
    -- Only 'teach' is stored. The interruptions — simpler, deeper, example,
    -- stuck — are answers to a moment and are not worth freezing.
    mode       TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (lesson_id, mode)
);

CREATE TABLE lesson_progress (
    lesson_id    TEXT PRIMARY KEY,
    -- taught -> the pass was read. explained -> they said it back in their own
    -- words. Mastery additionally needs the linked drill cleared, which lives
    -- in technical_attempts and is not duplicated here.
    state        TEXT NOT NULL CHECK (state IN ('taught', 'explained')),
    explained_at TEXT,
    updated_at   TEXT NOT NULL
);
