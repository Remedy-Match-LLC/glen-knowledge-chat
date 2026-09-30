"""Merge two people: preview, apply, undo and the hourly sweep.

Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md. Nothing here commits:
the caller holds the transaction, commits on success and rolls back on any exception, so a
failure part-way leaves nothing moved and nothing recorded as applied.

Every changed row is logged in person_merge_changes. A `rekey` moves a row to the survivor,
or changes one value under a clash rule; a `set_aside` removes a row that lost a clash, keeping
it whole in the log. Undo replays the log backwards, one row at a time.

Clashes (Glen, 2026-09-27: "written-rule"): settled only by a rule in CLASH_RULES. A clash in
any other table blocks the merge (MergeBlocked) and, in the sweep, is left in place."""
import base64
import json
import re
from datetime import datetime, timezone

from dashboard import db, dbwrite
from dashboard import person_aliases as pa
from dashboard import person_merge_discover as pd

# A portal page's parts that belong to one analysis, kept together as a set (Glen, 2026-09-26).
ANALYSIS_KEYS = ("greeting", "video", "layers", "findings", "report_pdf", "audio",
                 "reorder_items", "current_scan_date", "biofield_status", "auto_advance")
# How a clash is settled, by table. Anything not here blocks.
CLASH_RULES = {
    # A waiting-list sign-up: the survivor's own row stays (2026-09-29, Retina Renew list).
    "product_waitlist": "prefer:confirmed_at,emailed_at",   # the confirmed sign-up stays
    # Notification settings: the survivor's row, plus the merged row's text opt-out and phone
    # when the survivor lacks them (Glen, 2026-09-27: a text opt-out is the person's choice).
    "portal_notify_state": "notify",
    "portal_cart_seeded": "survivor",      # an "already seeded" marker
    "portal_fold_state": "survivor",       # which cards are folded
    # The cart touched last stays open; the other's items fold into it (Glen, 2026-09-27).
    "carts": "fold_cart:updated_at",
    "scan_freshness": "newest:last_scan_date",
    # Same scan date under both: an approved reveal first, then the later one (round 3).
    "biofield_reveals": "prefer:first_approved,updated_at",
    "evox_session_credits": "sum:credits",     # credits add up
}
_SKIP_GENERIC = {"people", "client_portals"}
# An owner's choice, for one merge only, settles a clash in a table with no written rule
# (Glen, 2026-09-27, Peach's affiliate record). Passed through as a parameter, never held
# globally, so a preview running alongside an apply cannot change it.


class MergeRefused(Exception):
    """A merge or undo that must not run. Nothing has been written."""


class MergeBlocked(MergeRefused):
    """A clash with no written rule. `clashes` lists {table, column, merged_row, survivor_row}."""

    def __init__(self, clashes):
        self.clashes = clashes
        names = sorted({c["table"] for c in clashes})
        super().__init__("clashes with no written rule in: " + ", ".join(names))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(email):
    return (email or "").strip().lower()


def _is_pg(cx):
    return db.backend_of(cx) == "postgres"


def _eq(cx):
    return "IS NOT DISTINCT FROM" if _is_pg(cx) else "IS"


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


# ── Values in the log: bytes and JSON survive the round trip ─────────────────
def _enc(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, (dict, list)):
        return {"__json__": json.dumps(v)}
    if isinstance(v, (str, int, float, type(None), bool)):
        return v
    return str(v)


def _dec(v):
    if isinstance(v, dict) and "__b64__" in v:
        return base64.b64decode(v["__b64__"])
    if isinstance(v, dict) and "__json__" in v:
        return v["__json__"]
    return v


def _dumps(obj):
    out = {}
    for k, v in obj.items():
        out[k] = ({c: _enc(x) for c, x in v.items()} if k == "__row__" else _enc(v))
    return json.dumps(out, sort_keys=True)


def _loads(s):
    out = {}
    for k, v in json.loads(s or "{}").items():
        out[k] = ({c: _dec(x) for c, x in v.items()} if k == "__row__" else _dec(v))
    return out


# ── Addressing one row ────────────────────────────────────────────────────────
def _row_where(cx, schema, table, row, keys):
    """WHERE clause and args that address exactly this row."""
    eq = _eq(cx)
    if keys:
        return " AND ".join(f'"{k}" {eq} ?' for k in keys), [row[k] for k in keys]
    cols = [c for c in row if c not in ("rowid", "ctid")]
    match = " AND ".join(f'"{c}" {eq} ?' for c in cols)
    rid = "ctid" if _is_pg(cx) else "rowid"
    return (f'{rid} = (SELECT {rid} FROM {pd.qualified(schema, table)} WHERE {match} LIMIT 1)',
            [row[c] for c in cols])


