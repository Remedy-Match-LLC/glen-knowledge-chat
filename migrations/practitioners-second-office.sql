-- practitioners.second_office — "looked at, both listings stay"
--
-- The duplicate audit had no way to record a decision, so it re-asked the same
-- question every run. On 2026-09-11 all seven groups it flagged were pairs Glen
-- had already decided to keep: the six second offices restored on 2026-09-10
-- (Boca Raton, Montebello, Katy, North Naples, Edinburgh, North Providence) and
-- Lisa Anne Arnold's two Cape Cod offices. The actionable count was zero and the
-- headline said seven.
--
-- DELIBERATELY NOT LIKE duplicate_of. That column hides a row from the finder.
-- This one hides nothing: both offices stay public, because both are real and a
-- patient needs to find the nearer one. It changes only what the audit COUNTS.
-- v_practitioners_public is therefore untouched by this migration, and must stay
-- untouched: adding second_office to its WHERE would silently delist every
-- second office in the directory.
--
-- NOT NULL DEFAULT false, so every existing row reads false and the audit behaves
-- exactly as it did before this lands. Idempotent: safe to run twice.
ALTER TABLE practitioners
  ADD COLUMN IF NOT EXISTS second_office boolean NOT NULL DEFAULT false;

-- Partial: only the reviewed rows are ever looked up by this, and they are a
-- small minority of the table.
CREATE INDEX IF NOT EXISTS practitioners_second_office
  ON practitioners (second_office) WHERE second_office;
