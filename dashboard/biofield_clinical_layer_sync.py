"""Bring the causal chain's Clinical layers up to date with the Clinical Summary.

Glen, 2026-10-18: "I don't see how to update the current layers from the clinical
section." "Clinical layers -> propose" only appends, so running it again duplicated
every layer. This compares instead.

His rulings, 2026-10-19, in the platform tab:
  - A layer entered by hand is updated too when its name matches a ticked row
    ("yes"). Josie Gagnon's 4 layers were typed in, none made by the old button.
    A layer holding a scan or program row is never touched.
  - A topic row with no stress pattern matches and names a layer by its topic name:
    its key, its label or its display name ("yes"). A free-text row with no pattern
    is still skipped, as clinical_layers skips it.

What the preview offers, each as a change Glen ticks:
  add      a newly ticked remedy, onto the layer that matches its row. Pre-ticked
           on a draft layer; left unticked on a confirmed one.
  remove   a remedy on a matched layer that its rows no longer tick. Never
           pre-ticked.
  new      a layer for a ticked row that has none. Pre-ticked. Draft, origin
           "clinical", doses from remedy_dosing.
  stale    a layer whose rows are no longer ticked. Shown, never changed.

A remedy lands once in the chain (Glen, 2026-09-23): one already on another layer
is reported, not added.

Nothing here reads a request or writes outside apply(). plan() is pure over the
database; apply() re-plans inside the write lock and refuses when the digest the
preview showed no longer matches.
"""
import hashlib
import json

from dashboard.biofield_clinical_checklist import _norm, display_labels
from dashboard.biofield_clinical_sections import row_ticks

# A layer holding one of these rows came from a scan or a program: never changed.
FIXED_ORIGINS = ("scan", "program")


class Stale(ValueError):
    """The chain or the Clinical Summary changed since the preview."""


def _tid(test_id):
    return int(str(test_id).lstrip("a") or 0)


def _topics(cx):
    """Condition keys that are Clinical Summary topics: they hold section data."""
    try:
        return {r[0] for r in cx.execute(
            "SELECT DISTINCT item_key FROM biofield_clinical_section_item")}
    except Exception:  # table absent on a fresh database
        return set()


def _remedy_key(cx, name, resolve):
    name = str(name or "").strip()
    return _norm(resolve(cx, name) or name) if name else ""


def chain_layers(cx, test_id):
    """The chain grouped as the editor groups it: rows sharing a head are one layer.
    [{head, key, layer, rows: [{id, remedy, confirmed, origin}], confirmed, fixed}]."""
    rows = cx.execute(
        "SELECT id,layer,head,most_affected,remedy,confirmed,origin,dosage,frequency,timing "
        "FROM biofield_auth_chain "
        "WHERE test_id=? ORDER BY layer,sort_seq,id", (_tid(test_id),)).fetchall()
    out, by_key = [], {}
    for rid, layer, head, _most, remedy, confirmed, origin, *dose in rows:
        head = (head or "").strip()
        if not head:
            continue
        key = _norm(head)
        g = by_key.get(key)
        if g is None:
            g = by_key[key] = {"head": head, "key": key, "layer": layer, "rows": []}
            out.append(g)
        g["rows"].append({"id": rid, "remedy": (remedy or "").strip(), "layer": layer,
                          "most_affected": (_most or "").strip(),
                          "confirmed": bool(confirmed), "origin": origin or "",
                          "dose": [d or "" for d in dose]})
    for g in out:
        g["confirmed"] = any(r["confirmed"] for r in g["rows"])
        g["fixed"] = any(r["origin"] in FIXED_ORIGINS for r in g["rows"])
    return out


