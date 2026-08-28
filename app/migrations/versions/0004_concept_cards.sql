-- Recall practice for the terms on the candidate's own resume.
--
-- The deck itself is derived from career_evidence.json at read time and is not
-- stored: a card list written down here would drift the moment a claim is added
-- or retired. What is stored is the two things the evidence file cannot know —
-- the sourced general definition of a term, and how well the candidate recalls
-- it.

CREATE TABLE concept_note (
    term         TEXT PRIMARY KEY,
    -- Two or three sentences of general meaning. Never a story, an employer or
    -- a figure — the candidate's own claims supply those, verbatim, from the
    -- evidence file. Same split as question_research.
    definition   TEXT NOT NULL,
    -- Enforced non-empty in code, as save_research does. An unsourced
    -- definition is a guess wearing a citation's clothes.
    sources      TEXT NOT NULL,
    researched_at TEXT NOT NULL
);

CREATE TABLE concept_review (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    term        TEXT NOT NULL,
    -- again | hard | good | easy
    rating      TEXT NOT NULL,
    -- Leitner box 1-5 AFTER this review, and when it comes back.
    box         INTEGER NOT NULL,
    due_at      TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

-- Every read wants the latest review per term, so order within a term matters
-- more than order across them.
CREATE INDEX idx_concept_review_term ON concept_review(term, id DESC);
CREATE INDEX idx_concept_review_due ON concept_review(due_at);
