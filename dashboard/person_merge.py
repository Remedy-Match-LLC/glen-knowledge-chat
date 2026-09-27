"""Merge two people: preview, apply, undo and the hourly sweep.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Nothing here commits:
the caller holds the transaction, commits on success and rolls back on any exception, so a
failure part-way leaves nothing moved and nothing recorded as applied.

Every changed row is logged in person_merge_changes. A `rekey` moves a row to the survivor;
a `set_aside` removes a row that would clash with one the survivor already holds, keeping it
whole in the log. Undo replays the log backwards."""
import json
from datetime import datetime, timezone

from dashboard import db, dbwrite
from dashboard import person_aliases as pa
from dashboard import person_merge_discover as pd

# A portal page's parts that belong to one analysis, kept together as a set (Glen, 2026-09-26).
ANALYSIS_KEYS = ("greeting", "video", "layers", "findings", "report_pdf", "audio",
                 "reorder_items", "current_scan_date", "biofield_status", "auto_advance")
_SKIP_GENERIC = {"people", "client_portals"}


class MergeRefused(Exception):
    """A merge or undo that must not run. Nothing has been written."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def _eq(cx):
    return "IS NOT DISTINCT FROM" if db.backend_of(cx) == "postgres" else "IS"


def _rows(cx, sql, args=()):
    cur = cx.execute(sql, args)
    rows = cur.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [dict(zip(r.keys(), list(r))) for r in rows]
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _person(cx, pid):
    got = _rows(cx, "SELECT * FROM people WHERE id=?", (pid,))
    return got[0] if got else None


def _jsonable(v):
    return v if isinstance(v, (str, int, float, type(None))) else str(v)


def _dumps(obj):
    return json.dumps(obj, default=_jsonable, sort_keys=True)


def _key_where(cx, keys, row):
    eq = _eq(cx)
    return " AND ".join(f'"{k}" {eq} ?' for k in keys), [row[k] for k in keys]


class _Log:
    def __init__(self, cx, merge_id, source="apply"):
        self.cx, self.merge_id, self.source = cx, merge_id, source
        last = cx.execute("SELECT MAX(seq) FROM person_merge_changes WHERE merge_id=?",
                          (merge_id,)).fetchone()
        self.seq = (last[0] or 0) if last else 0

    def add(self, table, key, column, old, new, action, row=None):
        self.seq += 1
        self.cx.execute(
            "INSERT INTO person_merge_changes (merge_id, seq, table_name, key_json, column_name, "
            "old_value, new_value, action, row_json, source, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.merge_id, self.seq, table, _dumps(key), column,
             None if old is None else str(old), None if new is None else str(new), action,
             _dumps(row) if row is not None else None, self.source, _now()))


def _match_sql(t):
    if t.kind == "email":
        return f'lower(trim("{t.column}"))=?'
    return f'"{t.column}"=?'


def _clashes(cx, t, row, new):
    """True when moving this row to `new` would break a unique rule the survivor already holds."""
    eq = _eq(cx)
    for cols in pd.unique_sets(cx, t.table):
        if t.column not in cols:
            continue
        others = [c for c in cols if c != t.column]
        clause = _match_sql(t) + "".join(f' AND "{c}" {eq} ?' for c in others)
        if cx.execute(f'SELECT 1 FROM "{t.table}" WHERE {clause} LIMIT 1',
                      [new] + [row[c] for c in others]).fetchone():
            return True
    return False


def _move_target(cx, log, t, old, new, *, dry=False):
    """Move one column's rows from `old` to `new`. Returns (moved, set_aside)."""
    moved = aside = 0
    keys = pd.key_columns(cx, t.table)
    for row in _rows(cx, f'SELECT * FROM "{t.table}" WHERE {_match_sql(t)}', (old,)):
        clash = _clashes(cx, t, row, new)
        if dry:
            aside, moved = aside + clash, moved + (not clash)
            continue
        where, args = _key_where(cx, keys, row)
        if clash:
            log.add(t.table, {k: row[k] for k in keys}, t.column, row[t.column], None,
                    "set_aside", row)
            cx.execute(f'DELETE FROM "{t.table}" WHERE {where}', args)
            aside += 1
        else:
            after = {k: (new if k == t.column else row[k]) for k in keys}
            log.add(t.table, after, t.column, row[t.column], new, "rekey")
            cx.execute(f'UPDATE "{t.table}" SET "{t.column}"=? WHERE {where}', [new] + args)
            moved += 1
    return moved, aside


