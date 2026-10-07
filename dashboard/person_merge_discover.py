"""Which tables and columns a person merge moves, on SQLite or Postgres.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Discovery runs at apply
time over every schema (production keeps each old SQLite file as its own Postgres schema), so a
table added later is still found. A text column whose name contains "email" (not a timestamp or
hash such as emailed_at or email_hash), or a column named person_id, people_id or *_person_id.

Every table in the repo with such a column is named below, in MOVE_TABLES or HISTORY_TABLES;
tests/test_person_merge_discover.py fails when a new one is in neither."""
import re
from collections import namedtuple

from dashboard import db

Target = namedtuple("Target", "schema table column kind")

# Never rewritten: they record what happened, or belong to someone who is not a client.
HISTORY_TABLES = frozenset({
    "ghl_write_queue", "portal_auth_events", "email_click_tokens", "cadence_clicks",
    "weekly_live_invitation_recipients", "portal_welcome_sent", "portal_token_welcome_sent",
    "ebook_library_welcome_sent", "email_suppression", "pending_merges", "fullscript_clicks",
    "pb_events", "e4l_account_notification_messages", "inbox_hidden_senders",
    "analysis_autoconfirm_log", "kloud_instruction_emails", "payer_link_emails",
    "remedy_match_email_queue", "review_invites", "client_erasures",
    "scan_reassignments", "referral_events", "stripe_failures", "users", "suppliers",
    "supplier_quotes", "journey_events",
    "sms_consent_events",             # a text consent log keyed by phone; never rewritten
    # Found in production on 2026-09-27 (prod-queries/merge-unclassified-tables.py):
    "fmp_clients",                    # a FileMaker mirror; the next FileMaker sync rewrites it
    "escalation_queue", "identity_consolidation_audit", "membership_reconcile_alerts",
    "query_log", "remedy_match_email_sent", "review_link_tokens",
    "email_identity_aliases",         # an older alias table (people pillar); left as it is
    "inquiry_practitioners", "practitioner_inquiry_opt_outs", "practitioner_suggestions",
})
MERGE_OWN_TABLES = frozenset({
    "person_merges", "email_aliases", "portal_token_aliases", "person_merge_changes",
})
# Reviewed 2026-09-27: records that belong to the person and follow them to the survivor.
MOVE_TABLES = frozenset({
    "affiliate_conversions", "affiliate_earnings", "affiliate_signups", "affiliate_social_links",
    "analysis_quota", "analysis_requests", "appointment_proposals", "ash_ally_memory",
    "auth_tokens", "biofield_auth_tests", "biofield_corrections", "biofield_free_unlocks",
    "biofield_readiness", "biofield_reveals", "biofield_trial_grants", "body_map_photos", "carts",
    "cert_bonus_grants", "cert_commitments", "cert_submissions", "client_conditions",
    "client_document_extractions", "client_documents", "client_facts", "client_identity_photos",
    "client_photos", "client_portals", "client_prefs", "client_prices", "client_scans",
    "client_species", "species_answers", "coach_requests", "coach_subscriptions", "coach_threads", "coach_volunteers",
    "coaching_windows", "cohort_members", "community_embeddings", "community_reactions",
    "condition_triage", "consult_eligibility", "coupons", "course_entitlements",
    "course_lesson_watched", "course_module_homework", "course_module_unlocks", "course_tokens",
    "course_unlock_pref", "dispensary_orders", "e4l_accounts", "ebook_grants", "evox_bookings",
    "evox_readiness", "evox_session_credits", "eye_vision_review_requests",
    "eye_vision_suggestion_reviews", "family_subscriptions", "ff_match_drafts", "fireside_sessions",
    "fullscript_client_pins", "health_suggestions", "historical_intake_snapshots", "household_holds",
    "household_members", "households", "inbound_leads", "ingredient_page_requests", "inquiries",
    "intake_responses", "intake_sessions", "life_stress_curations", "life_stress_selections",
    "live_event_attendance", "live_event_series_registrations", "masterclass_registrations",
    "member_data_sharing", "member_data_sharing_grants", "member_element_state",
    "member_reward_grants", "membership_comps", "mentor_page_requests", "module_certifications",
    "orders", "owned_tools", "peer_optin", "people", "person_attributes", "points_ledger",
    "portal_biofield_reports", "portal_card_state", "portal_cart_seeded", "portal_chat_messages",
    "portal_credentials", "portal_email_imports", "portal_event_registrations",
    "portal_extended_history", "portal_external_identities", "portal_fold_state",
    "portal_health_history", "portal_notify_state", "portal_process_requests",
    "portal_report_holds", "portal_triage", "practitioner_programs", "practitioner_recommendations",
    "product_reviews", "product_waitlist", "purchase_history", "purity_photos", "purity_ratings_access", "quest_state",
    "recommendation_events", "recommendation_hidden", "recommendation_notes",
    "recommendation_section_state", "referral_codes", "referral_redemptions", "repertoire",
    "review_gifts", "sales_page_viewers", "sales_page_votes", "scan_analyses", "scan_freshness",
    "scan_recommendations", "section_prefs", "sequence_enrollments", "share_headers", "shipments",
    "studio_bridge_claims", "studio_credit_claims", "subscriptions", "supplement_review_access",
    "supplement_reviews", "testimonial_invite_candidates", "topic_page_requests",
    "triage_invites",     # a health table for erasure (client_erasure.HEALTH_TABLES)
    "sequence_sends",     # joined to sequence_enrollments by address; moves with it
    # Found in production on 2026-09-27; a repo scan had missed every one:
    "biofield_month_grants", "biofield_reveal_spend_unlocks", "care_taster_grants",
    "coach_sub_charges", "coach_waitlist", "coaching_interest", "community_signals",
    "family_sub_charges", "family_sub_grants", "healing_oasis_requests", "journey_state",
    "member_interest", "membership_product_grants", "memberships", "order_membership_grants",
    "order_payments", "peer_interest", "peer_matches", "portal_library", "prepay_term_grants",
    "quiz_responses", "studio_credit_intents", "voice_signals",
})
_PERSON_COLUMNS = ("person_id", "people_id")
_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


