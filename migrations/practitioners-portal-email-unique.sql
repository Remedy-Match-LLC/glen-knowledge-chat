-- One PORTAL account per email address.
-- Additive + idempotent. Apply: psql "$SUPABASE_DB_URL" < migrations/practitioners-portal-email-unique.sql
--
-- SCOPED ON PURPOSE. This is NOT a unique constraint on practitioners.email. The
-- same table holds the scraped practitioner directory, where several practitioners
-- legitimately share one clinic address; a blanket constraint would break that,
-- which would be worse than the bug it fixes. The index covers only rows with
-- portal_role set, so it says exactly one thing: at most one portal account per
-- email. Directory rows are untouched.
--
-- It is the backstop for a race between two concurrent registrations. The ordinary
-- case is already handled in the writers: dashboard.practitioner_portal resolves an
-- email through find_row_for_email (deterministic ORDER BY) and every INSERT carries
-- a NOT EXISTS guard.
--
-- Postgres CANNOT build a unique index while a violation exists, and a swallowed
-- failure would leave a deploy that looks enforced and is not. So this file refuses
-- loudly rather than half-applying. If it raises, list the offenders with
-- GET /api/console/practitioners/duplicates, retire the spare rows with the console
-- retire action, and run this again.
DO $$
DECLARE dup_emails int;
BEGIN
  SELECT COUNT(*) INTO dup_emails FROM (
    SELECT lower(email) FROM practitioners
     WHERE portal_role IS NOT NULL AND email IS NOT NULL
     GROUP BY lower(email) HAVING COUNT(*) > 1) d;
  IF dup_emails > 0 THEN
    RAISE EXCEPTION
      'ux_practitioners_portal_email not created: % email(s) still carry more than one portal practitioner row. Retire the spare rows first (console retire action), then re-run this migration.',
      dup_emails;
  END IF;
END$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_practitioners_portal_email
  ON practitioners (lower(email)) WHERE portal_role IS NOT NULL;

-- Verify after applying. Anything other than one row here means NOTHING is
-- enforcing one portal account per email:
--   SELECT indexname FROM pg_indexes WHERE indexname = 'ux_practitioners_portal_email';
