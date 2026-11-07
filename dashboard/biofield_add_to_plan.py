"""Add to plan: where each incoming layer goes (Glen, 2026-11-07).

"Each one added adds its stress patterns that are already covered to the layers
that cover them from other sections already entered."

Each incoming layer lands in one of three places:

  joined   the same product as a layer already on the plan. Its stress patterns
           (codes, and the tail items it lacks) go onto that layer. No new row.
  related  a different product, but the 18 October cover test in
           biofield_reveal_cover says an existing layer covers it: the condition
           headings share a part, or a ticked recommended infoceutical. Glen
           confirmed that test on 7 November. It is put on the plan as its own
           layer and proposed for joining; nothing joins until Glen picks.
  add      neither. A new layer.

New layers (added and related) are numbered after the last stored layer, in the
order they came in. A later incoming layer of the same product as an earlier one
in the same batch joins that earlier one, so one click never adds a product twice.

The heading test and the ticked-infoceutical test are biofield_reveal_cover's own
functions, never a copy. The same-remedy rule there is replaced here by "joined".

Pure: no database, no request. `key` names a remedy's catalog identity; callers pass
biofield_chain_fold._remedy_key so aliases count as one product.
"""
import json

from dashboard import biofield_reveal_cover as _cover

HARMONY_LASER = "Harmony Laser"


def _plain_key(name):
    return str(name or "").strip().lower()


def codes_of(value):
    """A row's codes as a list, from a list or the JSON the chain stores."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = value.split(",")
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [str(c).strip() for c in value if str(c).strip()]


def _union(first, more):
    out = list(first)
    out += [c for c in more if c not in out]
    return out


def _tail_items(text):
    return [t.strip() for t in str(text or "").split(",") if t.strip()]


def _same(a, b):
    """Two heads or tail items alike, ignoring case and spacing."""
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def merged_tail(into, extra):
    """`into` with each item of `extra` it lacks appended, order kept. Items match
    ignoring case and spacing."""
    items = _tail_items(into)
    seen = {" ".join(t.lower().split()) for t in items}
    for t in _tail_items(extra):
        if " ".join(t.lower().split()) not in seen:
            seen.add(" ".join(t.lower().split()))
            items.append(t)
    return ", ".join(items)


def join_stresses(into_head, into_tail, head, tail, have=()):
    """What a join adds to the kept layer's balanced stresses: the joining layer's
    tail items, then its head when it differs from the kept head. The kept tail is
    never changed.

    Glen, 2026-11-07: "When two layers are joined, and the heads are different, add
    the head not selected to the balanced stresses as well." Then, on the tail: it
    "should be one prominent/relevant finding ... The rest of the info is balanced
    stress patterns on that layer." Nothing matching the kept head, the kept tail,
    `have` or an earlier item is listed, ignoring case and spacing."""
    seen = [into_head, into_tail] + list(have or [])
    out = []
    items = _tail_items(tail)
    if str(head or "").strip() and not _same(head, into_head):
        items.append(str(head).strip())
    for item in items:
        if not any(_same(item, s) for s in seen + out):
            out.append(item)
    return out


def incoming_fields(layer):
    """An incoming layer's head, tail, remedy and codes, whichever names it uses: a
    reveal layer has title/most_affected/remedy_name, a chain row head/most_affected/
    remedy."""
    return {"head": str(layer.get("head") or layer.get("title") or "").strip(),
            "tail": str(layer.get("most_affected") or layer.get("tail") or "").strip(),
            "remedy": str(layer.get("remedy") or layer.get("remedy_name") or "").strip(),
            "codes": codes_of(layer.get("codes"))}


def groups_of(rows):
    """The plan's layers as the page shows them: rows sharing a Head are one layer,
    numbered 1..k in chain order. Uses the page's own grouping."""
    from dashboard.biofield_report_html import group_layers
    ordered = sorted(rows or [], key=lambda r: (r.get("layer") is None,
                                                r.get("layer") or 0, r.get("id") or 0))
    return group_layers([dict(r, most_affected=r.get("most_affected") or "")
                         for r in ordered])