def targets(cx, items):
    """What the Clinical Summary asks for, one entry per layer it earns.
    [{name, keys, labels, remedies, checked}] in checklist order, plus the checked
    labels that earn no layer. An unchecked row is kept with checked False, so a
    layer it once earned can be reported stale."""
    topics, display = _topics(cx), display_labels(cx)
    out, by_key, no_pattern = [], {}, []
    for item in items or []:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        # The stored pattern names the layer when there is one; the pattern the page
        # shows (one typed for this test, else a suggestion) only when none is stored.
        # Review round 1 F7: the program and correct_chain read the stored one.
        pattern = str(item.get("remembered_pattern") or item.get("stress_pattern") or "").strip()
        key = _norm(label)
        if pattern:
            name, keys = pattern, {_norm(pattern)}
        elif key in topics:
            name = display.get(key) or label
            keys = {key, _norm(name)}
        else:
            if item.get("checked"):
                no_pattern.append(label)
            continue
        t = next((by_key[k] for k in sorted(keys) if k in by_key), None)
        if t is None:
            t = {"name": name, "keys": set(), "labels": [], "remedies": [], "checked": False,
                 "by_pattern": bool(pattern)}
            out.append(t)
        t["keys"] |= keys
        for k in keys:
            by_key[k] = t
        if item.get("checked"):
            t["checked"] = True
            t["labels"].append(label)
            # The remedies the page shows ticked: the saved set, else the chain
            # remedy that covers the row.
            for remedy in row_ticks(item):
                if str(remedy or "").strip() and remedy not in t["remedies"]:
                    t["remedies"].append(remedy)
    return out, no_pattern


def plan(cx, test_id, items, resolve=None):
    """{"changes": [...], "stale": [...], "no_pattern": [...], "digest"}.

    `items` is the checklist as the page builds it, selections applied. `resolve`
    maps a ticked name to the name the chain stores (resolve_remedy_name)."""
    resolve = resolve or (lambda cx, name: name)
    layers = chain_layers(cx, test_id)
    wanted, no_pattern = targets(cx, items)
    on_chain = {}
    for g in layers:
        for r in g["rows"]:
            k = _remedy_key(cx, r["remedy"], resolve)
            if k:
                on_chain.setdefault(k, g["head"])
    # A blank-head row is a layer of its own on the page: its remedy is on the chain.
    blank = _blank_rows(cx, test_id)
    for r in blank:
        k = _remedy_key(cx, r[1], resolve)
        if k:
            on_chain.setdefault(k, "an unnamed layer")
    changes, stale, matched, elsewhere = [], [], set(), []
    # Existing layers first, in chain order, then new ones in checklist order: a
    # remedy ticked on two rows lands on the earlier layer, and a new layer is last.
    order = {g["key"]: i for i, g in enumerate(layers)}
    paired = [(next((g for g in layers if g["key"] in t["keys"]), None), t) for t in wanted]
    paired.sort(key=lambda p: order[p[0]["key"]] if p[0] is not None else len(order))
    # Rows that reach one layer by different names are one target for it, so its
    # changes are offered once and no row's ticks are dropped.
    merged, by_layer = [], {}
    for layer, t in paired:
        if layer is None:
            merged.append((layer, t))
        elif layer["key"] in by_layer:
            first = by_layer[layer["key"]]
            first["checked"] = first["checked"] or t["checked"]
            first["labels"] += [l for l in t["labels"] if l not in first["labels"]]
            first["remedies"] += [r for r in t["remedies"] if r not in first["remedies"]]
        else:
            t = dict(t, labels=list(t["labels"]), remedies=list(t["remedies"]))
            by_layer[layer["key"]] = t
            merged.append((layer, t))
    for layer, t in merged:
        if layer is not None and layer["fixed"]:
            continue
        if layer is not None:
            matched.add(layer["key"])
        if not t["checked"]:
            if layer is not None:
                stale.append({"layer": layer["head"],
                              "remedies": [r["remedy"] for r in layer["rows"] if r["remedy"]]})
            continue
        ticked = {}
        for remedy in t["remedies"]:
            name = resolve(cx, remedy) or remedy
            ticked.setdefault(_norm(name), name)
        if layer is None:
            add = []
            for k, name in ticked.items():
                if k in on_chain:
                    elsewhere.append({"remedy": name, "layer": on_chain[k], "for": t["name"]})
                else:
                    on_chain[k] = t["name"]
                    add.append(name)
            changes.append({"id": "new:" + _norm(t["name"]), "kind": "new",
                            "layer": t["name"], "label": t["labels"][0] if t["labels"] else "",
                            "remedies": add, "preticked": True,
                            "balances": _wording(t)})
            continue
        have = {}
        for r in layer["rows"]:
            if r["remedy"]:
                have.setdefault(_remedy_key(cx, r["remedy"], resolve), []).append(r)
        for k, name in ticked.items():
            if k in have:
                continue
            if k in on_chain:
                elsewhere.append({"remedy": name, "layer": on_chain[k], "for": layer["head"]})
                continue
            # An add left unticked on a confirmed layer must not keep the remedy off a
            # later layer; apply() still lands each remedy once whichever are ticked.
            if not layer["confirmed"]:
                on_chain[k] = layer["head"]
            changes.append({"id": "add:" + layer["key"] + ":" + k, "kind": "add",
                            "layer": layer["head"], "remedy": name,
                            "confirmed_layer": layer["confirmed"],
                            "preticked": not layer["confirmed"],
                            "balances": _wording(t)})
        for k, rows in have.items():
            if k in ticked:
                continue
            for r in rows:
                changes.append({"id": "remove:" + str(r["id"]), "kind": "remove",
                                "layer": layer["head"], "remedy": r["remedy"], "rid": r["id"],
                                "confirmed_layer": layer["confirmed"], "preticked": False})
    # A Clinical layer no row earns any more, whatever its name became.
    for g in layers:
        if g["key"] not in matched and not g["fixed"] \
                and all(r["origin"] == "clinical" for r in g["rows"]):
            stale.append({"layer": g["head"],
                          "remedies": [r["remedy"] for r in g["rows"] if r["remedy"]]})
    return {"changes": changes, "stale": stale, "no_pattern": no_pattern,
            "elsewhere": elsewhere,
            "digest": _digest(layers, changes, [_ticks(cx, test_id), blank])}


