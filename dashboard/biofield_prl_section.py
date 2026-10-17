"""PRL (Premier Research Labs) section in the Biofield Intake's E4L scan panel.

Glen, 2026-10-17: under Infoceuticals, the scan's focus areas, each with its PRL
products and the matching Functional Formulation. "Unchecked checkboxes for each
item and each group. Checking an item automatically checks that group. Add a
second checkbox for each group that checks all items in that group." And:
"unchecking all unchecks every item in the group, except any that had been
individually checked." So each tick remembers how it was made (`via`).

The focus areas and products come from the same seed and the same module the
client portal reads (data/prl_seed.json, dashboard/prl_supplement.py). The FF
beside a product passes the portal's rule: it must be sellable and not on
DO_NOT_RECOMMEND (app._prl_ff_view; tests/test_biofield_prl_section.py checks the
two agree for every seed product).

Ratings work as in the five sections: three digits, 10 as X, highest first, Glen
can retype one per client, and an unapproved rating shows only on the client it
was written for. A ticked group with nothing ticked under it raises a flag with
Glen's four choices. Two of them borrow foods or nutrients from the client's
Clinical Summary conditions that the focus area links to.
"""
import html as _html
import json
import os
import sqlite3

from dashboard import biofield_clinical_sections as cs
from dashboard import prl_supplement
from dashboard.biofield_clinical_checklist import _norm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRL_LINK = "https://truly.vip/prl"   # practitioner code 021a1a, as app.PRL_LINK
EMPTY_CHOICES = ("bare", "foods", "nutrients", "omit")
# How many items "Add dietary suggestions" and "Add nutrients" bring in, per list.
TOP_N = 3

_SEED_CX = None
_CATALOG = None


def _seed_cx():
    """The portal's PRL tables, built in memory from the seed once per process."""
    global _SEED_CX
    if _SEED_CX is None:
        cx = sqlite3.connect(":memory:", check_same_thread=False)  # raw-sqlite-ok: in-memory PRL seed
        cx.row_factory = sqlite3.Row
        prl_supplement.init_tables(cx)
        prl_supplement.sync_from_seed(
            cx, json.load(open(os.path.join(ROOT, "data", "prl_seed.json"), encoding="utf-8")))
        _SEED_CX = cx
    return _SEED_CX


def _catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = (json.load(open(os.path.join(ROOT, "data", "products.json"),
                                   encoding="utf-8")).get("products") or {})
    return _CATALOG


def _resolve(name, products):
    """A sellable slug for a formulation name, in app._resolve_remedy_slug's order:
    the title map (exact, then any case; a later record with the same title wins,
    as in app's dict), then the name index (HTML-unescaped, then aliases). Every hit
    goes past retired duplicates and must be sellable. None when nothing matches."""
    from dashboard.products import superseded_slug

    def live(slug):
        slug = superseded_slug(slug, products)
        p = products.get(slug)
        return slug if p and not p.get("inactive") else None

    name = str(name or "").strip()
    if not name:
        return None
    titles = {(p.get("pinecone_title") or p.get("name")): s
              for s, p in products.items() if isinstance(p, dict)}
    if name in titles:
        return live(titles[name])
    low = name.lower()
    for title, slug in titles.items():
        if (title or "").strip().lower() == low:
            return live(slug)
    index = {}
    for key in ("pinecone_title", "name"):
        for slug, p in products.items():
            if isinstance(p, dict) and not p.get("inactive") and p.get(key):
                index.setdefault(_html.unescape(p[key]).strip().lower(), slug)
    for slug, p in products.items():
        if isinstance(p, dict) and not p.get("inactive"):
            for alias in p.get("aliases") or []:
                if alias:
                    index.setdefault(_html.unescape(alias).strip().lower(), slug)
    hit = index.get(_html.unescape(name).strip().lower())
    return live(hit) if hit else None


