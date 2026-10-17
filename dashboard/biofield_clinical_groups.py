"""Named groups of a client's stress factors, each one a Clinical Summary item.

Glen, 2026-10-16: "Make a process to add proposed stress factors to a Clinical Summary
Item: giving the summary item a name, and being able to add more than one factor into
the same summary pattern." Then: "Propose those groupings and names for approval or
editing." Asked whether the factors go into the item's stress pattern or stay a list of
their own, he answered "separate". The head-and-tail term is left exactly as it was.

A group is proposed, approved or dismissed, and it is kept either way. A factor in any
group is never proposed again, so a grouping he has judged is not re-asked.

Approving a group adds its name as a hand-added Clinical Summary item, through the same
decision store as the Add button. Nothing here writes the stresses list.
"""

import json

from dashboard.biofield_clinical_proposals import decide, item_key

# The checklist keeps 160-character labels, so a row name is never cut shorter than
# that before it is matched (review round 3).
NAME_MAX = 160
# Long enough for a whole record finding, so a trailing "ruled out" is never cut off
# and two findings that share a beginning stay two (review round 2).
FACTOR_MAX = 1000
# A medication and a lab result can each inform an item. A procedure only says a test
# was done, and its finding arrives as a result of its own.
RECORD_KINDS = ("diagnosis", "result", "medication")


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('proposed','approved','dismissed')),
        why TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_group_factors (
        group_id INTEGER NOT NULL,
        factor_key TEXT NOT NULL,
        factor TEXT NOT NULL,
        kind TEXT NOT NULL,
        PRIMARY KEY (group_id, factor_key))""")


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def factors_for(cx, test_id, record_findings=None):
    """Every factor this intake can group: its Mind stresses, then the pending record
    findings. [{"factor", "kind"}], one per factor key, in that order.

    record_findings is the caller's list of pending findings, or None when the address
    is shared or could not be checked. The caller decides that, as it does for the
    records box.
    """
    from dashboard.biofield_program import MIND_SOURCES
    from dashboard.biofield_stress import _num, init_stress_tables
    init_stress_tables(cx)
    out, seen = [], set()
    marks = ",".join("?" for _ in MIND_SOURCES)
    # Family history spoken during testing is stored as a voice stress. It belongs in
    # the Family history item all the same (review round 1). Scan rows never do.
    rows = cx.execute(
        f"SELECT label FROM biofield_auth_stress WHERE test_id=? AND (source IN ({marks}) "
        "OR (source<>'scan' AND lower(trim(label)) LIKE 'family history of %')) "
        "ORDER BY id", (_num(test_id), *MIND_SOURCES)).fetchall()
    for (label,) in rows:
        text, key = _clean(label, FACTOR_MAX), item_key(label)
        if key and key not in seen:
            seen.add(key)
            out.append({"factor": text, "kind": "stress"})
    for f in record_findings or []:
        if f.get("kind") not in RECORD_KINDS:
            continue
        text = _clean(f.get("text"), FACTOR_MAX)
        key = item_key(text)
        if key and key not in seen:
            seen.add(key)
            out.append({"factor": text, "kind": "record"})
    return out


def groups(cx, test_id, records_ok=True):
    """Every group on this intake, oldest first, each with its factors.

    records_ok False hides the record factors: the address is shared now, or could not
    be checked, so a finding stored earlier may be another person's (review round 2).
    """
    ensure_schema(cx)
    out = []
    for gid, name, status, why in cx.execute(
            "SELECT id, name, status, why FROM biofield_clinical_groups WHERE test_id=? "
            "ORDER BY id", (str(test_id),)).fetchall():
        facs = [{"factor": f, "kind": k} for f, k in cx.execute(
            "SELECT factor, kind FROM biofield_clinical_group_factors WHERE group_id=? "
            "ORDER BY rowid", (gid,)).fetchall() if records_ok or k != "record"]
        out.append({"id": gid, "name": name, "status": status, "why": why,
                    "factors": facs})
    return out


def grouped_keys(cx, test_id, statuses=("proposed", "approved", "dismissed")):
    """Factor keys already in a group of one of these statuses."""
    return {item_key(f["factor"]) for g in groups(cx, test_id)
            if g["status"] in statuses for f in g["factors"]}


def ungrouped(cx, test_id, factors):
    """Factors in no item and no open proposal. A factor from a dismissed proposal is
    free to place by hand: dismissing stops it being proposed again, no more
    (review round 1)."""
    taken = grouped_keys(cx, test_id, ("proposed", "approved"))
    return [f for f in factors if item_key(f["factor"]) not in taken]


class ModelReplyError(ValueError):
    """The model's reply was not the shape asked for."""