def _conditions(group):
    # As biofield_reveal_cover.plan: a scan row's most_affected holds the scan's
    # labels, which are not conditions.
    out = []
    for r in group["rows"]:
        for k in (("head",) if r.get("origin") == "scan" else ("head", "most_affected")):
            v = str(r.get(k) or "").strip()
            if v and v not in out:
                out.append(v)
    return out


def _summary(group):
    return {"into": group["layer"], "into_head": group.get("head") or "",
            "into_rids": [r.get("id") for r in group["rows"]],
            "into_remedies": [r.get("remedy") for r in group["rows"] if r.get("remedy")]}


def classify(existing_rows, incoming, *, recommended=None, ticked_names=(),
             vocabulary=(), key=None, resolve=None, last_layer=0, laser_for=None,
             hold_related=False):
    """{"joined": [...], "related": [...], "add": [...]}.

    `existing_rows` are chain rows {id, layer, head, most_affected, remedy, codes,
    origin}. `incoming` are layers in click order. `recommended` is the scan's
    recommended codes (None when unread), `ticked_names` the infoceuticals ticked in
    the Clinical Summary, as biofield_reveal_cover.plan takes them. `last_layer` is
    the highest stored layer number. `laser_for(codes)` is the Harmony Laser rule
    (biofield_reveal_import.harmony_laser_for); without it no laser is checked.

    joined:  {"layer", "into", "into_head", "into_rids", "into_remedies", "into_rid",
              "codes" (new to that row), "stresses" (labels for its Balances; its
              tail is never changed),
              "laser" (a laser row to add to that layer, or None)}. A join onto a
              layer from this same batch has "into_new" (the n it was given) and its
              codes are already merged into that layer in "add"/"related".
    related: {"layer", "n", "into", "into_head", "into_rids", "into_remedies",
              "rule", "why"}.
    add:     the incoming layer with "n" set.

    `hold_related` (Glen, 2026-11-07, "yes, hold them in the list"): a related layer
    is not written until Glen picks, so it takes no layer number ("n" is None) and
    the added layers are numbered without gaps.
    """
    key = key or _plain_key
    groups = groups_of(existing_rows)
    by_key = {}
    for g in groups:
        for r in g["rows"]:
            k = key(r.get("remedy"))
            if k and k not in by_key:
                by_key[k] = (g, r)
    conditions, owner = [], {}
    for g in groups:
        for c in _conditions(g):
            if c not in owner:
                owner[c] = g
                conditions.append(c)
    # A ticked code names the layer whose remedy is that infoceutical, else the
    # layer named by the Clinical row that ticked it.
    from dashboard.biofield_clinical_sections import infoceutical_code
    code_owner = {}
    for g in groups:
        for r in g["rows"]:
            c = infoceutical_code(r.get("remedy"))
            if c and c not in code_owner:
                code_owner[c] = g
    ticked_list = []
    if isinstance(ticked_names, dict):
        for cond, names in ticked_names.items():
            ticked_list += list(names or [])
            g = next((owner[c] for c in owner
                      if c.strip().lower() == str(cond or "").strip().lower()), None)
            for n in names or []:
                c = infoceutical_code(n)
                if g is not None and c and c not in code_owner:
                    code_owner[c] = g
    else:
        ticked_list = list(ticked_names or [])
    ticked = _cover.ticked_codes(ticked_list + [r.get("remedy") for r in existing_rows or []])

    out = {"joined": [], "related": [], "add": []}
    placed = {}            # remedy key -> the new layer (dict) this batch already made
    n = int(last_layer or 0)
    for layer in incoming or []:
        f = incoming_fields(layer)
        name = f["remedy"]
        if resolve and name:
            name = resolve(name) or name
        k = key(name)
        if k and k in by_key:
            g, row = by_key[k]
            group_codes = [c for r in g["rows"] for c in codes_of(r.get("codes"))]
            union = _union(group_codes, f["codes"])
            has_laser = any(key(r.get("remedy")) == key(HARMONY_LASER) for r in g["rows"])
            if laser_for and has_laser and f["codes"] and laser_for(union) is None:
                # Joining would put a thyroid or abdomen code on a Harmony Laser
                # layer (Glen, 2026-10-12). Glen decides; it is not joined by itself.
                rn = None if hold_related else n + 1
                n = rn or n
                out["related"].append({
                    "layer": dict(layer, n=rn), "n": rn, **_summary(g),
                    "rule": "same remedy",
                    "why": "joining would put a thyroid or abdomen code on the "
                           "Harmony Laser layer"})
                continue
            laser = None
            if (laser_for and not has_laser and layer.get("laser")
                    and laser_for(union) is not None):
                laser = layer.get("laser")
            new_codes = [c for c in f["codes"] if c not in codes_of(row.get("codes"))]
            out["joined"].append({
                "layer": layer, **_summary(g), "into_rid": row.get("id"),
                "codes": new_codes,
                "stresses": join_stresses(g.get("head"), g.get("most_affected"),
                                          f["head"], f["tail"]),
                "laser": laser})
            continue
        if k and k in placed:
            target = placed[k]
            target["codes"] = _union(codes_of(target.get("codes")), f["codes"])
            tf = incoming_fields(target)
            more = join_stresses(tf["head"], tf["tail"], f["head"], f["tail"],
                                 have=target.get("stresses") or [])
            target["stresses"] = list(target.get("stresses") or []) + more
            out["joined"].append({"layer": layer, "into_new": target["n"],
                                  "into_head": tf["head"], "codes": f["codes"],
                                  "stresses": more, "laser": None})
            continue
        new = dict(layer, n=None)
        if k:
            placed[k] = new
        hit = _cover.heading_match(f["head"], conditions, vocabulary)
        rule = why = g = None
        if hit:
            g = owner.get(hit[0])
            rule, why = "same heading", f"{hit[0]} (shares \"{hit[1]}\")"
        else:
            # The ticked rule alone: no conditions and no remedies are passed, so
            # only covered()'s third test can fire.
            tick = _cover.covered({"codes": f["codes"]}, [], set(), recommended, ticked)
            if tick:
                codes = [c.strip() for c in tick[1].split(",")]
                g = next((code_owner[c] for c in codes if c in code_owner), None)
                rule, why = tick
        if g is None or not hold_related:
            n += 1
            new["n"] = n
        if g is not None:
            out["related"].append({"layer": new, "n": new["n"], **_summary(g),
                                   "rule": rule, "why": why})
        else:
            out["add"].append(new)
    return out