def _savepoint(cx, fn):
    """Run fn inside a savepoint. Returns (ok, error). A refusal rolls back only fn."""
    cx.execute("SAVEPOINT pm_step")
    try:
        fn()
    except Exception as e:
        cx.execute("ROLLBACK TO SAVEPOINT pm_step")
        cx.execute("RELEASE SAVEPOINT pm_step")
        return False, e
    cx.execute("RELEASE SAVEPOINT pm_step")
    return True, None


class _Log:
    def __init__(self, cx, merge_id, source="apply"):
        self.cx, self.merge_id, self.source = cx, merge_id, source
        last = cx.execute("SELECT MAX(seq) FROM person_merge_changes WHERE merge_id=?",
                          (merge_id,)).fetchone()
        self.seq = (last[0] or 0) if last else 0

    def add(self, schema, table, key, column, old, new, action, row=None):
        self.seq += 1
        name = f"{schema}.{table}" if schema else table
        self.cx.execute(
            "INSERT INTO person_merge_changes (merge_id, seq, table_name, key_json, column_name, "
            "old_value, new_value, action, row_json, source, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.merge_id, self.seq, name, _dumps(key), column,
             None if old is None else str(old), None if new is None else str(new), action,
             _dumps(row) if row is not None else None, self.source, _now()))


def _split_name(name):
    return tuple(name.split(".", 1)) if "." in name else (None, name)


# ── Clashes ───────────────────────────────────────────────────────────────────
def _match_sql(t):
    # A person number may be stored as an integer or as text (portal_card_state). _val sends
    # it as text, which both backends coerce to the column's type, and a plain comparison
    # keeps the column's index usable (review round 3, final round).
    return f'lower(trim("{t.column}"))=?' if t.kind == "email" else f'"{t.column}"=?'


def _val(t, v):
    return str(v) if t.kind == "person" else v


def _clash_row(cx, t, row, new):
    """The survivor's row this one would collide with under a plain unique rule, or None.
    A NULL never collides, as in the database."""
    for cols in pd.unique_sets(cx, t.schema, t.table):
        if t.column not in cols:
            continue
        others = [c for c in cols if c != t.column]
        if any(row[c] is None for c in others):
            continue
        clause = _match_sql(t) + "".join(f' AND "{c}" = ?' for c in others)
        got = _rows(cx, f'SELECT * FROM {pd.qualified(t.schema, t.table)} WHERE {clause} LIMIT 1',
                    [_val(t, new)] + [row[c] for c in others])
        if got:
            return got[0]
    # A partial unique index (one OPEN cart per address) clashes only when both rows meet its
    # condition (2026-09-27: Peach Goddard's two open carts refused her merge).
    for cols, pred in pd.partial_unique_sets(cx, t.schema, t.table):
        if t.column not in cols:
            continue
        others = [c for c in cols if c != t.column]
        if any(row[c] is None for c in others):
            continue
        q = pd.qualified(t.schema, t.table)
        where, args = _row_where(cx, t.schema, t.table, row, pd.key_columns(cx, t.schema, t.table))
        # Judged on the row as it would be AFTER the move: the condition may name the
        # address itself (review round 2).
        moved = ", ".join((_param_as(cx, t) + f' AS "{c}"') if c == t.column else f'"{c}"'
                          for c in row)
        if not _rows(cx, f"SELECT 1 FROM (SELECT {moved} FROM {q} WHERE {where}) AS moved "
                         f"WHERE ({pred}) LIMIT 1", [_val(t, new)] + args):
            continue
        clause = _match_sql(t) + "".join(f' AND "{c}" = ?' for c in others) + f" AND ({pred})"
        got = _rows(cx, f"SELECT * FROM {q} WHERE {clause} LIMIT 1",
                    [_val(t, new)] + [row[c] for c in others])
        if got:
            return got[0]
    return None


def _param_as(cx, t):
    """A placeholder typed like the column, so Postgres can compare it in a condition."""
    if not _is_pg(cx):
        return "?"
    r = cx.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=? "
                   "AND table_name=? AND column_name=?",
                   (t.schema or "public", t.table, t.column)).fetchone()
    typ = r[0] if r and re.fullmatch(r"[a-z ]+", r[0] or "") else "text"
    return f"CAST(? AS {typ})"