def build_prompt(factors):
    system = (
        "You help a naturopathic practitioner build a client's Clinical Summary. "
        "You get a numbered list of the client's health factors: symptoms, conditions, "
        "lab results and medications. Group the factors that belong to one condition "
        "or one body system. Name each group in plain words, under 40 characters, "
        "such as 'Cataract and vision' or 'Liver and gallbladder'. A lab result or a "
        "medication joins the group it informs. A group may hold a single factor. "
        # Glen, 2026-10-16: family history is its own Clinical Summary item.
        "Every factor that begins 'family history of' goes in one group named "
        "'Family history'. "
        "Use each factor number at most once, and leave out a factor that fits no group. "
        'Reply with JSON only: {"groups": [{"name": "...", "factors": [1, 2], '
        '"why": "one short sentence"}]}')
    user = "\n".join(f"{i}. {f['factor']}" for i, f in enumerate(factors, 1))
    return system, user


def parse_reply(raw, factors):
    """The model's groups, kept only where they cite factors it was shown. A factor
    number used twice stays in the first group that used it."""
    # A reply of the wrong shape raises, so the open proposals it would have replaced
    # are kept (review round 2). A well-formed empty list is a real answer.
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        raise ModelReplyError("not JSON")
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ModelReplyError("no groups list")
    out, used = [], set()
    for g in data["groups"]:
        if not isinstance(g, dict):
            continue
        name = _clean(g.get("name"), NAME_MAX)
        nums = g.get("factors") if isinstance(g.get("factors"), list) else []
        picked = []
        for n in nums:
            if isinstance(n, bool) or not isinstance(n, int):
                continue
            if 1 <= n <= len(factors) and n not in used:
                used.add(n)
                picked.append(factors[n - 1])
        if name and item_key(name) and picked:
            out.append({"name": name, "why": _clean(g.get("why"), 200), "factors": picked})
    return out


def _insert(cx, test_id, name, status, why, factors):
    cur = cx.execute(
        "INSERT INTO biofield_clinical_groups(test_id,name,status,why,decided_at) "
        "VALUES (?,?,?,?,CASE WHEN ?='proposed' THEN NULL ELSE CURRENT_TIMESTAMP END)",
        (str(test_id), name, status, why, status))
    gid = cur.lastrowid
    _put_factors(cx, gid, factors)
    return gid


def _put_factors(cx, gid, factors):
    for f in factors:
        key = item_key(f["factor"])
        if key:
            cx.execute("INSERT OR IGNORE INTO biofield_clinical_group_factors"
                       "(group_id,factor_key,factor,kind) VALUES (?,?,?,?)",
                       (gid, key, f["factor"], f["kind"]))


def propose(cx, test_id, factors, complete):
    """Replace this intake's open proposals with fresh ones over every ungrouped factor.
    Approved and dismissed groups are kept, and so are their factors."""
    ensure_schema(cx)
    # The open proposals are replaced, so their factors are offered again. Nothing is
    # written until the model has answered: a write here would hold the database's
    # lock for the whole call (review round 2).
    judged = grouped_keys(cx, test_id, ("approved", "dismissed"))
    free = [f for f in factors if item_key(f["factor"]) not in judged]
    if not free:
        _drop_proposed(cx, test_id)
        return {"proposed": 0, "ungrouped": 0}
    system, user = build_prompt(free)
    found = parse_reply(complete(system, user), free)
    _drop_proposed(cx, test_id)
    for g in found:
        _insert(cx, test_id, g["name"], "proposed", g["why"], g["factors"])
    return {"proposed": len(found), "ungrouped": len(ungrouped(cx, test_id, factors))}


def _drop_proposed(cx, test_id):
    ids = [r[0] for r in cx.execute(
        "SELECT id FROM biofield_clinical_groups WHERE test_id=? AND status='proposed'",
        (str(test_id),))]
    for gid in ids:
        cx.execute("DELETE FROM biofield_clinical_group_factors WHERE group_id=?", (gid,))
        cx.execute("DELETE FROM biofield_clinical_groups WHERE id=?", (gid,))


