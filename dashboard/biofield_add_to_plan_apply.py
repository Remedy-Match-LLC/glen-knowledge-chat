"""Add to plan: the writes (Glen, 2026-11-07). The decisions are made in
biofield_add_to_plan, which is pure; this module carries them out.

  land()       writes one click's classification: each joined layer's codes and tail
               go onto the layer it joins and every added layer becomes rows
               numbered after the chain. Each related layer is held: stored with
               its proposal and never written to the chain until Glen picks (Glen,
               2026-11-07: "yes, hold them in the list").
  hold_rows()  holds a Mind layer the same way, after Update Clinical wrote it.
  propose()    stores proposals for layers that landed some other way (a live layer,
               or a Mind layer from Update Clinical).
  pending()    the proposals still waiting for Glen, checked against the chain now.
  resolve()    Glen's choice on one proposal: keep separate, or join keeping the
               existing remedy, the new one, or both. Nothing related joins before.

The preview store holds what one Body "Add to plan" click previewed, so the confirm
writes exactly that: the reveal synthesis is a model call and differs run to run.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from dashboard.biofield_add_to_plan import codes_of

CHOICES = ("separate", "existing", "new", "both")
PREVIEW_TTL = timedelta(hours=1)


def _num(tid):
    return int(str(tid).lstrip("a") or 0)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_add_to_plan_proposal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL,
        source_rids TEXT NOT NULL, into_rids TEXT NOT NULL,
        source_head TEXT NOT NULL DEFAULT '', into_head TEXT NOT NULL DEFAULT '',
        rule TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        resolved_at TEXT)""")
    cols = {r[1] for r in cx.execute("PRAGMA table_info(biofield_add_to_plan_proposal)")}
    if "held" not in cols:
        # A held layer is not on the chain: its rows wait here until Glen picks.
        cx.execute("ALTER TABLE biofield_add_to_plan_proposal ADD COLUMN held TEXT")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_add_to_plan_preview (
        test_id INTEGER PRIMARY KEY, token TEXT NOT NULL, body TEXT NOT NULL,
        created_at TEXT NOT NULL)""")


def chain_rows(cx, tid):
    """Every chain row with the fields the classifier reads, in chain order."""
    cols = {r[1] for r in cx.execute("PRAGMA table_info(biofield_auth_chain)")}
    codes = "codes" if "codes" in cols else "'[]'"
    return [{"id": r[0], "layer": r[1], "head": r[2] or "", "most_affected": r[3] or "",
             "remedy": r[4] or "", "codes": codes_of(r[5]), "origin": r[6] or "",
             "confirmed": r[7]}
            for r in cx.execute(
                f"SELECT id,layer,head,most_affected,remedy,{codes},origin,confirmed "
                "FROM biofield_auth_chain WHERE test_id=? ORDER BY layer,sort_seq,id",
                (_num(tid),))]


def digest(rows):
    return hashlib.sha256(json.dumps(rows or [], sort_keys=True, default=str)
                          .encode()).hexdigest()


# -- preview -------------------------------------------------------------------------

def save_preview(cx, tid, body):
    ensure_schema(cx)
    token = secrets.token_urlsafe(16)
    cx.execute("DELETE FROM biofield_add_to_plan_preview WHERE test_id=?", (_num(tid),))
    cx.execute("INSERT INTO biofield_add_to_plan_preview (test_id,token,body,created_at) "
               "VALUES (?,?,?,?)", (_num(tid), token, json.dumps(body), _now()))
    cx.commit()
    return token


def load_preview(cx, tid, token, now=None):
    ensure_schema(cx)
    row = cx.execute("SELECT token,body,created_at FROM biofield_add_to_plan_preview "
                     "WHERE test_id=?", (_num(tid),)).fetchone()
    if not row or not token or not secrets.compare_digest(row[0], str(token)):
        return None
    if (now or datetime.now(timezone.utc)) - datetime.fromisoformat(row[2]) > PREVIEW_TTL:
        return None
    return json.loads(row[1])


def consume_preview(cx, tid, token):
    """True for exactly one caller. Not committed here: it commits with the write."""
    ensure_schema(cx)
    return cx.execute("DELETE FROM biofield_add_to_plan_preview WHERE test_id=? AND token=?",
                      (_num(tid), str(token))).rowcount == 1


def describe(got):
    """What the page lists for one click, before or after it is written."""
    def lay(L):
        from dashboard.biofield_add_to_plan import incoming_fields
        f = incoming_fields(L)
        return {"n": L.get("n"), "title": f["head"], "remedy": f["remedy"]}
    return {"joined": [dict(lay(j["layer"]), into=j.get("into", j.get("into_new")),
                            into_head=j.get("into_head") or "", codes=j.get("codes") or [],
                            stresses=j.get("stresses") or [])
                       for j in got["joined"]],
            "related": [dict(lay(r["layer"]), into=r["into"], into_head=r["into_head"],
                             into_remedies=r["into_remedies"], rule=r["rule"], why=r["why"])
                        for r in got["related"]],
            "add": [lay(L) for L in got["add"]]}


# -- writes --------------------------------------------------------------------------

class Changed(ValueError):
    """The plan changed between the read a write was planned on and the write."""


def prepare(cx):
    """Create every table a write here touches. Each of these commits, so it runs
    before a write transaction opens, never inside one."""
    from dashboard.biofield_authoring import init_auth_tables
    from dashboard.biofield_stress import init_stress_tables
    init_auth_tables(cx)
    init_stress_tables(cx)
    ensure_schema(cx)


def begin(cx):
    """Open the write transaction now, so the re-read that guards it and the write
    are one unit. SQLite: BEGIN IMMEDIATE takes the write lock before the re-read.
    Postgres opens its transaction on the first statement."""
    from dashboard import db
    if db.backend_of(cx) != "sqlite":
        return
    if cx.in_transaction:
        cx.commit()           # a caller's earlier, unrelated work: not part of this write
    cx.execute("BEGIN IMMEDIATE")


def _set_codes(cx, tid, rid, codes):
    cx.execute("UPDATE biofield_auth_chain SET codes=? WHERE test_id=? AND id=?",
               (json.dumps(codes), _num(tid), rid))


def confirm(cx, tid, got, write_layers, laser_row=None, *, chain, token=None):
    """Write one click's classification as one transaction: the chain must still have
    digest `chain`, the preview `token` (when given) is used up, and land() writes.
    Any failure rolls back all three, so the token survives and a retry can run.
    Raises Changed when the plan moved since it was read, or the token was used.

    `write_layers` and `laser_row` must not commit (import_layers_to_test with
    commit=False, add_chain_row with commit=False)."""
    prepare(cx)
    begin(cx)
    try:
        if digest(chain_rows(cx, tid)) != chain:
            raise Changed("The plan changed since it was read.")
        if token is not None and not consume_preview(cx, tid, token):
            raise Changed("That preview was already added.")
        done = land(cx, tid, got, write_layers, laser_row, commit=False)
        cx.commit()
    except BaseException:
        cx.rollback()
        raise
    return done


def _laser_key():
    from dashboard.biofield_add_to_plan import HARMONY_LASER
    from dashboard.biofield_chain_fold import _remedy_key
    return _remedy_key, _remedy_key(HARMONY_LASER)


def land(cx, tid, got, write_layers, laser_row=None, commit=True):
    """Write one classification. `write_layers(layers)` writes the added and related
    layers, already numbered, as rows (import_layers_to_test). `laser_row(layer_no,
    head, tail, codes, laser)` adds a Harmony Laser row onto a joined layer.

    A join never changes the kept layer's tail: what the joining layer brings goes
    to the kept layer's Balances (Glen, 2026-11-07), as stresses linked to its rows.
    A layer is given at most one laser.

    Returns {"added", "joined", "proposals": [id]}. Commits unless `commit` is False;
    confirm() runs it inside its own transaction."""
    from dashboard.biofield_clinical_head_term import card_rids, link_wording
    if commit:
        prepare(cx)
    t = _num(tid)
    key, laser_k = _laser_key()
    lasered = set()
    for j in got["joined"]:
        if j.get("into_batch") or j.get("into_new") is not None:
            continue                  # merged into a new or held layer of this same batch
        row = cx.execute("SELECT codes,layer,head,most_affected FROM biofield_auth_chain "
                         "WHERE test_id=? AND id=?", (t, j["into_rid"])).fetchone()
        if not row:
            continue
        have = codes_of(row[0])
        more = [c for c in j.get("codes") or [] if c not in have]
        if more:
            _set_codes(cx, tid, j["into_rid"], have + more)
        if j.get("laser") and laser_row and row[1] not in lasered and not any(
                key(r[0]) == laser_k for r in cx.execute(
                    "SELECT remedy FROM biofield_auth_chain WHERE test_id=? AND layer=?",
                    (t, row[1]))):
            lasered.add(row[1])
            laser_row(row[1], row[2], row[3] or "", have + more, j["laser"])
        link_wording(cx, tid, j.get("stresses") or [],
                     card_rids(cx, tid, row[2]) or j["into_rids"])
    before = cx.execute("SELECT COALESCE(MAX(id),0) FROM biofield_auth_chain WHERE test_id=?",
                        (t,)).fetchone()[0]
    layers = sorted(got["add"], key=lambda L: L.get("n") or 0)
    added = write_layers(layers) if layers else 0
    # A repeat inside this click joined a new layer: its stresses go to that layer.
    for L in layers:
        if L.get("stresses"):
            rids = [r[0] for r in cx.execute(
                "SELECT id FROM biofield_auth_chain WHERE test_id=? AND id>? AND layer=?",
                (t, before, L.get("n")))]
            link_wording(cx, tid, L["stresses"], rids)
    ids = []
    for r in got["related"]:
        from dashboard.biofield_add_to_plan import incoming_fields
        # The held layer keeps what joined it in this click: its merged codes and
        # its "stresses", which _write_held links when Glen picks.
        layer = {k: v for k, v in r["layer"].items() if k != "n"}
        ids.append(_insert(cx, tid, [], r["into_rids"], incoming_fields(layer)["head"],
                           r["into_head"], r["rule"], r["why"],
                           held={"kind": "layer", "layer": layer}))
    if commit:
        cx.commit()
    return {"added": added, "joined": sum(1 for j in got["joined"]
                                          if j.get("into_new") is None
                                          and not j.get("into_batch")),
            "proposals": ids}


def _norm(text):
    return " ".join(str(text or "").lower().split())


def _fingerprint(source_rids, into_rids, source_head, held):
    """What makes two pending proposals the same: the test (the caller's filter),
    the head, the remedy, the stresses and what they would join."""
    if held is None:
        return ("rows", tuple(sorted(int(r) for r in source_rids)),
                tuple(sorted(int(r) for r in into_rids)))
    stresses = []
    if held.get("kind") == "layer":
        stresses = (held.get("layer") or {}).get("stresses") or []
    else:
        stresses = held.get("stress_ids") or []
    return ("held", _norm(source_head),
            tuple(sorted(_norm(r) for r in _held_remedies(held))),
            tuple(sorted(_norm(x) for x in stresses)),
            tuple(sorted(int(r) for r in into_rids)))


def _insert(cx, tid, source_rids, into_rids, source_head, into_head, rule, why, held=None):
    """Store one proposal and return its id. An identical one still pending (a retry
    after a lost response) is reused, never stored twice. Never commits."""
    from dashboard import dbwrite
    want = _fingerprint(source_rids, into_rids, source_head, held)
    for q in _rows(cx, tid, status="pending"):
        if _fingerprint(json.loads(q["source_rids"]), json.loads(q["into_rids"]),
                        q["source_head"], q["held"]) == want:
            return q["id"]
    return dbwrite.insert_returning_id(
        cx,
        "INSERT INTO biofield_add_to_plan_proposal (test_id,source_rids,into_rids,"
        "source_head,into_head,rule,why,status,created_at,held) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_num(tid), json.dumps([int(r) for r in source_rids]),
         json.dumps([int(r) for r in into_rids]), source_head or "", into_head or "",
         rule or "", why or "", "pending", _now(),
         json.dumps(held) if held is not None else None))


_HOLD_SKIP = ("id", "test_id", "layer")


def hold_rows(cx, tid, proposals):
    """Hold Mind layers that Update Clinical has just written: each proposal's source
    rows are copied into the proposal, with their Balances, and taken off the chain.
    The proposal is stored before its rows are deleted, all in one transaction, so a
    failure leaves the layer on the chain and raises. Returns the proposal ids."""
    if not proposals:
        return []
    prepare(cx)
    t = _num(tid)
    ids = []
    begin(cx)
    try:
        for p in proposals:
            rids = [int(r) for r in p["source_rids"]]
            marks = ",".join("?" * len(rids))
            cur = cx.execute(f"SELECT * FROM biofield_auth_chain WHERE test_id=? "
                             f"AND id IN ({marks}) ORDER BY id", [t] + rids)
            cols = [d[0] for d in cur.description]
            rows = [{c: v for c, v in zip(cols, r) if c not in _HOLD_SKIP}
                    for r in cur.fetchall()]
            if not rows:
                raise Changed("The layer to hold is no longer on the plan.")
            links = sorted({r[0] for r in cx.execute(
                f"SELECT stress_id FROM biofield_auth_layer_stress WHERE test_id=? "
                f"AND chain_rid IN ({marks})", [t] + rids)})
            ids.append(_insert(cx, tid, [], p["into_rids"], p["source_head"],
                               p["into_head"], p["rule"], p["why"],
                               held={"kind": "rows", "rows": rows, "stress_ids": links}))
            cx.execute(f"DELETE FROM biofield_auth_layer_stress WHERE test_id=? "
                       f"AND chain_rid IN ({marks})", [t] + rids)
            cx.execute(f"DELETE FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks})",
                       [t] + rids)
        cx.commit()
    except BaseException:
        cx.rollback()
        raise
    return ids


def _held_remedies(held):
    if held.get("kind") == "rows":
        return [r.get("remedy") for r in held.get("rows") or [] if (r.get("remedy") or "").strip()]
    from dashboard.biofield_add_to_plan import incoming_fields
    name = incoming_fields(held.get("layer") or {})["remedy"]
    return [name] if name else []


def _write_held(cx, tid, held, write_layer, drop_laser=False):
    """Put a held layer on the chain, after the last layer. Returns its new row ids.
    Its Balances come with it: a Mind layer's stored links, and a Body layer's
    "stresses" (what joined it in the click that held it). `drop_laser` writes a Body
    layer without its laser. Never commits; `write_layer` must not either."""
    from dashboard.biofield_clinical_head_term import link_wording
    t = _num(tid)
    before = cx.execute("SELECT COALESCE(MAX(id),0) FROM biofield_auth_chain WHERE test_id=?",
                        (t,)).fetchone()[0]
    last = cx.execute("SELECT COALESCE(MAX(layer),0) FROM biofield_auth_chain WHERE test_id=?",
                      (t,)).fetchone()[0] or 0
    layer = dict(held.get("layer") or {})
    if held.get("kind") == "rows":
        for r in held.get("rows") or []:
            cols = ["test_id", "layer"] + list(r)
            cx.execute(f"INSERT INTO biofield_auth_chain ({','.join(cols)}) "
                       f"VALUES ({','.join('?' * len(cols))})", [t, last + 1] + list(r.values()))
    else:
        if write_layer is None:
            raise ValueError("No writer for a held layer")
        if drop_laser:
            layer["laser"] = None
        write_layer(dict(layer, n=last + 1))
    rids = [r[0] for r in cx.execute(
        "SELECT id FROM biofield_auth_chain WHERE test_id=? AND id>? ORDER BY id", (t, before))]
    for sid in held.get("stress_ids") or []:
        for rid in rids:
            cx.execute("INSERT OR IGNORE INTO biofield_auth_layer_stress "
                       "(test_id,stress_id,chain_rid) VALUES(?,?,?)", (t, sid, rid))
    if layer.get("stresses"):
        link_wording(cx, tid, layer["stresses"], rids)
    return rids


def propose(cx, tid, proposals):
    """Store proposals from related_after_landing. Returns their ids. Commits."""
    ensure_schema(cx)
    ids = [_insert(cx, tid, p["source_rids"], p["into_rids"], p["source_head"],
                   p["into_head"], p["rule"], p["why"]) for p in proposals or []]
    cx.commit()
    return ids


def _live(cx, tid, rids):
    """The rows of `rids` still on the chain, as {id: head}."""
    if not rids:
        return {}
    marks = ",".join("?" * len(rids))
    return {r[0]: (r[1] or "").strip() for r in cx.execute(
        f"SELECT id,head FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks})",
        [_num(tid)] + list(rids))}


def _check(cx, tid, p):
    """(source card rids, into card rids) as the two layers stand now, or None when
    either layer is gone or they are already one. A layer is found by any of its
    stored rows still on the chain; a remedy added to it since comes along.

    A held layer is off the chain and lives only in its proposal, so it is never
    closed: when the layer it would join is gone, into is [] and Glen can still keep
    it separate."""
    from dashboard.biofield_add_to_plan import groups_of
    src, into = json.loads(p["source_rids"]), json.loads(p["into_rids"])
    card = {}
    for g in groups_of(chain_rows(cx, tid)):
        for r in g["rows"]:
            card[int(r["id"])] = g
    i = next((card[r] for r in into if r in card), None)
    if p.get("held"):
        return None, ([int(r["id"]) for r in i["rows"]] if i is not None else [])
    s = next((card[r] for r in src if r in card), None)
    if s is None or i is None or s is i:
        return None
    return [int(r["id"]) for r in s["rows"]], [int(r["id"]) for r in i["rows"]]


def _rows(cx, tid, status=None, pid=None):
    ensure_schema(cx)
    q = ("SELECT id,source_rids,into_rids,source_head,into_head,rule,why,status,held "
         "FROM biofield_add_to_plan_proposal WHERE test_id=?")
    args = [_num(tid)]
    if status:
        q += " AND status=?"
        args.append(status)
    if pid is not None:
        q += " AND id=?"
        args.append(int(pid))
    keys = ("id", "source_rids", "into_rids", "source_head", "into_head", "rule", "why",
            "status", "held")
    out = [dict(zip(keys, r)) for r in cx.execute(q + " ORDER BY id", args)]
    for p in out:
        p["held"] = json.loads(p["held"]) if p["held"] else None
    return out


def pending(cx, tid):
    """The proposals waiting for Glen. One whose layers have changed since (a row
    deleted, or the two already joined by hand) is closed as gone, never shown. A
    held layer is never closed: with the layer it would join gone ("into_gone") it
    can still be kept separate."""
    out, gone = [], []
    for p in _rows(cx, tid, status="pending"):
        ok = _check(cx, tid, p)
        if ok is None:
            gone.append(p["id"])
            continue
        src, into = ok
        rem = lambda ids: [r[0] for r in cx.execute(
            f"SELECT remedy FROM biofield_auth_chain WHERE test_id=? AND id IN "
            f"({','.join('?' * len(ids))}) AND TRIM(COALESCE(remedy,''))<>'' ORDER BY id",
            [_num(tid)] + list(ids))] if ids else []
        out.append({"id": p["id"], "source_head": p["source_head"],
                    "into_head": p["into_head"], "rule": p["rule"], "why": p["why"],
                    "source_rids": src or [], "into_rids": into, "held": bool(p["held"]),
                    "into_gone": not into,
                    "source_remedies": (_held_remedies(p["held"]) if p["held"]
                                        else rem(src)),
                    "into_remedies": rem(into)})
    if gone:
        marks = ",".join("?" * len(gone))
        cx.execute(f"UPDATE biofield_add_to_plan_proposal SET status='gone',resolved_at=? "
                   f"WHERE id IN ({marks})", [_now()] + gone)
        cx.commit()
    return out


class Gone(ValueError):
    """The proposal's layers changed since it was made, or it was already answered."""


def _drop_rows(cx, tid, drop, keep):
    """Delete rows `drop` from a joined layer, after putting their codes on the first
    of `keep` and their Balances on `keep`. Never commits."""
    from dashboard.biofield_stress import move_links_off
    t = _num(tid)
    if not drop or not keep:
        return
    marks = ",".join("?" * len(drop))
    gone_codes = [c for r in cx.execute(
        f"SELECT codes FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks}) "
        f"ORDER BY id", [t] + list(drop)) for c in codes_of(r[0])]
    first = cx.execute("SELECT codes FROM biofield_auth_chain WHERE test_id=? AND id=?",
                       (t, keep[0])).fetchone()
    have = codes_of(first[0] if first else None)
    more = [c for c in dict.fromkeys(gone_codes) if c not in have]
    if more:
        _set_codes(cx, tid, keep[0], have + more)
    move_links_off(cx, tid, drop, to_rids=keep)
    cx.execute(f"DELETE FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks})",
               [t] + list(drop))


def _remedies(cx, tid, rids):
    if not rids:
        return {}
    marks = ",".join("?" * len(rids))
    return {r[0]: r[1] or "" for r in cx.execute(
        f"SELECT id,remedy FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks})",
        [_num(tid)] + list(rids))}


def _head_codes(cx, tid, rids):
    """The first code of these rows in chain order: the layer's head (Glen, 2026-11-10)."""
    want = {int(r) for r in rids or []}
    for r in chain_rows(cx, tid):
        if int(r["id"]) in want and r["codes"]:
            return r["codes"][:1]
    return []


def resolve(cx, tid, pid, choice, write_layer=None):
    """Carry out Glen's choice on proposal `pid`. Raises Gone when its layers changed
    or it was already answered, and ValueError for an unknown choice or proposal.
    Returns {"choice", "into_head"}.

    The proposal is claimed first (pending -> the choice, in the same transaction as
    the writes), so two answers to one proposal cannot both run. Any failure rolls
    the claim and the writes back together. `write_layer` must not commit.

    Joining moves the new layer onto the existing one with the layer consolidation
    Glen already uses (biofield_stress.consolidate_layer_balances): the existing
    Head stays, the new layer's Head and Tail join the Tail, and its Balances move.
    Then "existing" removes the new layer's remedy rows and "new" removes the
    existing ones, each after moving the removed rows' codes and Balances onto the
    rows that stay. A Harmony Laser the new layer brings stays only when the joined
    layer's head allows it (laser_blocked_by_head) and the layer has no laser left.

    A held layer is first put on the chain after the last layer, by `write_layer`
    for a Body layer (import_layers_to_test) or from its stored rows for a Mind
    layer. "separate" stops there; a join then merges it as above."""
    from dashboard.biofield_add_to_plan import laser_blocked_by_head
    if choice not in CHOICES:
        raise ValueError("Pick one of: " + ", ".join(CHOICES))
    prepare(cx)
    rows = _rows(cx, tid, pid=pid)
    if not rows:
        raise ValueError("No such proposal")
    p = rows[0]
    t = _num(tid)
    begin(cx)
    try:
        claimed = cx.execute(
            "UPDATE biofield_add_to_plan_proposal SET status=?,resolved_at=? "
            "WHERE id=? AND test_id=? AND status='pending'",
            (choice, _now(), p["id"], t)).rowcount
        if claimed != 1:
            raise Gone("That proposal was already answered. Reload the page.")
        ok = _check(cx, tid, p)
        if ok is None:
            cx.execute("UPDATE biofield_add_to_plan_proposal SET status='gone',"
                       "resolved_at=? WHERE id=?", (_now(), p["id"]))
            cx.commit()
            raise Gone("Those layers changed since this was proposed. Reload the page.")
        src, into = ok
        if not into and choice != "separate":
            raise Gone("The layer it would join was deleted. It can still be kept "
                       "separate.")
        key, laser_k = _laser_key()
        head = (_head_codes(cx, tid, into) or _head_codes(cx, tid, src or [])) if into else []
        if p["held"]:
            held = p["held"]
            # A Body layer's laser is not written where the join could not keep it.
            drop = choice != "separate" and (laser_blocked_by_head(
                head or codes_of((held.get("layer") or {}).get("codes"))[:1]) or (
                choice == "both" and any(key(v) == laser_k
                                         for v in _remedies(cx, tid, into).values())))
            src = _write_held(cx, tid, held, write_layer, drop_laser=drop)
            if not src:
                raise ValueError("The held layer wrote no rows")
        if choice != "separate":
            from dashboard.biofield_stress import consolidate_layer_balances
            if not head:
                head = _head_codes(cx, tid, src)
            layer_of = lambda rid: cx.execute(
                "SELECT layer FROM biofield_auth_chain WHERE test_id=? AND id=?",
                (t, rid)).fetchone()[0]
            # The kept layer's tail stays its one item (Glen, 2026-11-07). The joining
            # layer's head and tail become Balances: the consolidation links them.
            kept_tail = next((r[0] for r in cx.execute(
                f"SELECT most_affected FROM biofield_auth_chain WHERE test_id=? AND id IN "
                f"({','.join('?' * len(into))}) ORDER BY layer,sort_seq,id",
                [t] + list(into)) if (r[0] or "").strip()), "")
            consolidate_layer_balances(cx, tid, layer_of(src[0]), layer_of(into[0]),
                                       source_rids=src, target_rids=into)
            left = list(_live(cx, tid, src)) + list(_live(cx, tid, into))
            cx.execute(f"UPDATE biofield_auth_chain SET most_affected=? WHERE test_id=? "
                       f"AND id IN ({','.join('?' * len(left))})", [kept_tail, t] + left)
            # The consolidation folds a remedy both layers share, so re-read who is left.
            s, i = list(_live(cx, tid, src)), list(_live(cx, tid, into))
            if choice == "existing":
                _drop_rows(cx, tid, s, i)
            elif choice == "new":
                _drop_rows(cx, tid, i, s)
            # At most one laser, and none under a blocked head: a laser the new layer
            # brought goes when the joined layer cannot keep it.
            s, i = list(_live(cx, tid, src)), list(_live(cx, tid, into))
            rem = _remedies(cx, tid, s + i)
            new_lasers = [r for r in s if key(rem.get(r)) == laser_k]
            if new_lasers and (laser_blocked_by_head(head) or
                               any(key(rem.get(r)) == laser_k for r in i)):
                _drop_rows(cx, tid, new_lasers,
                           [r for r in i + s if r not in new_lasers])
        cx.commit()
    except BaseException:
        cx.rollback()
        raise
    return {"choice": choice, "into_head": p["into_head"]}