def _key_of(row, keys):
    return {k: row[k] for k in keys} if keys else {"__row__": row}


def _set_aside(cx, log, t, row, keys):
    where, args = _row_where(cx, t.schema, t.table, row, keys)
    log.add(t.schema, t.table, _key_of(row, keys), t.column, row[t.column], None, "set_aside", row)
    cx.execute(f'DELETE FROM {pd.qualified(t.schema, t.table)} WHERE {where}', args)


def _rekey(cx, log, t, row, keys, new):
    new = _val(t, new)        # a person number travels as text: fits integer and text columns
    where, args = _row_where(cx, t.schema, t.table, row, keys)
    after = dict(row)
    after[t.column] = new
    log.add(t.schema, t.table, _key_of(after, keys), t.column, row[t.column], new, "rekey")
    cx.execute(f'UPDATE {pd.qualified(t.schema, t.table)} SET "{t.column}"=? WHERE {where}',
               [new] + args)


def _fold_cart_items(cx, log, schema, src, dst):
    """Fold one cart's items into another, the way the shop folds carts
    (cart_store._fold_cart_items): the higher quantity wins for a product in both, nothing is
    summed, and the source cart is left empty. Every change is logged, so undo puts each
    item back in its own cart. Nothing is committed here."""
    if not src or not dst or src == dst:
        return
    t = pd.Target(schema, "cart_items", "token", "cart")
    keys = pd.key_columns(cx, schema, "cart_items")
    q = pd.qualified(schema, "cart_items")
    for it in _rows(cx, f"SELECT * FROM {q} WHERE token=?", (src,)):
        have = _rows(cx, f"SELECT * FROM {q} WHERE token=? AND slug=? AND fmt=?",
                     (dst, it["slug"], it["fmt"]))
        if have:
            if int(it["qty"]) > int(have[0]["qty"]):
                where, args = _row_where(cx, schema, "cart_items", have[0], keys)
                log.add(schema, "cart_items", _key_of(have[0], keys), "qty", have[0]["qty"],
                        it["qty"], "rekey")
                cx.execute(f"UPDATE {q} SET qty=? WHERE {where}", [it["qty"]] + args)
        else:
            added = dict(it, token=dst)
            log.add(schema, "cart_items", _key_of(added, keys), "token", None, dst, "added", added)
            _insert_row(cx, schema, "cart_items", added)
        _set_aside(cx, log, t, it, keys)


def _mark_cart_merged(cx, log, t, cart, keys):
    where, args = _row_where(cx, t.schema, t.table, cart, keys)
    log.add(t.schema, t.table, _key_of(cart, keys), "status", cart.get("status"), "merged", "rekey")
    cx.execute(f"UPDATE {pd.qualified(t.schema, t.table)} SET status='merged' WHERE {where}", args)


def _settle(cx, log, t, row, other, keys, new, choices=None):
    """Settle a clash by the table's rule, or the owner's choice for this merge. Raises
    MergeBlocked when there is neither."""
    rule = CLASH_RULES.get(t.table) or (choices or {}).get(t.table)
    if not rule:
        raise MergeBlocked([{"table": t.table, "column": t.column, "merged_row": row,
                             "survivor_row": other}])
    if rule == "survivor":
        _set_aside(cx, log, t, row, keys)
    elif rule == "merged":
        _set_aside(cx, log, t, other, keys)
        _rekey(cx, log, t, row, keys, new)
    elif rule == "notify":
        carry = {}
        if (row.get("opt_status") or "") == "out" and (other.get("opt_status") or "") != "out":
            carry["opt_status"] = "out"
        if (row.get("phone") or "").strip() and not (other.get("phone") or "").strip():
            carry["phone"] = row["phone"]
        where, args = _row_where(cx, t.schema, t.table, other, keys)
        for col, val in carry.items():
            log.add(t.schema, t.table, _key_of(other, keys), col, other.get(col), val, "rekey")
            cx.execute(f'UPDATE {pd.qualified(t.schema, t.table)} SET "{col}"=? WHERE {where}',
                       [val] + args)
        _set_aside(cx, log, t, row, keys)
    elif rule.startswith("fold_cart:") and "token" in row and "status" in row:
        # The folded cart is marked 'merged', as the shop marks it, never deleted: a
        # signed-out browser holding its token would recreate it empty (review round 3).
        col = rule.split(":", 1)[1]
        if str(row.get(col) or "") > str(other.get(col) or ""):
            _fold_cart_items(cx, log, t.schema, other.get("token"), row.get("token"))
            _mark_cart_merged(cx, log, t, other, keys)
        else:
            _fold_cart_items(cx, log, t.schema, row.get("token"), other.get("token"))
            _mark_cart_merged(cx, log, t, row, keys)
        _rekey(cx, log, t, row, keys, new)
    elif rule.startswith("newest:") or rule.startswith("fold_cart:"):
        col = rule.split(":", 1)[1]
        if str(row.get(col) or "") > str(other.get(col) or ""):
            _set_aside(cx, log, t, other, keys)
            _rekey(cx, log, t, row, keys, new)
        else:
            _set_aside(cx, log, t, row, keys)
    elif rule.startswith("prefer:"):
        flag, col = rule.split(":", 1)[1].split(",")

        def rank(r):
            return (bool(r.get(flag)), str(r.get(col) or ""))

        if rank(row) > rank(other):
            _set_aside(cx, log, t, other, keys)
            _rekey(cx, log, t, row, keys, new)
        else:
            _set_aside(cx, log, t, row, keys)
    elif rule.startswith("sum:"):
        col = rule.split(":", 1)[1]
        total = (other.get(col) or 0) + (row.get(col) or 0)
        where, args = _row_where(cx, t.schema, t.table, other, keys)
        log.add(t.schema, t.table, _key_of(other, keys) if keys else {"__row__": {**other, col: total}},
                col, other.get(col), total, "rekey")
        cx.execute(f'UPDATE {pd.qualified(t.schema, t.table)} SET "{col}"=? WHERE {where}',
                   [total] + args)
        _set_aside(cx, log, t, row, keys)


