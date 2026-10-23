"""A layer made from a Clinical Summary condition is named by the condition's term.

Glen, 2026-10-20: "When it adds the layers from the Clinical, it is failing to make the
Head & Tail show the term like Ovaries or Adrenal, and instead puts the full description
which should go into the Balances list." And: "all the full wording data should go into
the list of what balances on that layer. Only the simple structure/function terms will
then show on the report."

His terms already exist. They are the "Stress pattern (head & tail)" field, stored in
biofield_clinical_stress.pattern and read through stress_pattern(cx, label). No new map.

So, for every path that writes a layer from a condition:
  - a condition WITH a stored pattern: Head and Tail carry the pattern, and the full
    wording becomes a stress linked to the layer's rows (biofield_auth_layer_stress),
    which is the card's "balances:" list;
  - a condition with NO stored pattern keeps today's behaviour, and its card is
    flagged with NO_PATTERN_FLAG.

A Head Glen typed is never rewritten. Nothing records who wrote a Head: updated_at is
bumped by imports and moves too, and on a50 row 454 carried a bumped updated_at with
the program's wording still in its Head. So the correction rewrites a Head or a Tail
item only when it EQUALS the full wording of a condition this client has that has a
stored pattern. "Green Jasper Gem Elixir" (a50, card 6) is no client's condition, so it
always stays.
"""
import re

from dashboard.biofield_clinical_checklist import _norm as _cnorm
from dashboard.biofield_stress import (_norm as _snorm, _num, _now, _table_exists,
                                       client_condition_labels, move_links_off)

# Shown on a layer card whose Head is a condition's full wording because the condition
# has no stored pattern yet. Glen approves this text.
NO_PATTERN_FLAG = "No stress pattern yet for this condition"

_SEP = re.compile(r"[,;\n]+")


def stored_patterns(cx):
    """{normalised condition label: pattern} for every stored pattern. Read only: the
    table is never created here, so a dry run on a read-only copy cannot write."""
    if not _table_exists(cx, "biofield_clinical_stress"):
        return {}
    return {k: (p or "").strip() for k, p in cx.execute(
        "SELECT item_key, pattern FROM biofield_clinical_stress") if (p or "").strip()}


def pattern_of(label, patterns):
    """The stored pattern for a label, looked up the way stress_pattern() does."""
    return patterns.get(_cnorm(label), "") if label else ""


def _key(text):
    """The only folding a full-wording match allows: case and surrounding spaces.
    No punctuation folding, so "Adrenal fatigue current 2023" typed by Glen is not
    "Adrenal Fatigue, Current, 2023" (review round 1 F3, round 2)."""
    return (text or "").strip().casefold()


def typed_patterns(cx, tid):
    """{selection key: pattern} typed into the field for this one test. Read only."""
    if not _table_exists(cx, "biofield_clinical_selection"):
        return {}
    t = _num(tid)
    return {k: (p or "").strip() for k, p in cx.execute(
        "SELECT item_key, pattern FROM biofield_clinical_selection "
        "WHERE test_id IN (?,?) AND TRIM(COALESCE(pattern,''))<>''", ("a" + str(t), str(t)))}


def shown_pattern(cx, label, patterns, typed):
    """The pattern the "Stress pattern (head & tail)" field shows for `label`, in the
    page's own order: one typed for this test, then the stored one, then the drafted
    suggestion, with any folded condition's function appended as build() does.

    Agnes Verches (test 56, 2026-10-23): her field showed the suggestion "Macular
    Vascular Containment" for Wet AMD, but only the stored table was read here, so the
    program named layer 4 "Wet AMD", the diagnosis. Never falls back to the wording:
    no pattern anywhere returns ""."""
    from dashboard.biofield_clinical_checklist import (absorbed_patterns, combine_patterns,
                                                       suggested_pattern)
    from dashboard.biofield_clinical_proposals import _key as _skey
    typed_p = typed.get(_skey(label), "")
    if typed_p:
        return typed_p                 # apply_selection puts it over everything
    stored = pattern_of(label, patterns)
    base = stored or suggested_pattern(label)
    folded = []
    if _table_exists(cx, "biofield_clinical_alias"):
        folded = absorbed_patterns(cx, label, lambda l: pattern_of(l, patterns))
    return (combine_patterns(base, folded) if folded else base).strip()