def ff_view(best_ff, relation, products=None):
    """The FF shown beside a PRL product, or None. Same rule as app._prl_ff_view."""
    from dashboard.related_products import DO_NOT_RECOMMEND
    if not best_ff:
        return None
    products = products if products is not None else _catalog()
    slug = _resolve(best_ff, products)
    if not slug or slug in DO_NOT_RECOMMEND:
        return None
    name = (products[slug].get("name") or best_ff).strip()
    return {"name": name, "relation": relation or "consider", "slug": slug}


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_prl_rating (
        focus_area_id INTEGER NOT NULL, name_key TEXT NOT NULL, name TEXT NOT NULL,
        overall INTEGER, research INTEGER, uniqueness INTEGER,
        reason TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
        botanical INTEGER NOT NULL DEFAULT 0, lowers_bp INTEGER,
        reviewed INTEGER NOT NULL DEFAULT 0, origin_test TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (focus_area_id, name_key))""")
    # via: 'item' when ticked by itself, 'all' when ticked by the group's All box.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_prl_pick (
        test_id TEXT NOT NULL, focus_area_id INTEGER NOT NULL, name_key TEXT NOT NULL,
        checked INTEGER NOT NULL DEFAULT 0, via TEXT NOT NULL DEFAULT '',
        overall INTEGER, research INTEGER, uniqueness INTEGER, note TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, focus_area_id, name_key))""")
    # direct: the group box itself was ticked, so it stays ticked with no items.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_prl_group (
        test_id TEXT NOT NULL, focus_area_id INTEGER NOT NULL,
        checked INTEGER NOT NULL DEFAULT 0, direct INTEGER NOT NULL DEFAULT 0,
        all_on INTEGER NOT NULL DEFAULT 0, empty_choice TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, focus_area_id))""")
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_prl_condition_map (
        focus_area_id INTEGER PRIMARY KEY, condition_keys TEXT NOT NULL DEFAULT '[]',
        display_name TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # The condition rows the author page last showed for a test, so the scan panel
    # (fetched separately) knows the client's conditions without a second profile call.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_rows (
        test_id TEXT PRIMARY KEY, labels TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")


def remember_rows(cx, test_id, labels):
    ensure_schema(cx)
    cx.execute("INSERT INTO biofield_clinical_rows (test_id,labels,updated_at) "
               "VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(test_id) DO UPDATE SET "
               "labels=excluded.labels,updated_at=CURRENT_TIMESTAMP",
               (str(test_id), json.dumps([str(x) for x in labels or [] if x])))
    cx.commit()


def _rows(cx, test_id):
    row = cx.execute("SELECT labels FROM biofield_clinical_rows WHERE test_id=?",
                     (str(test_id),)).fetchone()
    try:
        return json.loads(row[0]) if row else []
    except ValueError:
        return []


def upsert_rating(cx, focus_area_id, item, origin_test=""):
    ensure_schema(cx)
    name = str(item.get("name") or "").strip()[:200]
    if not name:
        raise ValueError("a product name is required")
    lowers = item.get("lowers_bp")
    exists = cx.execute("SELECT 1 FROM biofield_prl_rating WHERE focus_area_id=? AND name_key=?",
                        (int(focus_area_id), _norm(name))).fetchone()
    cx.execute("""INSERT INTO biofield_prl_rating
        (focus_area_id,name_key,name,overall,research,uniqueness,reason,source,
         botanical,lowers_bp,reviewed,origin_test,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,0,?,CURRENT_TIMESTAMP)
        ON CONFLICT(focus_area_id,name_key) DO UPDATE SET name=excluded.name,
          overall=excluded.overall,research=excluded.research,uniqueness=excluded.uniqueness,
          reason=excluded.reason,source=excluded.source,botanical=excluded.botanical,
          lowers_bp=excluded.lowers_bp,updated_at=CURRENT_TIMESTAMP""",
               (int(focus_area_id), _norm(name), name, cs._score(item.get("overall")),
                cs._score(item.get("research")), cs._score(item.get("uniqueness")),
                str(item.get("reason") or "")[:400], str(item.get("source") or "")[:600],
                1 if item.get("botanical") else 0,
                None if lowers is None else (1 if lowers else 0), str(origin_test or "")))
    return "updated" if exists else "added"


def save_condition_map(cx, focus_area_id, condition_keys, display_name=""):
    """Which Clinical Summary conditions a focus area draws on, and the name our
    pages show for it when it differs from E4L's (Glen, 2026-10-17: area 23 shows
    as "Ear / Hearing / Audio Processing"). E4L's name stays the join key."""
    ensure_schema(cx)
    keys = [_norm(k) for k in condition_keys or [] if _norm(k)]
    cx.execute("INSERT INTO biofield_prl_condition_map "
               "(focus_area_id,condition_keys,display_name) VALUES (?,?,?) "
               "ON CONFLICT(focus_area_id) DO UPDATE SET condition_keys=excluded.condition_keys,"
               "display_name=excluded.display_name,updated_at=CURRENT_TIMESTAMP",
               (int(focus_area_id), json.dumps(keys), str(display_name or "").strip()[:120]))


def approve_rating(cx, focus_area_id, name):
    ensure_schema(cx)
    cur = cx.execute("UPDATE biofield_prl_rating SET reviewed=1,updated_at=CURRENT_TIMESTAMP "
                     "WHERE focus_area_id=? AND name_key=?", (int(focus_area_id), _norm(name)))
    cx.commit()
    return bool(cur.rowcount)