def _move_target(cx, log, t, old, new, *, dry=False, blocked=None, choices=None):
    """Move one column's rows from `old` to `new`. Returns (moved, settled).
    `blocked`, when a list, collects unsettled clashes instead of raising (the sweep)."""
    moved = settled = 0
    keys = pd.key_columns(cx, t.schema, t.table)
    for row in _rows(cx, f'SELECT * FROM {pd.qualified(t.schema, t.table)} WHERE {_match_sql(t)}',
                     (_val(t, old),)):
        other = _clash_row(cx, t, row, new)
        if dry:
            if other is not None:
                settled += 1
                if (t.table not in CLASH_RULES and t.table not in (choices or {})
                        and blocked is not None):
                    blocked.append({"table": t.table, "column": t.column,
                                    "choice": t.table not in NO_CHOICE_TABLES})
            else:
                moved += 1
            continue
        if other is not None:
            ok, err = _savepoint(cx, lambda: _settle(cx, log, t, row, other, keys, new, choices))
            if not ok:
                if isinstance(err, MergeBlocked) and blocked is not None:
                    blocked.extend(err.clashes)
                    continue
                raise err
            settled += 1
            continue
        ok, err = _savepoint(cx, lambda: _rekey(cx, log, t, row, keys, new))
        if not ok:
            # A partial or expression unique index the database enforces refused the move.
            clash = [{"table": t.table, "column": t.column, "merged_row": row,
                      "survivor_row": None, "refused_by": type(err).__name__}]
            if blocked is not None:
                blocked.extend(clash)
                continue
            raise MergeBlocked(clash)
        moved += 1
    return moved, settled


def _generic_targets(cx, kinds=("email", "person")):
    """Reviewed tables only (MOVE_TABLES). Anything else is never moved (round 3)."""
    own = _own_schema(cx)
    return [t for t in pd.targets(cx) if t.table in pd.MOVE_TABLES and t.kind in kinds
            and not (t.table in _SKIP_GENERIC and t.schema == own)]


def _unreviewed_with_rows(cx, old_email, old_id, kinds=("email", "person")):
    """Tables on neither reviewed list that hold this person's rows: they block."""
    named = pd.MOVE_TABLES | pd.HISTORY_TABLES | pd.MERGE_OWN_TABLES
    out = []
    for t in pd.targets(cx):
        if t.table in named or t.kind not in kinds:
            continue
        v = old_email if t.kind == "email" else old_id
        if v is None:
            continue
        if cx.execute(f"SELECT 1 FROM {pd.qualified(t.schema, t.table)} WHERE {_match_sql(t)} "
                      "LIMIT 1", (_val(t, v),)).fetchone():
            out.append({"table": t.table, "column": t.column, "reason": "unreviewed table"})
    return out


# ── Tables the app itself reads through its own connection ────────────────────
def _own_schema(cx):
    return cx.execute("SELECT current_schema()").fetchone()[0] if _is_pg(cx) else None