def _wording(t):
    """The full wording a pattern-named layer balances. Glen, 2026-10-20: the
    pattern is the Head and Tail, and the condition's wording goes to the Balances.
    Empty for a layer named by a topic, which keeps the wording in its Tail."""
    return [l for l in t["labels"] if _norm(l) != _norm(t["name"])] if t.get("by_pattern") else []


def _blank_rows(cx, test_id):
    return [list(r) for r in cx.execute(
        "SELECT id,remedy,layer,most_affected,dosage,frequency,timing,confirmed,origin "
        "FROM biofield_auth_chain WHERE test_id=? AND TRIM(COALESCE(head,''))='' "
        "ORDER BY id", (_tid(test_id),))]


def _ticks(cx, test_id):
    """The stored ticks, order and decisions, read inside the caller's transaction, so
    a tick saved between the preview and the apply changes the digest."""
    out = []
    for table in ("biofield_clinical_selection", "biofield_clinical_proposals",
                  "biofield_clinical_order"):
        try:
            out.append(sorted(map(list, cx.execute(
                f"SELECT * FROM {table} WHERE test_id=?", (str(test_id),)))))
        except Exception:  # table absent on a fresh database
            out.append(None)
    return out


def _digest(layers, changes, ticks=None):
    body = json.dumps([[(g["head"], g["rows"]) for g in layers], changes, ticks],
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def apply(cx, test_id, items, digest, chosen, resolve=None, dosing=None, now=""):
    """Write the chosen changes, re-planned inside the write lock. Raises Stale when
    the plan's digest is not the one previewed. Returns a count per kind.

    `chosen` is the list of change ids Glen ticked. One that is not in the plan is
    refused as stale too: it can only come from an older preview."""
    dosing = dosing or (lambda cx, name: {})
    from dashboard.biofield_clinical_head_term import card_rids, link_wording
    from dashboard.biofield_stress import init_stress_tables
    init_stress_tables(cx)   # it commits, so before the write lock, never inside it
    if cx.in_transaction:  # the checklist build may have opened one
        cx.commit()
    cx.execute("BEGIN IMMEDIATE")
    try:
        p = plan(cx, test_id, items, resolve)
        ids = {c["id"]: c for c in p["changes"]}
        if p["digest"] != digest or any(c not in ids for c in chosen or []):
            raise Stale("The chain or the Clinical Summary changed. Preview again.")
        tid = _tid(test_id)
        layers = {g["key"]: g for g in chain_layers(cx, test_id)}
        landed = {_remedy_key(cx, r[0], resolve or (lambda cx, n: n)) for r in cx.execute(
            "SELECT remedy FROM biofield_auth_chain WHERE test_id=?", (tid,))} - {""}
        # In plan order, once each: an earlier layer takes a remedy first.
        picked = set(chosen or [])
        chosen = [c["id"] for c in p["changes"] if c["id"] in picked]
        last = cx.execute("SELECT COALESCE(MAX(layer),0) FROM biofield_auth_chain "
                          "WHERE test_id=?", (tid,)).fetchone()[0] or 0
        done = {"added": 0, "removed": 0, "layers_added": 0}
        for cid in chosen or []:
            c = ids[cid]
            if c["kind"] == "add":
                k = _norm(c["remedy"])
                if k in landed:
                    continue
                landed.add(k)
                g = layers[_norm(c["layer"])]
                _insert(cx, tid, g["layer"], g["head"], "", c["remedy"],
                        dosing(cx, c["remedy"]), confirmed=0, origin="clinical", now=now)
                link_wording(cx, tid, c.get("balances"), card_rids(cx, tid, g["head"]))
                done["added"] += 1
            elif c["kind"] == "remove":
                _remove(cx, tid, c["rid"], now)
                done["removed"] += 1
            elif c["kind"] == "new":
                last += 1
                names = [n for n in c["remedies"] if _norm(n) not in landed]
                landed |= {_norm(n) for n in names}
                # A pattern-named layer carries the pattern as its Tail too; the
                # wording goes to its Balances. A topic-named one keeps the wording.
                tail = c["layer"] if c.get("balances") else (c["label"] or c["layer"])
                for name in names or [""]:
                    _insert(cx, tid, last, c["layer"], tail, name,
                            dosing(cx, name) if name else {}, confirmed=0,
                            origin="clinical", now=now)
                link_wording(cx, tid, c.get("balances"), card_rids(cx, tid, c["layer"]))
                done["layers_added"] += 1
        cx.commit()
        return done
    except BaseException:
        cx.rollback()
        raise


def _insert(cx, tid, layer, head, most_affected, remedy, d, confirmed, origin, now):
    cx.execute(
        "INSERT INTO biofield_auth_chain(test_id,layer,head,most_affected,remedy,dosage,"
        "frequency,timing,sort_seq,created_at,updated_at,confirmed,origin,codes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, layer, head, most_affected, remedy, d.get("dosage", ""),
         d.get("frequency", ""), d.get("timing", ""), 0, now, now, confirmed, origin, "[]"))