def _group(cx, test_id, fa):
    row = cx.execute("SELECT checked,direct,all_on,empty_choice FROM biofield_prl_group "
                     "WHERE test_id=? AND focus_area_id=?", (str(test_id), int(fa))).fetchone()
    return {"checked": bool(row and row[0]), "direct": bool(row and row[1]),
            "all_on": bool(row and row[2]), "empty_choice": (row[3] if row else "") or ""}


def _set_group(cx, test_id, fa, **fields):
    cx.execute("INSERT INTO biofield_prl_group (test_id,focus_area_id) VALUES (?,?) "
               "ON CONFLICT(test_id,focus_area_id) DO NOTHING", (str(test_id), int(fa)))
    for key, value in fields.items():
        cx.execute(f"UPDATE biofield_prl_group SET {key}=?,updated_at=CURRENT_TIMESTAMP "
                   "WHERE test_id=? AND focus_area_id=?", (value, str(test_id), int(fa)))


def _set_pick(cx, test_id, fa, name, **fields):
    cx.execute("INSERT INTO biofield_prl_pick (test_id,focus_area_id,name_key) VALUES (?,?,?) "
               "ON CONFLICT(test_id,focus_area_id,name_key) DO NOTHING",
               (str(test_id), int(fa), _norm(name)))
    for key, value in fields.items():
        cx.execute(f"UPDATE biofield_prl_pick SET {key}=?,updated_at=CURRENT_TIMESTAMP "
                   "WHERE test_id=? AND focus_area_id=? AND name_key=?",
                   (value, str(test_id), int(fa), _norm(name)))


def _ticked(cx, test_id, fa):
    return {r[0]: r[1] for r in cx.execute(
        "SELECT name_key,via FROM biofield_prl_pick WHERE test_id=? AND focus_area_id=? "
        "AND checked=1", (str(test_id), int(fa)))}


def products_for(fa):
    return prl_supplement.products_for_focus_area(_seed_cx(), int(fa))


def act(cx, test_id, fa, action, name=None, value=None):
    """One checkbox or field change. Actions: item_on, item_off, group_on, group_off,
    all_on, all_off, rating, empty_choice."""
    ensure_schema(cx)
    fa = int(fa)
    names = [p["name"] for p in products_for(fa)]
    if not names:
        raise ValueError("no PRL products for that focus area")
    if action in ("item_on", "item_off", "rating") and _norm(name) not in {_norm(n) for n in names}:
        raise ValueError("that product is not in this focus area")
    if action == "item_on":
        _set_pick(cx, test_id, fa, name, checked=1, via="item")
        _set_group(cx, test_id, fa, checked=1)
    elif action == "item_off":
        _set_pick(cx, test_id, fa, name, checked=0, via="")
        # The group no longer has every item; one ticked only by items unticks with
        # the last of them (the rule Glen asked for on Clinical Summary, 2026-10-16).
        _set_group(cx, test_id, fa, all_on=0)
        if not _ticked(cx, test_id, fa) and not _group(cx, test_id, fa)["direct"]:
            _set_group(cx, test_id, fa, checked=0)
    elif action == "group_on":
        _set_group(cx, test_id, fa, checked=1, direct=1)
    elif action == "group_off":
        for n in names:
            _set_pick(cx, test_id, fa, n, checked=0, via="")
        _set_group(cx, test_id, fa, checked=0, direct=0, all_on=0)
    elif action == "all_on":
        ticked = _ticked(cx, test_id, fa)
        for n in names:
            if _norm(n) not in ticked:
                _set_pick(cx, test_id, fa, n, checked=1, via="all")
        _set_group(cx, test_id, fa, checked=1, all_on=1)
    elif action == "all_off":
        # Glen: "unchecking all unchecks every item in the group, except any that had
        # been individually checked."
        for key, via in _ticked(cx, test_id, fa).items():
            if via == "all":
                cx.execute("UPDATE biofield_prl_pick SET checked=0,via='',"
                           "updated_at=CURRENT_TIMESTAMP WHERE test_id=? AND "
                           "focus_area_id=? AND name_key=?", (str(test_id), fa, key))
        _set_group(cx, test_id, fa, all_on=0)
        if not _ticked(cx, test_id, fa) and not _group(cx, test_id, fa)["direct"]:
            _set_group(cx, test_id, fa, checked=0)
    elif action == "rating":
        scores = cs.parse_rating(value)
        if scores is None:
            raise ValueError("a rating is three of 1-9 or X, such as X74")
        _set_pick(cx, test_id, fa, name, overall=scores[0], research=scores[1],
                  uniqueness=scores[2])
    elif action == "empty_choice":
        if value not in EMPTY_CHOICES + ("",):
            raise ValueError("unknown choice")
        _set_group(cx, test_id, fa, empty_choice=value)
    else:
        raise ValueError(f"unknown action {action!r}")
    cx.commit()