def _generic_targets(cx, kinds=("email", "person")):
    skip = pd.HISTORY_TABLES | pd.MERGE_OWN_TABLES | _SKIP_GENERIC
    return [t for t in pd.targets(cx) if t.table not in skip and t.kind in kinds]


def _has_table(cx, name):
    return any(t.table == name for t in pd.targets(cx))


def _portal_plan(cx, s_email, m_email):
    """Which portal row is kept, and the combined content. None when neither has one."""
    if not _has_table(cx, "client_portals"):
        return None
    rows = _rows(cx, "SELECT * FROM client_portals WHERE lower(trim(email)) IN (?,?)",
                 (s_email, m_email))
    if not rows:
        return None

    def rank(r):
        return (r.get("updated_at") or "", _norm(r["email"]) == s_email, r["id"])

    rows.sort(key=rank, reverse=True)
    keep, others = rows[0], rows[1:]
    content = json.loads(keep.get("content_json") or "{}")
    copied = []
    for o in others:
        for k, v in json.loads(o.get("content_json") or "{}").items():
            if k not in ANALYSIS_KEYS and k not in content:
                content[k] = v
                copied.append(k)
    return {"keep": keep, "others": others, "content": content, "copied": sorted(copied),
            "keep_is_merged": _norm(keep["email"]) != s_email}


def _portal_rule(cx, log, merge_id, s_email, m_email):
    plan = _portal_plan(cx, s_email, m_email)
    if not plan:
        return
    keys = pd.key_columns(cx, "client_portals")
    keep = plan["keep"]
    for o in plan["others"]:
        if o.get("token_hash"):
            pa.add_token_alias(cx, o["token_hash"], s_email, merge_id)
        where, args = _key_where(cx, keys, o)
        log.add("client_portals", {k: o[k] for k in keys}, "email", o["email"], None,
                "set_aside", o)
        cx.execute(f'DELETE FROM "client_portals" WHERE {where}', args)
    new_json = json.dumps(plan["content"])
    if plan["copied"]:
        where, args = _key_where(cx, keys, keep)
        log.add("client_portals", {k: keep[k] for k in keys}, "content_json",
                keep.get("content_json"), new_json, "rekey")
        cx.execute(f'UPDATE client_portals SET content_json=? WHERE {where}', [new_json] + args)
    if plan["keep_is_merged"]:
        where, args = _key_where(cx, keys, keep)
        log.add("client_portals", {k: (s_email if k == "email" else keep[k]) for k in keys},
                "email", keep["email"], s_email, "rekey")
        cx.execute(f'UPDATE client_portals SET email=? WHERE {where}', [s_email] + args)
        _carry_raw_token(cx, log, s_email, m_email)


def _carry_raw_token(cx, log, s_email, m_email):
    """Re-sent links must carry the kept page's token: give the survivor the merged raw token."""
    if not _has_table(cx, "portal_notify_state"):
        return
    m = cx.execute("SELECT portal_token FROM portal_notify_state WHERE lower(trim(email))=?",
                   (m_email,)).fetchone()
    s = cx.execute("SELECT portal_token FROM portal_notify_state WHERE lower(trim(email))=?",
                   (s_email,)).fetchone()
    if m and m[0] and s is not None:
        log.add("portal_notify_state", {"email": s_email}, "portal_token", s[0], m[0], "rekey")
        cx.execute("UPDATE portal_notify_state SET portal_token=? WHERE lower(trim(email))=?",
                   (m[0], s_email))