def _is_pg(cx):
    return db.backend_of(cx) == "postgres"


def _is_email_col(name):
    n = name.lower()
    return "email" in n and not n.endswith("_at") and not n.endswith("_hash")


def _is_person_col(name):
    return name in _PERSON_COLUMNS or name.endswith("_person_id")


def _sqlite_is_text(decl):
    d = (decl or "").upper()
    return d == "" or "TEXT" in d or "CHAR" in d


def repo_tables_with_person_columns(src):
    """Tables created in source text that carry an address or person column. Parses each
    CREATE TABLE to its matching parenthesis, so a table inside a multi-statement
    executescript block is found too (memberships was missed by a simpler regex)."""
    import re
    found = set()
    for m in re.finditer(r'CREATE TABLE IF NOT EXISTS\s+"?(\w+)"?\s*\(', src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        body = src[m.end():i]
        cols = re.findall(r'(?:^|[,(\n"])\s*"?\s*(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB|BYTEA|BIGINT|'
                          r'DOUBLE|NUMERIC|TIMESTAMP)', body)
        if any(_is_email_col(c) or _is_person_col(c) for c in cols):
            found.add(m.group(1))
    return found


def qualified(schema, table):
    return f'"{schema}"."{table}"' if schema else f'"{table}"'


def targets(cx):
    out = []
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema NOT IN (?,?,?) AND table_schema NOT LIKE 'pg_temp%'",
            _SYSTEM_SCHEMAS).fetchall()
        for schema, table, col, dtype in rows:
            if _is_email_col(col) and dtype in ("text", "character varying"):
                out.append(Target(schema, table, col, "email"))
            elif _is_person_col(col):
                out.append(Target(schema, table, col, "person"))
    else:
        tables = [r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            for c in cx.execute(f'PRAGMA table_info("{table}")').fetchall():
                col, decl = c[1], c[2]
                if _is_email_col(col) and _sqlite_is_text(decl):
                    out.append(Target(None, table, col, "email"))
                elif _is_person_col(col):
                    out.append(Target(None, table, col, "person"))
    return sorted(out, key=lambda t: (t.schema or "", t.table, t.column))


def unique_sets(cx, schema, table):
    """Column tuples of every PLAIN unique index or primary key: no predicate, no expression.
    Partial and expression indexes are left to the database, which refuses a violating write."""
    sets = []
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) "
            "FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=ANY(ix.indkey) "
            "WHERE n.nspname=? AND t.relname=? AND ix.indisunique "
            "AND ix.indpred IS NULL AND ix.indexprs IS NULL GROUP BY ix.indexrelid",
            (schema or "public", table)).fetchall()
        sets = [tuple(r[0]) for r in rows]
    else:
        for idx in cx.execute(f'PRAGMA index_list("{table}")').fetchall():
            if not idx[2] or (len(idx) > 4 and idx[4]):
                continue
            cols = [r[2] for r in cx.execute(f'PRAGMA index_info("{idx[1]}")').fetchall()]
            if cols and all(cols):
                sets.append(tuple(cols))
        pk = [c[1] for c in sorted(cx.execute(f'PRAGMA table_info("{table}")').fetchall(),
                                   key=lambda c: c[5]) if c[5]]
        if pk and tuple(pk) not in sets:
            sets.append(tuple(pk))
    return sets


