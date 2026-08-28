-- A concept explained in layers, and a library of concepts to choose from.
--
-- The card used to hold one sourced definition. That is the right thing to
-- recite and the wrong thing to learn from: a term you have never met is not
-- made familiar by a precise sentence, it is made familiar by a plain one, an
-- example, and a picture.
--
-- The layers are derived from the sourced definition rather than researched
-- separately, and are marked as derived. The definition remains the only thing
-- asserting facts about the world; a plain-English restatement and a Hindi
-- translation of it introduce no new claims.

ALTER TABLE concept_note ADD COLUMN simple TEXT NOT NULL DEFAULT '';
ALTER TABLE concept_note ADD COLUMN hindi TEXT NOT NULL DEFAULT '';
ALTER TABLE concept_note ADD COLUMN application TEXT NOT NULL DEFAULT '';
-- A small structured diagram: {"kind": flow|layers|compare|cycle, "nodes": [...]}.
-- Structured rather than an image so it renders in the app's own type and
-- colours, and so a wrong diagram can be corrected as data.
ALTER TABLE concept_note ADD COLUMN visual TEXT NOT NULL DEFAULT '';
-- Which layers came from a model rather than from the sourced text, so the UI
-- can say so instead of implying every line is cited.
ALTER TABLE concept_note ADD COLUMN derived TEXT NOT NULL DEFAULT '[]';

-- Concepts worth knowing that are not on the resume. The resume deck answers
-- "can you defend what you wrote"; this answers "do you know the field you say
-- you work in". Topics are curated, not generated, for the same reason the
-- curriculum is.
CREATE TABLE concept_topic (
    slug        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    blurb       TEXT NOT NULL,
    terms       TEXT NOT NULL,   -- JSON array, in teaching order
    sort_order  INTEGER NOT NULL DEFAULT 0
);
