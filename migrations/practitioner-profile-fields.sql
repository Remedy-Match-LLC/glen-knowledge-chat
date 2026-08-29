-- migrations/practitioner-profile-fields.sql
-- Section 2b: two self-authored profile fields.
-- Spec: docs/superpowers/specs/2026-08-27-practitioner-website-design.md
-- Additive + idempotent. Apply: psql "$SUPABASE_DB_URL" < migrations/practitioner-profile-fields.sql
--
-- Both are published ONLY by dashboard.practitioner_profile._write_live_profile,
-- and only from a draft Glen has approved.
--
-- DELIBERATELY does NOT re-create v_practitioners_public. That view is a stored
-- SELECT of a frozen column list feeding the PUBLIC practitioner finder, and it
-- already exposes bio, photo_url, credentials, city and state directly, with no
-- PRACTITIONER_PUBLIC_FIELDS whitelist in front of it. Adding these columns to it
-- would publish practitioner prose on a surface the storefront whitelist does not
-- guard. Same reasoning, and the same deliberate omission, as
-- migrations/practitioners-storefront.sql.
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS tagline text;
ALTER TABLE practitioners ADD COLUMN IF NOT EXISTS how_i_work text;