def _resolve(cx, tid, patterns=None):
    """(conditions, cycles). `conditions` is {_key(wording): (wording, final pattern)}
    for this client's conditions with a usable pattern, the one the page shows
    (shown_pattern). `cycles` is the set of _key(wording) whose pattern chain loops
    back on itself."""
    patterns = stored_patterns(cx) if patterns is None else patterns
    typed = typed_patterns(cx, tid)
    direct = {}
    for label in client_condition_labels(cx, tid):
        p = shown_pattern(cx, label, patterns, typed)
        if p:
            direct.setdefault(_key(label), (label, p))
    out, cycles = {}, set()
    for k, (label, p) in direct.items():
        if _key(p) == k:              # a wording that already IS its term needs nothing
            continue
        cur, seen, loop = k, {k}, False
        while _key(p) in direct:
            nk = _key(p)
            if nk == cur:             # the chain ends at a term that names itself
                break
            if nk in seen:            # back to a wording already passed: a cycle
                loop = True
                break
            seen.add(nk)
            cur, p = nk, direct[nk][1]
        if loop:
            cycles.add(k)
        else:
            out[k] = (label, p)
    return out, cycles


def conditions(cx, tid, patterns=None):
    """This client's conditions that have a stored pattern: {_key(wording): (wording,
    pattern)}. Wording comes from client_condition_labels, the same source set as
    consolidate. The pattern is followed to a fixed point: when one condition's
    pattern is another condition's wording, the final term is used, so a second
    correction changes nothing (review round 1 F5). A condition whose chain loops is
    left out, so it is handled as a condition with no pattern (review round 3 N3)."""
    return _resolve(cx, tid, patterns)[0]


def pattern_cycles(cx, tid, patterns=None):
    """_key(wording) of this client's conditions whose pattern chain loops. Their cards
    show the missing-pattern flag."""
    return _resolve(cx, tid, patterns)[1]


def layer_fields(covers, conds, lead=None):
    """(head, tail, wording) for a layer built from `covers`, the stress labels its
    remedy balances. Today's rule picks the Head: `lead` when given, else the first
    cover ending in "driver", else the first cover. A cover that is exactly one of
    this client's conditions (`conds`, from conditions()) is replaced by its pattern in
    Head and Tail, and returned in `wording` to be linked as a balance. Any other
    cover, such as a voice stress Glen spoke, keeps its words. With no condition among
    the covers this returns exactly what the paths wrote before."""
    covers = [c for c in (covers or []) if (c or "").strip()]
    head0 = lead or next((c for c in covers if c.strip().lower().endswith("driver")),
                         covers[0] if covers else "")
    wording, tail, seen = [], [], set()
    for c in covers:
        hit = conds.get(_key(c))
        if hit:
            if c not in wording:
                wording.append(c)
            c = hit[1]
        if _key(c) not in seen:
            seen.add(_key(c))
            tail.append(c)
    head = (conds.get(_key(head0)) or (None, head0))[1]
    if not wording:
        return head, ", ".join(covers)[:200], []
    return head, ", ".join(tail)[:200], wording


def stress_id_for(cx, tid, label, source="clinical"):
    """The stress on this test whose label is `label`, added when there is none.
    Never commits, so it can run inside a caller's transaction."""
    t, n = _num(tid), _snorm(label)
    if not n:
        return None
    for sid, lab in cx.execute("SELECT id,label FROM biofield_auth_stress WHERE test_id=?",
                               (t,)).fetchall():
        if _snorm(lab) == n:
            return int(sid)
    now = _now()
    cx.execute("INSERT OR IGNORE INTO biofield_auth_stress(test_id,code,label,source,balance,"
               "manual_balanced,created_at,updated_at) VALUES(?,?,?,?,'required',0,?,?)",
               (t, n, label.strip(), source, now, now))
    row = cx.execute("SELECT id FROM biofield_auth_stress WHERE test_id=? AND source=? AND code=?",
                     (t, source, n)).fetchone()
    return int(row[0]) if row else None


def link_wording(cx, tid, labels, rids):
    """Put each condition's full wording in the Balances of rows `rids`: the same
    stress rows and links a drag onto a card makes. Never commits. Returns the ids."""
    rids = [int(r) for r in rids or [] if r is not None]
    out = []
    if not rids:
        return out
    for label in labels or []:
        sid = stress_id_for(cx, tid, label)
        if sid is None:
            continue
        for rid in rids:
            cx.execute("INSERT OR IGNORE INTO biofield_auth_layer_stress"
                       "(test_id,stress_id,chain_rid) VALUES(?,?,?)", (_num(tid), sid, rid))
        out.append(sid)
    return out


