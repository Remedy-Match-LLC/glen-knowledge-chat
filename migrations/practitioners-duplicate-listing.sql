-- Hide a duplicate listing from the public finder.
--
-- The scraped directory has been re-scraped several times, so the same person at
-- the same place can hold several `practitioners` rows. 953 rows share 398 email
-- addresses, and 386 practitioners are listed in the finder more than once. Of
-- those groups, 126 are unambiguously one person at one place; the rest are
-- either genuinely different clinicians on a shared clinic inbox (240 groups,
-- which must keep every listing) or need a human ruling (20 groups).
--
-- There was no way to hide a row. show_contact only decides whether email and
-- phone are exposed, not whether the row appears at all.
--
-- WHY NOT removal_requested: that flag is documented in
-- migrations/practitioners-farms.sql as the practitioner OPT-OUT ("Opt-out
-- reuses the existing practitioners.removal_requested flag"). Setting it for
-- 131 de-duplicated listings would mean that "who asked to be removed from the
-- directory?" answers with 131 people who never asked, on a consent-adjacent
-- field. So de-duplication gets its own column and the two facts stay separable
-- forever.
--
-- Apply by hand (psql) against remedy-match/prd. Not applied automatically.

-- 1) The column. NULL means "this row is not a duplicate of anything", which is
-- every existing row, so the ADD is a no-op for current data.
-- Self-referencing FK: the survivor must be a real practitioners row. ON DELETE
-- SET NULL rather than CASCADE, because deleting the survivor must never delete
-- the listing that was folded into it; that listing simply becomes visible again.
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS duplicate_of uuid;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'practitioners_duplicate_of_fkey') THEN
    ALTER TABLE practitioners ADD CONSTRAINT practitioners_duplicate_of_fkey
      FOREIGN KEY (duplicate_of) REFERENCES practitioners(id) ON DELETE SET NULL;
  END IF;
END$$;

-- A row can never be its own duplicate; that would hide it with no survivor.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'practitioners_duplicate_of_not_self') THEN
    ALTER TABLE practitioners ADD CONSTRAINT practitioners_duplicate_of_not_self
      CHECK (duplicate_of IS NULL OR duplicate_of <> id);
  END IF;
END$$;

-- Partial: only the marked rows are ever looked up by this, and they are a small
-- minority of the table.
CREATE INDEX IF NOT EXISTS practitioners_duplicate_of
  ON practitioners (duplicate_of) WHERE duplicate_of IS NOT NULL;

-- 2) The public view. A marked row must stop appearing in the finder.
--
-- CAREFUL EDIT, NOT A REWRITE. This column list is the live view's list verbatim
-- (migrations/practitioners-farms.sql, the most recent recreation) with NOTHING
-- added or removed; the only change is the extra AND in the WHERE. The view's
-- SELECT * was frozen at creation, so it does not auto-include later columns,
-- and a `SELECT *` refresh would newly expose sensitive columns added since
-- (wallet_balance_cents, license_number, portal_role, ...) through the PUBLIC
-- search API. duplicate_of is deliberately NOT selected: every row the view
-- returns has it NULL by construction, so exposing it would add a column that
-- carries no information to a public payload.
CREATE OR REPLACE VIEW v_practitioners_public
WITH (security_invoker = on) AS
SELECT id, tier, source_org, source_url, fellowship_level, specialties, name,
       practice_name, credentials, phone, email, website, address1, city, state,
       postal, country, lat, lng, geocode_quality, photo_url, bio,
       accepting_new_patients, telehealth, ghl_contact_id, removal_requested,
       last_scraped_at, created_at, updated_at, accepts_inquiries,
       claim_token_hash, claim_verified_at, modules_completed, show_contact,
       products, order_options
FROM practitioners
WHERE removal_requested = false AND lat IS NOT NULL AND duplicate_of IS NULL;