def preview(cx, survivor_id, merged_id):
    """What apply would do. Writes nothing."""
    s, m = _person(cx, survivor_id), _person(cx, merged_id)
    if not s or not m:
        raise MergeRefused("both people must exist")
    s_email, m_email = _norm(s["email"]), _norm(m["email"])
    counts, clashes = {}, {}
    for t in _generic_targets(cx):
        old, new = (m_email, s_email) if t.kind == "email" else (merged_id, survivor_id)
        n_m = cx.execute(f'SELECT COUNT(*) FROM "{t.table}" WHERE {_match_sql(t)}', (old,)).fetchone()[0]
        n_s = cx.execute(f'SELECT COUNT(*) FROM "{t.table}" WHERE {_match_sql(t)}', (new,)).fetchone()[0]
        if n_m or n_s:
            prev = counts.get(t.table, [0, 0])
            counts[t.table] = [max(prev[0], n_m), max(prev[1], n_s)]
        if n_m:
            _, aside = _move_target(cx, None, t, old, new, dry=True)
            if aside:
                clashes[t.table] = clashes.get(t.table, 0) + aside
    plan = _portal_plan(cx, s_email, m_email)
    portal = {"keep": None, "reason": "neither person has a portal page", "from_other": []}
    if plan:
        who = "merged" if plan["keep_is_merged"] else "survivor"
        portal = {"keep": who, "from_other": plan["copied"],
                  "reason": f"the {who}'s page was updated most recently "
                            f"({plan['keep'].get('updated_at') or 'no date'})"}
    return {"survivor": {"id": survivor_id, "email": s_email, "name": s.get("name")},
            "merged": {"id": merged_id, "email": m_email, "name": m.get("name")},
            "counts": counts, "clashes": clashes, "portal": portal}


def apply(cx, *, survivor_id, merged_id, mail_old, evidence, suggestion, applied_by,
          merge_people_fields):
    if survivor_id == merged_id:
        raise MergeRefused("a person cannot be merged into themselves")
    if mail_old not in ("stop", "keep"):
        raise MergeRefused("mail_old must be stop or keep")
    s, m = _person(cx, survivor_id), _person(cx, merged_id)
    if not s or not m:
        raise MergeRefused("both people must exist")
    s_email, m_email = _norm(s["email"]), _norm(m["email"])
    if pa.canonical_email(cx, m_email) != m_email:
        raise MergeRefused(f"{m_email} is already merged into another person")
    if pa.canonical_email(cx, s_email) != s_email:
        raise MergeRefused(f"{s_email} is itself merged into another person")
    merge_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO person_merges (survivor_person_id, merged_person_id, survivor_email, "
        "merged_email, merged_person_json, survivor_before_json, evidence_json, suggestion_json, "
        "mail_old, applied_by, applied_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (survivor_id, merged_id, s_email, m_email, _dumps(m), _dumps(s), _dumps(evidence),
         _dumps(suggestion), mail_old, applied_by, _now()))
    log = _Log(cx, merge_id)
    _portal_rule(cx, log, merge_id, s_email, m_email)
    for t in _generic_targets(cx):
        old, new = (m_email, s_email) if t.kind == "email" else (merged_id, survivor_id)
        _move_target(cx, log, t, old, new)
    merge_people_fields(cx, survivor_id, merged_id)
    cx.execute("UPDATE person_merges SET survivor_after_json=? WHERE id=?",
               (_dumps(_person(cx, survivor_id)), merge_id))
    pa.add_alias(cx, m_email, s_email, merge_id)
    return merge_id


def _identity_always(cx, table, column="id"):
    if db.backend_of(cx) != "postgres":
        return False
    row = cx.execute("SELECT identity_generation FROM information_schema.columns "
                     "WHERE table_schema=current_schema() AND table_name=? AND column_name=?",
                     (table, column)).fetchone()
    return bool(row and row[0] == "ALWAYS")