def _own_target(cx, table, column):
    """people, client_portals and portal_notify_state as the app reads them: in the
    connection's own schema, never a same-named table in another schema."""
    own = _own_schema(cx)
    for t in pd.targets(cx):
        if t.table == table and t.column == column and t.schema == own:
            return t
    return None


# ── Portal pages ──────────────────────────────────────────────────────────────
def _portal_target(cx):
    return _own_target(cx, "client_portals", "email")


def _portal_plan(cx, s_email, m_email):
    t = _portal_target(cx)
    if not t:
        return None
    rows = _rows(cx, f"SELECT * FROM {pd.qualified(t.schema, t.table)} "
                     "WHERE lower(trim(email)) IN (?,?)", (s_email, m_email))
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
    return {"target": t, "keep": keep, "others": others, "content": content,
            "copied": sorted(copied), "keep_is_merged": _norm(keep["email"]) != s_email}


def _portal_rule(cx, log, merge_id, s_email, m_email):
    plan = _portal_plan(cx, s_email, m_email)
    if not plan:
        return
    t = plan["target"]
    keys = pd.key_columns(cx, t.schema, t.table)
    keep = plan["keep"]
    for o in plan["others"]:
        if o.get("token_hash"):
            pa.add_token_alias(cx, o["token_hash"], s_email, merge_id)
        _set_aside(cx, log, t, o, keys)
    new_json = json.dumps(plan["content"])
    if plan["copied"]:
        where, args = _row_where(cx, t.schema, t.table, keep, keys)
        log.add(t.schema, t.table, _key_of(keep, keys), "content_json",
                keep.get("content_json"), new_json, "rekey")
        cx.execute(f'UPDATE {pd.qualified(t.schema, t.table)} SET content_json=? WHERE {where}',
                   [new_json] + args)
        keep = dict(keep, content_json=new_json)
    if plan["keep_is_merged"]:
        _rekey(cx, log, t, keep, keys, s_email)
        _carry_raw_token(cx, log, s_email, m_email)


def _carry_raw_token(cx, log, s_email, m_email):
    """Re-sent links must carry the kept page's token: give the survivor the merged raw token."""
    t = _own_target(cx, "portal_notify_state", "email")
    if not t:
        return
    q = pd.qualified(t.schema, t.table)
    m = _rows(cx, f"SELECT * FROM {q} WHERE lower(trim(email))=?", (m_email,))
    s = _rows(cx, f"SELECT * FROM {q} WHERE lower(trim(email))=?", (s_email,))
    if m and m[0].get("portal_token") and s:
        keys = pd.key_columns(cx, t.schema, t.table)
        where, args = _row_where(cx, t.schema, t.table, s[0], keys)
        log.add(t.schema, t.table, _key_of(s[0], keys), "portal_token", s[0].get("portal_token"),
                m[0]["portal_token"], "rekey")
        cx.execute(f"UPDATE {q} SET portal_token=? WHERE {where}", [m[0]["portal_token"]] + args)


# ── Preview, apply ───────────────────────────────────────────────────────────
# Paid schedules: a choice could drop a prepaid period, so a clash here is resolved by hand.
NO_CHOICE_TABLES = frozenset({"subscriptions", "coach_subscriptions", "family_subscriptions",
                              "memberships", "coupons", "points_ledger"})


def _choices(resolutions):
    bad = {k: v for k, v in (resolutions or {}).items() if v not in ("survivor", "merged")}
    if bad:
        raise MergeRefused("a choice must be survivor or merged: " + ", ".join(sorted(bad)))
    paid = sorted(k for k in (resolutions or {}) if k in NO_CHOICE_TABLES)
    if paid:
        raise MergeRefused("a clash in " + ", ".join(paid) + " must be resolved by hand, "
                           "not by a choice: a prepaid period could be lost")
    return {k: v for k, v in (resolutions or {}).items() if k not in CLASH_RULES}


