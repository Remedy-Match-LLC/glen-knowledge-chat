"""Add to plan: the writes (Glen, 2026-11-07). The decisions are made in
biofield_add_to_plan, which is pure; this module carries them out.

  land()       writes one click's classification: each joined layer's codes and tail
               go onto the layer it joins, every added or related layer becomes rows
               numbered after the chain, and each related layer is stored as a
               proposal.
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

from dashboard.biofield_add_to_plan import codes_of, merged_tail

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
                            into_head=j.get("into_head") or "", codes=j.get("codes") or [])
                       for j in got["joined"]],
            "related": [dict(lay(r["layer"]), into=r["into"], into_head=r["into_head"],
                             into_remedies=r["into_remedies"], rule=r["rule"], why=r["why"])
                        for r in got["related"]],
            "add": [lay(L) for L in got["add"]]}


# -- writes --------------------------------------------------------------------------

def _set_codes(cx, tid, rid, codes):
    cx.execute("UPDATE biofield_auth_chain SET codes=? WHERE test_id=? AND id=?",
               (json.dumps(codes), _num(tid), rid))


def land(cx, tid, got, write_layers, laser_row=None):
    """Write one classification. `write_layers(layers)` writes the added and related
    layers, already numbered, as rows (import_layers_to_test). `laser_row(layer_no,
    head, tail, codes, laser)` adds a Harmony Laser row onto a joined layer.

    Returns {"added", "joined", "proposals": [id]}. Commits."""
    ensure_schema(cx)
    t = _num(tid)
    for j in got["joined"]:
        if j.get("into_new") is not None:
            continue                  # merged into a new layer of this same batch
        row = cx.execute("SELECT codes,layer,head FROM biofield_auth_chain "
                         "WHERE test_id=? AND id=?", (t, j["into_rid"])).fetchone()
        if not row:
            continue
        have = codes_of(row[0])
        more = [c for c in j.get("codes") or [] if c not in have]
        if more:
            _set_codes(cx, tid, j["into_rid"], have + more)
        if j.get("tail") is not None:
            marks = ",".join("?" * len(j["into_rids"]))
            cx.execute(f"UPDATE biofield_auth_chain SET most_affected=? "
                       f"WHERE test_id=? AND id IN ({marks})",
                       [j["tail"], t] + list(j["into_rids"]))
        if j.get("laser") and laser_row:
            laser_row(row[1], row[2], j.get("tail") or "", have + more, j["laser"])
    before = cx.execute("SELECT COALESCE(MAX(id),0) FROM biofield_auth_chain WHERE test_id=?",
                        (t,)).fetchone()[0]
    layers = sorted(got["add"] + [r["layer"] for r in got["related"]],
                    key=lambda L: L.get("n") or 0)
    added = write_layers(layers) if layers else 0
    ids = []
    for r in got["related"]:
        rids = [x[0] for x in cx.execute(
            "SELECT id FROM biofield_auth_chain WHERE test_id=? AND id>? AND layer=? "
            "ORDER BY id", (t, before, r["n"]))]
        if rids:
            ids.append(_insert(cx, tid, rids, r["into_rids"], r["layer"].get("title") or
                               r["layer"].get("head") or "", r["into_head"],
                               r["rule"], r["why"]))
    cx.commit()
    return {"added": added, "joined": sum(1 for j in got["joined"]
                                          if j.get("into_new") is None),
            "proposals": ids}


def _insert(cx, tid, source_rids, into_rids, source_head, into_head, rule, why):
    cur = cx.execute(
        "INSERT INTO biofield_add_to_plan_proposal (test_id,source_rids,into_rids,"
        "source_head,into_head,rule,why,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (_num(tid), json.dumps([int(r) for r in source_rids]),
         json.dumps([int(r) for r in into_rids]), source_head or "", into_head or "",
         rule or "", why or "", "pending", _now()))
    return cur.lastrowid


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
    stored rows still on the chain; a remedy added to it since comes along."""
    from dashboard.biofield_add_to_plan import groups_of
    src, into = json.loads(p["source_rids"]), json.loads(p["into_rids"])
    card = {}
    for g in groups_of(chain_rows(cx, tid)):
        for r in g["rows"]:
            card[int(r["id"])] = g
    s = next((card[r] for r in src if r in card), None)
    i = next((card[r] for r in into if r in card), None)
    if s is None or i is None or s is i:
        return None
    return [int(r["id"]) for r in s["rows"]], [int(r["id"]) for r in i["rows"]]