def _remove(cx, tid, rid, now):
    """As remove_remedy_preserving_layer, inside this transaction: the layer's last
    remedy row stays as its anchor with the remedy cleared."""
    row = cx.execute("SELECT head,most_affected FROM biofield_auth_chain "
                     "WHERE id=? AND test_id=?", (rid, tid)).fetchone()
    if not row:
        raise Stale("A row to remove is gone. Preview again.")
    same = "test_id=? AND id<>? AND LOWER(TRIM(COALESCE(head,'')))=LOWER(TRIM(?))"
    siblings = cx.execute(f"SELECT COUNT(*) FROM biofield_auth_chain WHERE {same}",
                          (tid, rid, row[0] or "")).fetchone()[0]
    # The row carrying a tail keeps it unless a sibling carries the same tail.
    tail_kept = not (row[1] or "").strip() or cx.execute(
        f"SELECT COUNT(*) FROM biofield_auth_chain WHERE {same} AND "
        "LOWER(TRIM(COALESCE(most_affected,'')))=LOWER(TRIM(?))",
        (tid, rid, row[0] or "", row[1])).fetchone()[0]
    if siblings and tail_kept:
        from dashboard.biofield_stress import move_links_off
        move_links_off(cx, tid, [rid])     # its Balances stay on the card
        cx.execute("DELETE FROM biofield_auth_chain WHERE id=?", (rid,))
    else:
        cx.execute("UPDATE biofield_auth_chain SET remedy='',dosage='',frequency='',timing='',"
                   "schedule_slot='',updated_at=? WHERE id=?", (now, rid))