# Glen, 2026-10-16: "I need to be able to add a factor to a stress pattern by search
# and also by typing." A typed factor lives on the item only; it is not a stress.
TYPED_MAX = 120


def _known(factors, wanted, typed_ok=False, blocked=()):
    """The wanted factor texts as factor dicts: this intake's own, and, when typed_ok,
    anything else he typed, kept as kind 'typed'. A typed text whose key is in
    `blocked` (the client's record findings) is refused, so a finding hidden on a
    shared address cannot come back as 'typed' (review round 1)."""
    by_key = {item_key(f["factor"]): f for f in factors}
    out, seen = [], set()
    for w in wanted or []:
        key = item_key(w)
        if key in by_key:
            if key not in seen:
                seen.add(key)
                out.append(by_key[key])
        elif typed_ok:
            # Capped first, then matched, so a long entry cannot pass as another factor
            # or appear twice (review round 2).
            text = _clean(w, TYPED_MAX)
            tkey = item_key(text)
            if tkey and tkey not in seen and (tkey in by_key or tkey not in blocked):
                seen.add(tkey)
                out.append(by_key.get(tkey) or {"factor": text, "kind": "typed"})
    return out


def _take_from_open(cx, test_id, keys):
    """Remove these factors from the intake's open proposals, and drop a proposal
    that is left empty."""
    for gid in [r[0] for r in cx.execute(
            "SELECT id FROM biofield_clinical_groups WHERE test_id=? AND status='proposed'",
            (str(test_id),))]:
        for key in keys:
            cx.execute("DELETE FROM biofield_clinical_group_factors "
                       "WHERE group_id=? AND factor_key=?", (gid, key))
        if not cx.execute("SELECT 1 FROM biofield_clinical_group_factors WHERE group_id=?",
                          (gid,)).fetchone():
            cx.execute("DELETE FROM biofield_clinical_groups WHERE id=?", (gid,))


def _approved_by_name(cx, test_id, name):
    key = item_key(name)
    for gid, gname in cx.execute(
            "SELECT id, name FROM biofield_clinical_groups WHERE test_id=? "
            "AND status='approved' ORDER BY id", (str(test_id),)).fetchall():
        if item_key(gname) == key:
            return gid
    return None


def canonical_name(cx, test_id, name):
    """The checklist's own label for a typed name (review round 1).

    A name that is a combination's display name means the combined row, and a name
    folded into another condition means the survivor. Accepting the typed name as it
    stands would make a second row, or a row the checklist folds away so its factors
    showed nowhere.
    """
    from dashboard.biofield_clinical_checklist import (
        _norm, aliases, display_labels, resolve_alias)
    shown = {_norm(v): k for k, v in display_labels(cx).items()}
    key = shown.get(_norm(name))
    if key:
        name = _label_for_key(cx, test_id, key) or name
    return resolve_alias(name, aliases(cx))


def _label_for_key(cx, test_id, key):
    from dashboard.biofield_clinical_checklist import _norm
    for sql, args in (
            ("SELECT to_label FROM biofield_clinical_alias WHERE to_key=?", (key,)),
            ("SELECT label FROM biofield_clinical_proposals WHERE test_id=?", (str(test_id),))):
        try:
            for (label,) in cx.execute(sql, args):
                if _norm(label) == key:
                    return label
        except Exception:
            continue
    return None


