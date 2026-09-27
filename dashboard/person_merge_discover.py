"""Which tables and columns a person merge moves, on SQLite or Postgres.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Discovery runs at apply
time, so a table added later is moved without a code change. The same rule as the read-only
dry run (00 System/scripts/prod-queries/person-merge-dry-run.py): a text column whose name
contains "email", or a column named person_id or people_id."""
from collections import namedtuple

from dashboard import db

Target = namedtuple("Target", "table column kind")

# Never rewritten: they record what happened, and rewriting them would falsify it.
HISTORY_TABLES = frozenset({
    "ghl_write_queue",                    # what was queued to GoHighLevel, for which contact
    "portal_auth_events",                 # who signed in, as recorded at the time
    "email_click_tokens",                 # which address a sent link went to
    "cadence_clicks",                     # clicks on links sent to an address
    "weekly_live_invitation_recipients",  # who was sent which invitation
    "portal_welcome_sent",                # which address got the welcome email
    "email_suppression",                  # an address-level block; moving it would block the survivor
    "pending_merges",                     # the old merge queue's own history
})
# The merge tool's own tables.
MERGE_OWN_TABLES = frozenset({
    "person_merges", "email_aliases", "portal_token_aliases", "person_merge_changes",
})

_PERSON_COLUMNS = ("person_id", "people_id")


def _is_pg(cx):
    return db.backend_of(cx) == "postgres"


def _sqlite_is_text(decl):
    d = (decl or "").upper()
    return d == "" or "TEXT" in d or "CHAR" in d


def targets(cx):
    out = []
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema=current_schema()").fetchall()
        for table, col, dtype in rows:
            if "email" in col.lower() and dtype in ("text", "character varying"):
                out.append(Target(table, col, "email"))
            elif col in _PERSON_COLUMNS:
                out.append(Target(table, col, "person"))
    else:
        tables = [r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            for c in cx.execute(f'PRAGMA table_info("{table}")').fetchall():
                col, decl = c[1], c[2]
                if "email" in col.lower() and _sqlite_is_text(decl):
                    out.append(Target(table, col, "email"))
                elif col in _PERSON_COLUMNS:
                    out.append(Target(table, col, "person"))
    return sorted(out)


def unique_sets(cx, table):
    """Column tuples of every unique index or primary key on the table."""
    sets = []
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) "
            "FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=ANY(ix.indkey) "
            "WHERE n.nspname=current_schema() AND t.relname=? AND ix.indisunique "
            "GROUP BY ix.indexrelid", (table,)).fetchall()
        sets = [tuple(r[0]) for r in rows]
    else:
        for idx in cx.execute(f'PRAGMA index_list("{table}")').fetchall():
            if idx[2]:
                cols = [r[2] for r in cx.execute(f'PRAGMA index_info("{idx[1]}")').fetchall()]
                sets.append(tuple(cols))
        pk = [c[1] for c in sorted(cx.execute(f'PRAGMA table_info("{table}")').fetchall(),
                                   key=lambda c: c[5]) if c[5]]
        if pk and tuple(pk) not in sets:
            sets.append(tuple(pk))
    return sets


def key_columns(cx, table):
    """Primary key columns, or every column when the table has none."""
    if _is_pg(cx):
        rows = cx.execute(
            "SELECT a.attname FROM pg_index ix JOIN pg_class t ON t.oid=ix.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=ANY(ix.indkey) "
            "WHERE n.nspname=current_schema() AND t.relname=? AND ix.indisprimary "
            "ORDER BY array_position(ix.indkey, a.attnum)", (table,)).fetchall()
        if rows:
            return [r[0] for r in rows]
        return [r[0] for r in cx.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position",
            (table,)).fetchall()]
    info = cx.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk = [c[1] for c in sorted(info, key=lambda c: c[5]) if c[5]]
    return pk or [c[1] for c in info]
