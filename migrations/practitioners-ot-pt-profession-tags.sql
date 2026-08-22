-- Explicit profession facets for already-qualified practitioner rows.
--
-- This migration does NOT import general OT/PT populations. It tags only rows
-- already admitted through curated organization/advanced-credential adapters.
-- Future adapter upserts apply the same classification in db.py.

UPDATE practitioners
SET specialties = array_append(COALESCE(specialties, ARRAY[]::text[]),
                               'occupational_therapy'),
    updated_at = now()
WHERE NOT (COALESCE(specialties, ARRAY[]::text[]) @> ARRAY['occupational_therapy'])
  AND (
    lower(COALESCE(credentials, '')) LIKE '%occupational therapist%'
    OR COALESCE(credentials, '') ~* '(^|[^A-Za-z])(OTR/L|OTR|OTD|MOT|MSOT|OT)([^A-Za-z]|$)'
  );

UPDATE practitioners
SET specialties = array_append(COALESCE(specialties, ARRAY[]::text[]),
                               'physical_therapy'),
    updated_at = now()
WHERE NOT (COALESCE(specialties, ARRAY[]::text[]) @> ARRAY['physical_therapy'])
  AND (
    lower(COALESCE(credentials, '')) LIKE '%physical therapist%'
    OR lower(COALESCE(credentials, '')) LIKE '%physiotherapist%'
    OR COALESCE(credentials, '') ~* '(^|[^A-Za-z])(DPT|MPT|MSPT|PT)([^A-Za-z]|$)'
  );