def approve(cx, test_id, factors, name, wanted, group_id=None, typed_ok=False,
            blocked=()):
    """Make `name` a Clinical Summary item holding the wanted factors.

    Glen's edits arrive here: the name as he left it, and the factors he kept ticked or
    moved in. A name that is already an approved item gains the factors, which is how
    a factor is added to an existing item. Returns the approved group id, or None.
    """
    ensure_schema(cx)
    name = _clean(canonical_name(cx, test_id, " ".join(str(name or "").split())), NAME_MAX)
    if blocked is None:            # the findings could not be read
        typed_ok, blocked = False, ()
    picked = _known(factors, wanted, typed_ok, set(blocked))
    if not item_key(name) or not picked:
        return None
    # A write, not a read, so this claims the database's lock before checking: a
    # dismissal from another tab cannot land between the check and the change.
    if group_id is not None and cx.execute(
            "UPDATE biofield_clinical_groups SET status='proposed' WHERE id=? AND "
            "test_id=? AND status='proposed'", (int(group_id), str(test_id))).rowcount != 1:
        return None
    keys = [item_key(f["factor"]) for f in picked]
    # A factor lives in one item. Taking it into this item takes it out of any other
    # approved item on this intake. A dismissed proposal keeps it, so that dismissal
    # is still remembered if the factor is later taken back out (review round 3).
    for key in keys:
        cx.execute("DELETE FROM biofield_clinical_group_factors WHERE factor_key=? AND "
                   "group_id IN (SELECT id FROM biofield_clinical_groups WHERE test_id=? "
                   "AND status='approved')", (key, str(test_id)))
    _take_from_open(cx, test_id, keys)
    # The proposal is used up. A factor he unticked in it goes back to ungrouped.
    if group_id is not None:
        cx.execute("DELETE FROM biofield_clinical_group_factors WHERE group_id=?",
                   (int(group_id),))
        cx.execute("DELETE FROM biofield_clinical_groups WHERE id=?", (int(group_id),))
    gid = _approved_by_name(cx, test_id, name)
    if gid is None:
        gid = _insert(cx, test_id, name, "approved", "", picked)
    else:
        _put_factors(cx, gid, picked)
    # An item already accepted keeps the evidence it was accepted with (review round 1).
    from dashboard.biofield_clinical_proposals import decisions
    if (decisions(cx, test_id).get(item_key(name)) or {}).get("status") != "accepted":
        # Stress wording only. A record finding's text stays in the grouping store,
        # where it is hidden if the address becomes shared (review round 3).
        said = [f["factor"] for f in picked if f["kind"] != "record"]
        n_rec = len(picked) - len(said)
        decide(cx, test_id, name, "accepted", "Grouped: " + ", ".join(
            said + ([f"{n_rec} record finding(s)"] if n_rec else [])))
    return gid


def dismiss(cx, test_id, group_id):
    """Keep a proposal as judged, so its factors are not offered again."""
    ensure_schema(cx)
    cur = cx.execute(
        "UPDATE biofield_clinical_groups SET status='dismissed', "
        "decided_at=CURRENT_TIMESTAMP WHERE id=? AND test_id=? AND status='proposed'",
        (int(group_id), str(test_id)))
    return cur.rowcount == 1


def remove_factor(cx, test_id, name, factor):
    """Take one factor back out of the item a row shows. It becomes ungrouped again.
    The row's label reaches a group stored under a name since folded into it."""
    ensure_schema(cx)
    row = item_key(canonical_name(cx, test_id, name))
    for g in groups(cx, test_id):
        if g["status"] == "approved" and item_key(canonical_name(cx, test_id, g["name"])) == row:
            cur = cx.execute("DELETE FROM biofield_clinical_group_factors WHERE group_id=? "
                             "AND factor_key=?", (g["id"], item_key(factor)))
            if cur.rowcount:
                return True
    return False


def item_factors(cx, test_id, records_ok=True):
    """{row key: [factor text, ...]} for every approved group on this intake.

    Keyed by the row the group's name lands on today, so a group whose condition was
    later folded into another shows on the survivor (review round 3)."""
    out = {}
    for g in groups(cx, test_id, records_ok):
        if g["status"] == "approved":
            key = item_key(canonical_name(cx, test_id, g["name"]))
            out.setdefault(key, []).extend(
                f["factor"] for f in g["factors"] if f["factor"] not in out.get(key, []))
    return out


def on_hidden_items(cx, test_id, records_ok=True):
    """Factors of approved items hidden from this checklist. They show on no row, so
    they are offered to place again (review round 3)."""
    from dashboard.biofield_clinical_proposals import dismissed_labels
    hidden = {item_key(canonical_name(cx, test_id, l)) for l in dismissed_labels(cx, test_id)}
    return [f for g in groups(cx, test_id, records_ok) if g["status"] == "approved"
            and item_key(canonical_name(cx, test_id, g["name"])) in hidden
            for f in g["factors"]]


def record_keys(cx, email):
    """The keys of every record finding stored for this address, in any status, both
    whole and cut to a typed factor's length, since a typed entry is cut before it is
    matched (review round 3). None when they could not be read: typing is then
    refused rather than left unguarded."""
    if not cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='client_record_findings'").fetchone():
        return set()
    try:
        rows = cx.execute("SELECT text FROM client_record_findings WHERE client_email=?",
                          (email,)).fetchall()
    except Exception:
        return None
    out = set()
    for (text,) in rows:
        out.add(item_key(text))
        out.add(item_key(_clean(text, TYPED_MAX)))
    return out - {""}