def preview(cx, survivor_id, merged_id, resolutions=None):
    """What apply would do. Writes nothing."""
    choices = _choices(resolutions)
    s, m = _person(cx, survivor_id), _person(cx, merged_id)
    if not s or not m:
        raise MergeRefused("both people must exist")
    s_email, m_email = _norm(s["email"]), _norm(m["email"])
    counts, clashes, blocked = {}, {}, []
    for t in _generic_targets(cx):
        old, new = (m_email, s_email) if t.kind == "email" else (merged_id, survivor_id)
        q = pd.qualified(t.schema, t.table)
        n_m = cx.execute(f'SELECT COUNT(*) FROM {q} WHERE {_match_sql(t)}', (_val(t, old),)).fetchone()[0]
        n_s = cx.execute(f'SELECT COUNT(*) FROM {q} WHERE {_match_sql(t)}', (_val(t, new),)).fetchone()[0]
        if n_m or n_s:
            prev = counts.get(t.table, [0, 0])
            counts[t.table] = [max(prev[0], n_m), max(prev[1], n_s)]
        if n_m:
            _, n_clash = _move_target(cx, None, t, old, new, dry=True, blocked=blocked,
                                      choices=choices)
            if n_clash:
                clashes[t.table] = {"count": clashes.get(t.table, {}).get("count", 0) + n_clash,
                                    "rule": CLASH_RULES.get(t.table)}
    blocked.extend(_unreviewed_with_rows(cx, m_email, merged_id))
    plan = _portal_plan(cx, s_email, m_email)
    portal = {"keep": None, "reason": "neither person has a portal page", "from_other": []}
    if plan:
        who = "merged" if plan["keep_is_merged"] else "survivor"
        portal = {"keep": who, "from_other": plan["copied"],
                  "reason": f"the {who}'s page was updated most recently "
                            f"({plan['keep'].get('updated_at') or 'no date'})"}
    return {"survivor": {"id": survivor_id, "email": s_email, "name": s.get("name")},
            "merged": {"id": merged_id, "email": m_email, "name": m.get("name")},
            "counts": counts, "clashes": clashes,
            "blocked": sorted({b["table"] for b in blocked}),
            "needs_choice": sorted({b["table"] for b in blocked if b.get("choice")}),
            "choices": choices, "portal": portal}


def apply(cx, *, survivor_id, merged_id, mail_old, evidence, suggestion, applied_by,
          merge_people_fields, resolutions=None):
    choices = _choices(resolutions)
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
    unreviewed = _unreviewed_with_rows(cx, m_email, merged_id)
    if unreviewed:
        raise MergeBlocked(unreviewed)
    merge_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO person_merges (survivor_person_id, merged_person_id, survivor_email, "
        "merged_email, merged_person_json, survivor_before_json, evidence_json, suggestion_json, "
        "mail_old, applied_by, applied_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (survivor_id, merged_id, s_email, m_email, _dumps(m), _dumps(s), json.dumps(evidence),
         json.dumps(suggestion), mail_old, applied_by, _now()))
    log = _Log(cx, merge_id)
    _portal_rule(cx, log, merge_id, s_email, m_email)
    for t in _generic_targets(cx):
        old, new = (m_email, s_email) if t.kind == "email" else (merged_id, survivor_id)
        _move_target(cx, log, t, old, new, choices=choices)
    merge_people_fields(cx, survivor_id, merged_id)
    cx.execute("UPDATE person_merges SET survivor_after_json=? WHERE id=?",
               (_dumps(_person(cx, survivor_id)), merge_id))
    pa.add_alias(cx, m_email, s_email, merge_id)
    return merge_id


# ── Undo ──────────────────────────────────────────────────────────────────────
def _insert_row(cx, schema, table, row):
    cols = list(row)
    over = ""
    if _is_pg(cx):
        r = cx.execute("SELECT identity_generation FROM information_schema.columns "
                       "WHERE table_schema=? AND table_name=? AND column_name='id'",
                       (schema or "public", table)).fetchone()
        over = " OVERRIDING SYSTEM VALUE" if r and r[0] == "ALWAYS" else ""
    cx.execute(f'INSERT INTO {pd.qualified(schema, table)} '
               f'({", ".join(chr(34) + c + chr(34) for c in cols)}){over} '
               f'VALUES ({", ".join("?" for _ in cols)})', [row[c] for c in cols])


def _people_schema(cx):
    return _own_schema(cx)


def _rekey_still_there(cx, schema, table, key, col, new):
    eq = _eq(cx)
    if "__row__" in key:
        row = key["__row__"]
        match = " AND ".join(f'"{c}" {eq} ?' for c in row)
        return cx.execute(f'SELECT 1 FROM {pd.qualified(schema, table)} WHERE {match} LIMIT 1',
                          list(row.values())).fetchone() is not None
    where = " AND ".join(f'"{k}" {eq} ?' for k in key)
    return cx.execute(f'SELECT 1 FROM {pd.qualified(schema, table)} WHERE {where} AND "{col}" = ? '
                      'LIMIT 1', list(key.values()) + [new]).fetchone() is not None


