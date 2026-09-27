"""The address map and merge log for merging two people.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Every identity entry
point asks canonical_email() first, so a merged address lands on the survivor."""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def init_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS person_merges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        survivor_person_id INTEGER, merged_person_id INTEGER,
        survivor_email TEXT, merged_email TEXT,
        merged_person_json TEXT, survivor_before_json TEXT, survivor_after_json TEXT,
        evidence_json TEXT, suggestion_json TEXT, mail_old TEXT,
        applied_by TEXT, applied_at TEXT, undone_at TEXT, undone_by TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS email_aliases (
        alias_email TEXT PRIMARY KEY, canonical_email TEXT NOT NULL,
        merge_id INTEGER, created_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS portal_token_aliases (
        token_hash TEXT PRIMARY KEY, canonical_email TEXT NOT NULL,
        merge_id INTEGER, created_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS person_merge_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, merge_id INTEGER, seq INTEGER,
        table_name TEXT, key_json TEXT, column_name TEXT,
        old_value TEXT, new_value TEXT, action TEXT, row_json TEXT, source TEXT,
        created_at TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_pmc_merge ON person_merge_changes(merge_id, seq)")
    cx.commit()


def canonical_email(cx, email, _depth=0):
    e = _norm(email)
    if not e or _depth > 10:
        return e
    row = cx.execute("SELECT canonical_email FROM email_aliases WHERE alias_email=?",
                     (e,)).fetchone()
    return canonical_email(cx, row[0], _depth + 1) if row else e


def add_alias(cx, alias, canonical, merge_id):
    a, c = _norm(alias), _norm(canonical)
    if not a or not c or a == c:
        raise ValueError("an alias needs two different addresses")
    if cx.execute("SELECT 1 FROM email_aliases WHERE alias_email=?", (a,)).fetchone():
        raise ValueError(f"{a} is already merged into another person")
    if canonical_email(cx, c) == a:
        raise ValueError("this merge would make a loop")
    cx.execute("INSERT INTO email_aliases (alias_email, canonical_email, merge_id, created_at) "
               "VALUES (?,?,?,?)", (a, c, merge_id, _now()))


def add_token_alias(cx, token_hash, canonical, merge_id):
    cx.execute("INSERT INTO portal_token_aliases (token_hash, canonical_email, merge_id, created_at) "
               "VALUES (?,?,?,?)", (token_hash, _norm(canonical), merge_id, _now()))


def token_alias(cx, token_hash):
    row = cx.execute("SELECT canonical_email FROM portal_token_aliases WHERE token_hash=?",
                     (token_hash,)).fetchone()
    return canonical_email(cx, row[0]) if row else None


def aliases_for_merge(cx, merge_id):
    return [r[0] for r in cx.execute(
        "SELECT alias_email FROM email_aliases WHERE merge_id=?", (merge_id,))]


def remove_merge_aliases(cx, merge_id):
    cx.execute("DELETE FROM email_aliases WHERE merge_id=?", (merge_id,))
    cx.execute("DELETE FROM portal_token_aliases WHERE merge_id=?", (merge_id,))


def all_aliases(cx):
    return [(r[0], r[1]) for r in cx.execute(
        "SELECT alias_email, canonical_email FROM email_aliases")]