def related_after_landing(rows, new_ids, *, recommended=None, ticked_names=(),
                          vocabulary=(), key=None):
    """The proposals for layers that have just landed (a live layer, or a Mind
    layer from Update Clinical). `rows` is the chain now, `new_ids` the rows just
    written. A layer counts as new only when every row on it is new: a row added to
    a layer already there is part of that layer. A new layer sharing a product with
    an older one is left to the duplicate fold (biofield_chain_fold), not proposed.

    [{"source_rids", "source_head", "into", "into_head", "into_rids",
      "into_remedies", "rule", "why"}]"""
    key = key or _plain_key
    new_ids = {int(i) for i in new_ids or []}
    old = [r for r in rows or [] if int(r.get("id")) not in new_ids]
    old_keys = {key(r.get("remedy")) for r in old} - {""}
    out = []
    for g in groups_of(rows):
        rids = [int(r.get("id")) for r in g["rows"]]
        if not rids or not set(rids) <= new_ids:
            continue
        if {key(r.get("remedy")) for r in g["rows"]} & old_keys:
            continue
        probe = {"head": g.get("head") or "", "most_affected": g.get("most_affected") or "",
                 "remedy": "",
                 "codes": [c for r in g["rows"] for c in codes_of(r.get("codes"))]}
        got = classify(old, [probe], recommended=recommended, ticked_names=ticked_names,
                       vocabulary=vocabulary, key=key)
        for rel in got["related"]:
            out.append({"source_rids": rids, "source_head": g.get("head") or "",
                        "into": rel["into"], "into_head": rel["into_head"],
                        "into_rids": rel["into_rids"],
                        "into_remedies": rel["into_remedies"],
                        "rule": rel["rule"], "why": rel["why"]})
    return out