def partial_unique_sets(cx, schema, table):
    """(columns, predicate SQL) for every PARTIAL unique index with no expression, such as
    ux_carts_open_email ON carts(email) WHERE status='open'. unique_sets leaves these out;
    the merge engine needs them to see a clash before the database refuses the move
    (2026-09-27: two open carts refused Peach Goddard's merge)."""
    out = []
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)), "
            "min(pg_get_expr(ix.indpred, ix.indrelid)) "
            "FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            # Key columns only: INCLUDE columns are stored, not unique (review round 2).
            "JOIN pg_attribute a ON a.attrelid=t.oid "
            "AND a.attnum=ANY((ix.indkey::int2[])[0:ix.indnkeyatts - 1]) "
            "WHERE n.nspname=? AND t.relname=? AND ix.indisunique "
            "AND ix.indpred IS NOT NULL AND ix.indexprs IS NULL GROUP BY ix.indexrelid",
            (schema or "public", table)).fetchall()
        return [(tuple(r[0]), r[1]) for r in rows if r[1]]
    for idx in cx.execute(f'PRAGMA index_list("{table}")').fetchall():
        if not idx[2] or not (len(idx) > 4 and idx[4]):
            continue
        cols = [r[2] for r in cx.execute(f'PRAGMA index_info("{idx[1]}")').fetchall()]
        sql = (cx.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                          (idx[1],)).fetchone() or [""])[0] or ""
        sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip()   # comments
        parts = re.split(r"\bWHERE\b", sql, flags=re.I)
        if cols and all(cols) and len(parts) == 2:
            out.append((tuple(cols), parts[1].strip()))
    return out


def key_columns(cx, schema, table):
    """Primary key columns, or None when the table has none (rows are then addressed by
    rowid on SQLite and by a one-row ctid subquery on Postgres)."""
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT a.attname FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=ANY(ix.indkey) "
            "WHERE n.nspname=? AND t.relname=? AND ix.indisprimary "
            "ORDER BY array_position(ix.indkey, a.attnum)", (schema or "public", table)).fetchall()
        return [r[0] for r in rows] or None
    info = cx.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk = [c[1] for c in sorted(info, key=lambda c: c[5]) if c[5]]
    return pk or None


def all_columns(cx, schema, table):
    if _is_pg(cx):
        return [r[0] for r in cx.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND "
            "table_name=? ORDER BY ordinal_position", (schema or "public", table)).fetchall()]
    return [c[1] for c in cx.execute(f'PRAGMA table_info("{table}")').fetchall()]