def card_rids(cx, tid, head):
    """Every row of the card with this Head, as group_layers groups them."""
    h = (head or "").strip().lower()
    if not h:
        return []
    return [int(r[0]) for r in cx.execute(
        "SELECT id FROM biofield_auth_chain WHERE test_id=? AND LOWER(TRIM(COALESCE(head,'')))=?",
        (_num(tid), h))]


def no_pattern_heads(items):
    """{normalised wording: wording} of the Clinical Summary rows whose field shows no
    pattern (typed, stored or suggested), or whose pattern chain loops (`pattern_cycle`, set by the page). A card
    whose Head or any Tail item is one of these is flagged."""
    return {_cnorm(i.get("label")): str(i.get("label")).strip() for i in items or []
            if (i.get("label") or "").strip()
            and (i.get("pattern_cycle") or not (i.get("stress_pattern") or "").strip())}


# ---- correcting an existing chain -------------------------------------------------

_FIELD_SPLIT = re.compile(r"([,;\n]+)")


def _replace_in_field(text, conds):
    """(new text, [wording found]). Replaces each item of a Tail that is exactly a
    condition's wording with its pattern. Every other item, Glen's separators and his
    repeated items stay as they were (review round 1 F4). The wording holds commas
    itself, so it is matched whole between separators, longest first. Where the same
    pattern would then stand twice, the extra copy and its separator go: that repeat
    is the path's, never Glen's."""
    text = text or ""
    found, slots = [], []
    for k, (wording, pattern) in sorted(conds.items(), key=lambda kv: -len(kv[1][0])):
        rx = re.compile(r"(?:^|(?<=[,;\n]))(\s*)" + re.escape(wording.strip()) +
                        r"(\s*)(?=[,;\n]|$)", re.I)

        def put(m, pattern=pattern):
            slots.append(pattern)
            return m.group(1) + "\x00%d\x00" % (len(slots) - 1) + m.group(2)
        new = rx.sub(put, text)
        if new != text:
            found.append((wording, pattern))
            text = new
    if not found:
        return text, []
    parts = _FIELD_SPLIT.split(text)
    items, seps = parts[0::2], parts[1::2]
    plain = {_key(i) for i in items if "\x00" not in i}
    used, keep = set(), []
    for n, item in enumerate(items):
        m = re.search(r"\x00(\d+)\x00", item)
        if m:
            term = slots[int(m.group(1))]
            if _key(term) in plain or _key(term) in used:
                keep.append(None)
                continue
            used.add(_key(term))
            # The replaced item is the term alone, after one space when it follows a
            # comma or semicolon (review round 3 N4).
            prev = seps[n - 1] if n > 0 else "\n"
            item = term if prev.endswith("\n") else " " + term
        keep.append(item)
    out = ""
    for n, item in enumerate(keep):
        if item is None:
            continue
        if out or any(k is not None for k in keep[:n]):
            out += seps[n - 1] if n > 0 else ""
        out += item
    # Nothing is stored with leading or trailing whitespace, even when the first
    # item was the one dropped.
    return out.strip(), found


