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
        named = [r for r in rows if _key(r.get("remedy"))]
        repeats = [r for r in named
                   if _key(r.get("remedy")) in first and first[_key(r.get("remedy"))] < gi]
        for r in named:
            first.setdefault(_key(r.get("remedy")), gi)
        if not repeats:
            continue
        whole = len(repeats) == len(named)
        into_gi = min(first[_key(r.get("remedy"))] for r in repeats)
        into = groups[into_gi]
        gone = rows if whole else repeats
        involved = gone + (into.get("rows") or [] if whole else [])
        out.append({
            "layer": g.get("layer", gi + 1),
            "into": into.get("layer", into_gi + 1),
            "remedies": [r.get("remedy") for r in repeats],
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
