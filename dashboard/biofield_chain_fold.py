"""A remedy belongs on one layer of a causal chain, the earliest one.

Glen, 2026-09-24, on Alyssa Fukushima's intake (a40): layer 11 was balanced by Nous
Energy, already on layer 8. *"Layer 11 should be folded into Layer 8 automatically,
with the tail stresses incorporated into Layer 8's tail."* Then, for layers he has
confirmed: *"can the flag propose the fold for approval?"*

So there are two outcomes for a repeat:
- Every row involved is still a proposal, both the rows removed and, for a whole
  fold, the layer whose tail grows: it folds on its own, after any write to the chain.
- A confirmed row is involved: the page flags it and proposes the fold; Glen approves.

A later layer whose EVERY remedy already sits on earlier layers is folded whole: its
tail joins the earliest such layer's tail and its rows go. A later layer with other
remedies of its own keeps its stresses, because those remedies still balance them;
only the repeated remedy row goes.

A "layer" here is a card as the page shows it (`group_layers` over `ordered_chain`),
not a stored layer number: rows sharing a Head are one layer.
"""

from dashboard.biofield_authoring import _num, ordered_chain


def _key(name):
    return str(name or "").strip().lower()


def _remedy_key(name):
    """A remedy's catalog identity, so "ES1 Immune" and "ES1 Lymph and Immune
    Energetic Star Infoceutical" are one remedy. Glen, 2026-10-22: "When folding
    layers together, do not duplicate an identical remedy."

    Exact names only: a retired name's survivor, then the catalog's aliases, then the
    name itself. Never a fuzzy match, because a fold deletes rows."""
    from dashboard import biofield_authoring as _a
    k = _a._norm_name(str(name or "").strip())
    if not k:
        return ""
    # Retired-name redirects are left out: some cross product forms or doses
    # ("Sublingual B12 Powder" to the drops). A missed fold is safe; a wrong delete is not.
    hit = _a._catalog_exact_aliases().get(k) or _a._catalog_alias_map().get(k)
    return _a._norm_name(hit) if hit else k


def _tail_items(text):
    return [t.strip() for t in str(text or "").split(",") if t.strip()]


def merged_tail(into_tail, extra_tail):
    """`into_tail` with each item of `extra_tail` it lacks appended, order kept."""
    items = _tail_items(into_tail)
    seen = {_key(t) for t in items}
    for t in _tail_items(extra_tail):
        if _key(t) not in seen:
            seen.add(_key(t))
            items.append(t)
    return ", ".join(items)


def duplicate_folds(groups):
    """Each later layer carrying a remedy already on an earlier layer.

    Pure. `groups` is `group_layers(...)` output. Returns, in page order:
    {layer, into, remedies, whole, confirmed, rids, into_rids, tail}
    where `layer`/`into` are page layer numbers, `rids` the rows the fold deletes,
    `into_rids` the rows of the layer that receives the tail (whole folds only).
    """
    first = {}
    out = []
    for gi, g in enumerate(groups or []):
        rows = g.get("rows") or []
        named = [r for r in rows if _remedy_key(r.get("remedy"))]
        # A repeat of an earlier layer's remedy, or of an earlier row on this same
        # layer: the first row of each remedy stays, with its dose and note.
        repeats, same, kept_here = [], [], {}
        for r in named:
            k = _remedy_key(r.get("remedy"))
            if k in first:
                repeats.append(r)
                if first[k] == gi:
                    same.append(r)
            else:
                first[k] = gi
                kept_here[k] = r
        if not repeats:
            continue
        whole = len(repeats) == len(named)
        into_gi = min(first[_remedy_key(r.get("remedy"))] for r in repeats)
        into = groups[into_gi]
        gone = rows if whole else repeats
        # A repeat on its own card involves the row it repeats: when that row is
        # confirmed, the fold is proposed, never applied on its own (review, 2026-10-22).
        survivors = [kept_here[_remedy_key(r.get("remedy"))] for r in same]
        involved = gone + (into.get("rows") or [] if whole else []) + survivors
        out.append({
            "layer": g.get("layer", gi + 1),
            "into": into.get("layer", into_gi + 1),
            "remedies": [r.get("remedy") for r in repeats if r not in same],
            "same_card": list(dict.fromkeys(r.get("remedy") for r in same)),
            "sources": [(r.get("remedy"),
                         groups[first[_remedy_key(r.get("remedy"))]].get(
                             "layer", first[_remedy_key(r.get("remedy"))] + 1))
                        for r in repeats if r not in same],
            "whole": whole,
            "confirmed": any(r.get("confirmed", 1) for r in involved),
            "rids": [r.get("id") for r in gone],
            "into_rids": [r.get("id") for r in into.get("rows") or []] if whole else [],
            "tail": (g.get("most_affected") or "").strip(),
            "into_tail": (into.get("most_affected") or "").strip(),
        })
    return out


def chain_folds(cx, tid):
    from dashboard.biofield_report_html import group_layers
    return duplicate_folds(group_layers(ordered_chain(cx, tid)))


def _apply(cx, tid, fold):
    if fold["whole"] and fold["into_rids"]:
        tail = merged_tail(fold["into_tail"], fold["tail"])
        marks = ",".join("?" * len(fold["into_rids"]))
        cx.execute(f"UPDATE biofield_auth_chain SET most_affected=? "
                   f"WHERE test_id=? AND id IN ({marks})",
                   [tail, _num(tid)] + list(fold["into_rids"]))
    # The folded rows' Balances go where the rows went: the layer folded into, or the
    # rows left on their own card.
    from dashboard.biofield_stress import move_links_off
    move_links_off(cx, tid, fold["rids"],
                   to_rids=fold["into_rids"] if fold["whole"] and fold["into_rids"] else None)
    marks = ",".join("?" * len(fold["rids"]))
    cx.execute(f"DELETE FROM biofield_auth_chain WHERE test_id=? AND id IN ({marks})",
               [_num(tid)] + list(fold["rids"]))
    cx.commit()


def fold_layer(cx, tid, layer, rids):
    """Glen approved folding page layer `layer`, shown to him as rows `rids`.

    Re-derived from the chain as it is now. It folds only if the rows it would remove
    are exactly the rows the page showed, so a stale tab cannot delete the wrong ones.
    """
    want = {int(r) for r in rids or []}
    for f in chain_folds(cx, tid):
        if int(f["layer"]) == int(layer) and set(f["rids"]) == want:
            _apply(cx, tid, f)
            return f
    return None


def auto_fold(cx, tid, limit=50):
    """Fold every repeat whose removed rows are all unconfirmed. Each fold renumbers
    the page layers, so the chain is re-read after every one."""
    done = []
    for _ in range(limit):
        f = next((f for f in chain_folds(cx, tid) if not f["confirmed"]), None)
        if f is None:
            break
        _apply(cx, tid, f)
        done.append(f)
    return done