def _rows(cx, tid, status=None, pid=None):
    ensure_schema(cx)
    q = ("SELECT id,source_rids,into_rids,source_head,into_head,rule,why,status "
         "FROM biofield_add_to_plan_proposal WHERE test_id=?")
    args = [_num(tid)]
    if status:
        q += " AND status=?"
        args.append(status)
    if pid is not None:
        q += " AND id=?"
        args.append(int(pid))
    keys = ("id", "source_rids", "into_rids", "source_head", "into_head", "rule", "why",
            "status")
    return [dict(zip(keys, r)) for r in cx.execute(q + " ORDER BY id", args)]


def pending(cx, tid):
    """The proposals waiting for Glen. One whose layers have changed since (a row
    deleted, or the two already joined by hand) is closed as gone, never shown."""
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
            [_num(tid)] + list(ids))]
        out.append({"id": p["id"], "source_head": p["source_head"],
                    "into_head": p["into_head"], "rule": p["rule"], "why": p["why"],
                    "source_rids": src, "into_rids": into,
                    "source_remedies": rem(src), "into_remedies": rem(into)})
    if gone:
        marks = ",".join("?" * len(gone))
        cx.execute(f"UPDATE biofield_add_to_plan_proposal SET status='gone',resolved_at=? "
                   f"WHERE id IN ({marks})", [_now()] + gone)
        cx.commit()
    return out


class Gone(ValueError):
    """The proposal's layers changed since it was made."""


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


def resolve(cx, tid, pid, choice):
    """Carry out Glen's choice on proposal `pid`. Raises Gone when its layers changed
    and ValueError for an unknown choice or proposal. Returns {"choice", "layer"}.

    Joining moves the new layer onto the existing one with the layer consolidation
    Glen already uses (biofield_stress.consolidate_layer_balances): the existing
    Head stays, the new layer's Head and Tail join the Tail, and its Balances move.
    Then "existing" removes the new layer's remedy rows and "new" removes the
    existing ones, each after moving the removed rows' codes and Balances onto the
    rows that stay."""
    if choice not in CHOICES:
        raise ValueError("Pick one of: " + ", ".join(CHOICES))
    rows = _rows(cx, tid, pid=pid)
    if not rows:
        raise ValueError("No such proposal")
    p = rows[0]
    if p["status"] != "pending":
        raise Gone("That proposal was already answered. Reload the page.")
    ok = _check(cx, tid, p)
    if ok is None:
        cx.execute("UPDATE biofield_add_to_plan_proposal SET status='gone',resolved_at=? "
                   "WHERE id=?", (_now(), p["id"]))
        cx.commit()
        raise Gone("Those layers changed since this was proposed. Reload the page.")
    src, into = ok
    if choice != "separate":
        from dashboard.biofield_stress import consolidate_layer_balances
        t = _num(tid)
        layer_of = lambda rid: cx.execute(
            "SELECT layer FROM biofield_auth_chain WHERE test_id=? AND id=?",
            (t, rid)).fetchone()[0]
        consolidate_layer_balances(cx, tid, layer_of(src[0]), layer_of(into[0]),
                                   source_rids=src, target_rids=into)
        # The consolidation folds a remedy both layers share, so re-read who is left.
        s, i = list(_live(cx, tid, src)), list(_live(cx, tid, into))
        if choice == "existing":
            _drop_rows(cx, tid, s, i)
        elif choice == "new":
            _drop_rows(cx, tid, i, s)
    cx.execute("UPDATE biofield_add_to_plan_proposal SET status=?,resolved_at=? WHERE id=?",
               (choice, _now(), p["id"]))
    cx.commit()
    return {"choice": choice, "into_head": p["into_head"]}