def _undo_rekey(cx, schema, table, key, col, old, new):
    eq = _eq(cx)
    q = pd.qualified(schema, table)
    if "__row__" in key:
        row = key["__row__"]
        match = " AND ".join(f'"{c}" {eq} ?' for c in row)
        rid = "ctid" if _is_pg(cx) else "rowid"
        cx.execute(f'UPDATE {q} SET "{col}"=? WHERE {rid} = (SELECT {rid} FROM {q} WHERE {match} '
                   'LIMIT 1)', [old] + list(row.values()))
        return
    where = " AND ".join(f'"{k}" {eq} ?' for k in key)
    cx.execute(f'UPDATE {q} SET "{col}"=? WHERE {where}', [old] + list(key.values()))


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
    skipped, restored = [], 0
    # The person first, so rows pointing at them are never orphaned, even for a moment.
    ok, err = _savepoint(cx, lambda: _insert_row(cx, _people_schema(cx), "people",
                                                 _loads(mg["merged_person_json"])))
    if not ok:
        raise MergeRefused(f"could not recreate the merged person ({type(err).__name__})")
    changes = _rows(cx, "SELECT * FROM person_merge_changes WHERE merge_id=? ORDER BY seq DESC",
                    (merge_id,))
    kept, handled, sum_rest, notify_out = [], set(), {}, False
    # A folded cart's items are left where they are once the cart they joined is no longer
    # open: it may have been ordered since (review round 3).
    fold = [ch for ch in changes if _split_name(ch["table_name"])[1] == "cart_items"]
    for ch in fold:
        if ch["action"] not in ("added", "rekey"):
            continue
        schema = _split_name(ch["table_name"])[0]
        token = (_loads(ch["key_json"]) or {}).get("token")
        cur = cx.execute(f"SELECT status FROM {pd.qualified(schema, 'carts')} WHERE token=?",
                         (token,)).fetchone()
        if not cur or cur[0] != "open":
            handled.update(c["id"] for c in fold)
            skipped.append({"table": "cart_items", "key": {"token": token},
                            "reason": "that cart is no longer open, so its items stay"})
            break
    # Consent and money are never reversed blindly (final review round, 2026-09-27).
    for ch in changes:
        schema, table = _split_name(ch["table_name"])
        rule = CLASH_RULES.get(table, "")
        if ch["action"] != "rekey":
            continue
        key = _loads(ch["key_json"])
        where = " AND ".join(f'"{k}" {_eq(cx)} ?' for k in key if k != "__row__")
        if rule == "notify" and ch["column_name"] == "opt_status":
            # A carried text opt-out stays: a STOP sent since cannot be told apart from it.
            handled.add(ch["id"])
            kept.append({"table": table, "key": key, "kept": "text opt-out"})
            if where:
                cur = cx.execute(f'SELECT opt_status FROM {pd.qualified(schema, table)} WHERE {where}',
                                 [v for k, v in key.items() if k != "__row__"]).fetchone()
                notify_out = notify_out or bool(cur and cur[0] == "out")
        elif rule.startswith("sum:") and ch["column_name"] == rule.split(":", 1)[1] and where:
            # Split what is there now; never more than the survivor had, never new money.
            col = ch["column_name"]
            q = pd.qualified(schema, table)
            args = [v for k, v in key.items() if k != "__row__"]
            cur = cx.execute(f'SELECT "{col}" FROM {q} WHERE {where}', args).fetchone()
            now = int((cur[0] if cur else 0) or 0)
            had = int(float(ch["old_value"] or 0))
            back = min(had, now)
            cx.execute(f'UPDATE {q} SET "{col}"=? WHERE {where}', [back] + args)
            sum_rest[table] = (col, now - back)
            handled.add(ch["id"])
            restored += 1
    for ch in changes:
        schema, table = _split_name(ch["table_name"])
        if ch["action"] == "set_aside" and table == "portal_notify_state" and not notify_out:
            s_row = _rows(cx, f"SELECT opt_status FROM {pd.qualified(schema, table)} "
                              "WHERE lower(trim(email))=?", (_norm(mg["survivor_email"]),))
            notify_out = bool(s_row and s_row[0].get("opt_status") == "out")
    # Group by row, so a row's changes are all undone or none are.
    groups, order = {}, []
    for ch in changes:
        if ch["id"] in handled:
            continue
        g = (ch["table_name"], ch["key_json"]) if ch["action"] == "rekey" else ("seq", ch["seq"])
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(ch)
    for g in order:
        chs = groups[g]
        schema, table = _split_name(chs[0]["table_name"])
        key = _loads(chs[0]["key_json"])
        if chs[0]["action"] == "added":
            # A row the merge created (a folded cart item): remove it if it is unchanged.
            row = _loads(chs[0]["row_json"])
            q = pd.qualified(schema, table)
            where = " AND ".join(f'"{k}" {_eq(cx)} ?' for k in row)
            cur = cx.execute(f"DELETE FROM {q} WHERE {where}", [row[k] for k in row])
            if getattr(cur, "rowcount", 1):
                restored += 1
            else:
                skipped.append({"table": table, "key": key, "reason": "changed since the merge"})
            continue
        if chs[0]["action"] == "set_aside":
            row = _loads(chs[0]["row_json"])
            if table in sum_rest and sum_rest[table][0] in row:
                row[sum_rest[table][0]] = sum_rest[table][1]
            if table == "portal_notify_state" and notify_out and "opt_status" in row:
                row["opt_status"] = "out"          # the person is opted out; so is this row
            ok, err = _savepoint(cx, lambda: _insert_row(cx, schema, table, row))
            if ok:
                restored += 1
            else:
                skipped.append({"table": table, "key": key,
                                "reason": f"could not restore ({type(err).__name__})"})
            continue
        if not all(_rekey_still_there(cx, schema, table, _loads(c["key_json"]), c["column_name"],
                                      c["new_value"]) for c in chs):
            skipped.append({"table": table, "key": key, "reason": "changed since the merge"})
            continue

        def run(chs=chs, schema=schema, table=table):
            for c in chs:
                _undo_rekey(cx, schema, table, _loads(c["key_json"]), c["column_name"],
                            c["old_value"], c["new_value"])

        ok, err = _savepoint(cx, run)
        if ok:
            restored += len(chs)
        else:
            skipped.append({"table": table, "key": key,
                            "reason": f"could not restore ({type(err).__name__})"})
    before_s = _loads(mg["survivor_before_json"])
    after_s = _loads(mg["survivor_after_json"])
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
    return {"restored": restored, "skipped": skipped, "kept": kept}