def plan_correction(cx, test_id):
    """What correct_chain would change, read only. {"rows": [{id, head, new_head,
    tail, new_tail, wording}], "links": [{stress_id, label, to}], "dangling": [{stress_id,
    label, deleted_rid, to}], "unbalanced": [...], "fragments": [{stress_id, label}]}."""
    tid = _num(test_id)
    conds = conditions(cx, tid)
    rows = cx.execute("SELECT id, head, most_affected FROM biofield_auth_chain WHERE test_id=? "
                      "ORDER BY layer, sort_seq, id", (tid,)).fetchall()
    plan = {"rows": [], "links": [], "dangling": [], "unbalanced": [], "fragments": []}
    new_head = {}
    wording_on = {}          # row id -> [wording]
    for rid, head, tail in rows:
        head, tail = (head or "").strip(), (tail or "").strip()
        nh, words = head, []
        hit = conds.get(_key(head))
        if hit:                       # a Head that IS a condition's wording, never Glen's
            nh = hit[1]
            words.append(hit[0])
        nt, found = _replace_in_field(tail, conds)
        words += [w for w, _ in found if w not in words]
        new_head[int(rid)] = nh
        if nh != head or nt != tail:
            plan["rows"].append({"id": int(rid), "head": head, "new_head": nh,
                                 "tail": tail, "new_tail": nt, "wording": words})
        if words:
            wording_on[int(rid)] = words
    # The wording goes to every row of the card the row is on after the rewrite.
    by_head = {}
    for rid, h in new_head.items():
        if h.strip():
            by_head.setdefault(h.strip().lower(), []).append(rid)
    placed = {}
    for rid, words in wording_on.items():
        h = new_head[rid].strip().lower()
        to = by_head.get(h, [rid]) if h else [rid]
        for w in words:
            placed.setdefault(_snorm(w), (w, set()))[1].update(to)
    stresses = {}
    if _table_exists(cx, "biofield_auth_stress"):
        stresses = {int(i): (lab or "") for i, lab in cx.execute(
            "SELECT id,label FROM biofield_auth_stress WHERE test_id=?", (tid,))}
    sid_by_norm = {_snorm(l): i for i, l in stresses.items()}
    links = set()
    if _table_exists(cx, "biofield_auth_layer_stress"):
        links = {(int(s), int(r)) for s, r in cx.execute(
            "SELECT stress_id, chain_rid FROM biofield_auth_layer_stress WHERE test_id=?", (tid,))}
    for n, (w, to) in placed.items():
        sid = sid_by_norm.get(n)
        new = sorted(r for r in to if sid is None or (sid, r) not in links)
        if new:
            plan["links"].append({"stress_id": sid, "label": w, "to": new})
    live = {int(r[0]) for r in rows}
    for sid, rid in sorted(links):
        if rid in live:
            continue
        others = sorted(r for s, r in links if s == sid and r in live)
        n = _snorm(stresses.get(sid, ""))
        to = sorted(placed[n][1]) if n in placed else []
        entry = {"stress_id": sid, "label": stresses.get(sid, ""), "deleted_rid": rid,
                 "to": to, "still_on": others}
        plan["dangling"].append(entry)
        if not others and not to:
            plan["unbalanced"].append(entry)
    # Pieces of a wording that a comma split turned into stresses of their own
    # ("Current", "2020"). Listed only; correct_chain removes them only when asked.
    pieces = {}
    for w, _p in conds.values():
        for part in _SEP.split(w):
            if _snorm(part) and _snorm(part) != _snorm(w):
                pieces.setdefault(_snorm(part), part.strip())
    if _table_exists(cx, "biofield_auth_stress"):
        for sid, lab, src in cx.execute("SELECT id,label,source FROM biofield_auth_stress "
                                        "WHERE test_id=? AND source='chain' ORDER BY id", (tid,)):
            if _snorm(lab) in pieces:
                plan["fragments"].append({"stress_id": int(sid), "label": lab})
    return plan


def correct_chain(cx, test_id, dry_run=True, drop_fragments=False):
    """Apply the fixed rule to one client's existing chain. dry_run (the default)
    returns the plan and writes nothing. Otherwise, in one transaction: rewrite the
    Heads and Tails that hold a condition's full wording, link that wording to the
    card's rows, and move or drop links to deleted rows. updated_at is left alone, so
    this never reads as Glen's edit. A Head that is not a condition's wording, such as
    a remedy he set, is never touched."""
    if dry_run:
        return plan_correction(cx, test_id)
    if cx.in_transaction:
        cx.commit()
    cx.execute("BEGIN IMMEDIATE")
    try:
        p = plan_correction(cx, test_id)
        tid = _num(test_id)
        for r in p["rows"]:
            cx.execute("UPDATE biofield_auth_chain SET head=?, most_affected=? "
                       "WHERE id=? AND test_id=?", (r["new_head"], r["new_tail"], r["id"], tid))
        for ln in p["links"]:
            link_wording(cx, tid, [ln["label"]], ln["to"])
        dead = sorted({d["deleted_rid"] for d in p["dangling"]})
        if dead:
            move_links_off(cx, tid, dead, to_rids=[])
        if drop_fragments:
            for f in p["fragments"]:
                cx.execute("DELETE FROM biofield_auth_layer_stress WHERE test_id=? AND stress_id=?",
                           (tid, f["stress_id"]))
                cx.execute("DELETE FROM biofield_auth_stress WHERE test_id=? AND id=?",
                           (tid, f["stress_id"]))
        cx.commit()
        return p
    except BaseException:
        cx.rollback()
        raise