def _linked_conditions(cx, test_id, fa):
    row = cx.execute("SELECT condition_keys FROM biofield_prl_condition_map "
                     "WHERE focus_area_id=?", (int(fa),)).fetchone()
    try:
        keys = json.loads(row[0]) if row else []
    except ValueError:
        keys = []
    mine = {_norm(label): label for label in _rows(cx, test_id)}
    return [mine[k] for k in keys if k in mine]


def _top(cx, test_id, labels, section, botanical):
    seen, out = set(), []
    for label in labels:
        for sec in cs.sections_for(cx, test_id, label, botanical=botanical):
            if sec["key"] == section:
                for item in sec["items"]:
                    if _norm(item["name"]) not in seen:
                        seen.add(_norm(item["name"]))
                        out.append(dict(item, condition=label))
    return cs.sort_items(out)[:TOP_N]


def view(cx, test_id, item_codes):
    """The focus areas the scan's items hit, in hit order, each with its products,
    their FF, ratings and ticks. Areas with no PRL product are left out, as on
    the portal."""
    ensure_schema(cx)
    cs.ensure_schema(cx)
    test_id = str(test_id)
    botanical = cs.botanical_setting(cx, test_id)
    exceptions = cs._exception_keys(botanical.get("exceptions"))
    areas = []
    for area in prl_supplement.focus_areas_for_items(_seed_cx(), list(item_codes or [])):
        fa = int(area["focus_area_id"])
        products = products_for(fa)
        if not products:
            continue
        ratings = {r[0]: r for r in cx.execute(
            "SELECT name_key,overall,research,uniqueness,reason,source,botanical,lowers_bp,"
            "reviewed,origin_test FROM biofield_prl_rating WHERE focus_area_id=?", (fa,))}
        picks = {r[0]: r for r in cx.execute(
            "SELECT name_key,checked,via,overall,research,uniqueness,note "
            "FROM biofield_prl_pick WHERE test_id=? AND focus_area_id=?", (test_id, fa))}
        group = _group(cx, test_id, fa)
        items, hidden = [], 0
        for p in products:
            key = _norm(p["name"])
            r = ratings.get(key)
            if r and not (r[8] or r[9] in ("", test_id)):
                r = None   # unapproved and written for another client
            pick = picks.get(key)
            scores = (r[1], r[2], r[3]) if r else (None, None, None)
            if pick and pick[3] is not None:
                scores = (pick[3], pick[4], pick[5])
            item = {"name": p["name"], "url": p.get("url") or "",
                    "ff": ff_view(p.get("best_ff"), p.get("relation")),
                    "overall": scores[0], "research": scores[1], "uniqueness": scores[2],
                    "rating": cs.rating_code(*scores),
                    "reason": r[4] if r else "", "source": r[5] if r else "",
                    "botanical": bool(r and r[6]), "lowers_bp": bool(r and r[7]),
                    "reviewed": bool(r[8]) if r else True, "rated": bool(r),
                    "checked": bool(pick and pick[1]), "via": (pick[2] if pick else "") or "",
                    "note": (pick[6] if pick else "") or ""}
            if botanical.get("avoid") and item["botanical"] and not item["checked"] \
                    and not cs._allowed_botanical(item["name"], exceptions):
                hidden += 1
                continue
            items.append(item)
        shown = cx.execute("SELECT display_name FROM biofield_prl_condition_map "
                           "WHERE focus_area_id=?", (fa,)).fetchone()
        entry = {"focus_area_id": fa,
                 "name": (shown[0] if shown and shown[0] else area["focus_area_name"]),
                 "hits": area["hits"],
                 "items": cs.sort_items(items), "botanical_hidden": hidden, **group}
        entry["empty"] = group["checked"] and not any(i["checked"] for i in items)
        if entry["empty"]:
            linked = _linked_conditions(cx, test_id, fa)
            entry["linked"] = linked
            entry["foods"] = {
                "emphasize": _top(cx, test_id, linked, "foods_emphasize", botanical),
                "avoid": _top(cx, test_id, linked, "foods_avoid", botanical)}
            entry["nutrients"] = _top(cx, test_id, linked, cs.NUTRIENTS, botanical)
        areas.append(entry)
    return {"areas": areas, "prl_link": PRL_LINK}


def scan_item_codes(e4l_db, scan_id):
    """Every item code on one E4L scan, bare ("ED12", not "ED12 - Pancreas")."""
    if not scan_id or not e4l_db or not os.path.exists(e4l_db):
        return []
    cx = sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)  # raw-sqlite-ok: e4l.db, read only
    try:
        rows = cx.execute("SELECT item_code FROM e4l_scan_results WHERE scan_id=? "
                          "ORDER BY priority_rank", (scan_id,)).fetchall()
    finally:
        cx.close()
    return [str(r[0]).split(" - ")[0].strip() for r in rows if r[0]]