# ── The hourly sweep ─────────────────────────────────────────────────────────
def _last_hop_merge(cx, alias):
    """The merge that owns the final hop of an alias chain: sweep changes are logged there,
    so undoing that merge takes them back (review round 2)."""
    merge_id, addr = None, _norm(alias)
    for _ in range(50):
        row = cx.execute("SELECT canonical_email, merge_id FROM email_aliases WHERE alias_email=?",
                         (addr,)).fetchone()
        if not row:
            break
        addr, merge_id = row[0], row[1]
    return merge_id


def sweep(cx):
    """Move records written since a merge under an old address or the old person number.
    A clash with no rule is left in place and reported. Returns {moved, blocked}."""
    moved, blocked = 0, []
    for alias, _ in pa.all_aliases(cx):
        canonical = pa.canonical_email(cx, alias)
        merge_id = _last_hop_merge(cx, alias)
        log = _Log(cx, merge_id, source="sweep")
        if _portal_plan(cx, canonical, alias) and _rows(
                cx, f"SELECT 1 FROM {pd.qualified(_portal_target(cx).schema, 'client_portals')} "
                    "WHERE lower(trim(email))=? LIMIT 1", (alias,)):
            _portal_rule(cx, log, merge_id, canonical, alias)
            moved += 1
        for t in _generic_targets(cx, kinds=("email",)):
            n, s = _move_target(cx, log, t, alias, canonical, blocked=blocked)
            moved += n + s
        blocked.extend(_unreviewed_with_rows(cx, alias, None, kinds=("email",)))
    for mg in _rows(cx, "SELECT id, merged_person_id, survivor_email FROM person_merges "
                        "WHERE undone_at IS NULL"):
        canon = pa.canonical_email(cx, mg["survivor_email"])
        row = cx.execute("SELECT id FROM people WHERE lower(email)=?", (canon,)).fetchone()
        if not row:
            continue
        log = _Log(cx, _last_hop_merge(cx, mg["survivor_email"]) or mg["id"], source="sweep")
        for t in _generic_targets(cx, kinds=("person",)):
            n, s = _move_target(cx, log, t, mg["merged_person_id"], row[0], blocked=blocked)
            moved += n + s
    return {"moved": moved, "blocked": blocked}