def _insert_row(cx, table, row):
    cols = list(row)
    over = " OVERRIDING SYSTEM VALUE" if _identity_always(cx, table) else ""
    cx.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)})'
               f'{over} VALUES ({", ".join("?" for _ in cols)})', [row[c] for c in cols])


def _restore_change(cx, ch, skipped):
    table, col = ch["table_name"], ch["column_name"]
    key = json.loads(ch["key_json"] or "{}")
    if ch["action"] == "set_aside":
        try:
            if db.backend_of(cx) == "postgres":
                cx.execute("SAVEPOINT pm_restore")
            _insert_row(cx, table, json.loads(ch["row_json"]))
            if db.backend_of(cx) == "postgres":
                cx.execute("RELEASE SAVEPOINT pm_restore")
        except Exception:
            if db.backend_of(cx) == "postgres":
                cx.execute("ROLLBACK TO SAVEPOINT pm_restore")
            skipped.append({"table": table, "key": key, "reason": "a newer row holds this key"})
        return
    eq = _eq(cx)
    where = " AND ".join(f'"{k}" {eq} ?' for k in key)
    cur = cx.execute(f'UPDATE "{table}" SET "{col}"=? WHERE {where} AND "{col}"=?',
                     [ch["old_value"]] + list(key.values()) + [ch["new_value"]])
    if cur.rowcount == 0:
        skipped.append({"table": table, "key": key, "reason": "changed since the merge"})


def undo(cx, merge_id, undone_by):
    rows = _rows(cx, "SELECT * FROM person_merges WHERE id=?", (merge_id,))
    if not rows:
        raise MergeRefused("no such merge")
    mg = rows[0]
    if mg.get("undone_at"):
        raise MergeRefused("this merge was already undone")
    later = cx.execute(
        "SELECT id FROM person_merges WHERE id>? AND undone_at IS NULL AND "
        "(merged_person_id=? OR survivor_person_id=?) ORDER BY id LIMIT 1",
        (merge_id, mg["survivor_person_id"], mg["survivor_person_id"])).fetchone()
    if later:
        raise MergeRefused(f"undo merge {later[0]} first: it depends on this one")
    skipped = []
    restored = 0
    changes = _rows(cx, "SELECT * FROM person_merge_changes WHERE merge_id=? ORDER BY seq DESC",
                    (merge_id,))
    for ch in changes:
        before = len(skipped)
        _restore_change(cx, ch, skipped)
        restored += len(skipped) == before
    _insert_row(cx, "people", json.loads(mg["merged_person_json"]))
    before_s = json.loads(mg["survivor_before_json"] or "{}")
    after_s = json.loads(mg["survivor_after_json"] or "{}")
    now_s = _person(cx, mg["survivor_person_id"]) or {}
    for col, old in before_s.items():
        if col == "id" or after_s.get(col) == old:
            continue
        if str(now_s.get(col)) == str(after_s.get(col)):
            cx.execute(f'UPDATE people SET "{col}"=? WHERE id=?', (old, mg["survivor_person_id"]))
        else:
            skipped.append({"table": "people", "key": {"id": mg["survivor_person_id"]},
                            "reason": f"{col} changed since the merge"})
    pa.remove_merge_aliases(cx, merge_id)
    cx.execute("UPDATE person_merges SET undone_at=?, undone_by=? WHERE id=?",
               (_now(), undone_by, merge_id))
    return {"restored": restored, "skipped": skipped}


def sweep(cx):
    """Move rows written under an old address since its merge. Returns rows moved."""
    moved = 0
    for alias, _canonical in pa.all_aliases(cx):
        row = cx.execute("SELECT merge_id FROM email_aliases WHERE alias_email=?",
                         (alias,)).fetchone()
        merge_id = row[0] if row else None
        canonical = pa.canonical_email(cx, alias)
        log = _Log(cx, merge_id, source="sweep")
        for t in _generic_targets(cx, kinds=("email",)):
            n, aside = _move_target(cx, log, t, alias, canonical)
            moved += n + aside
    return moved
