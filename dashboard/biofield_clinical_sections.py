"""Six rated sections under each Clinical Summary condition.

Spec: vault clinical/specs/2026-10-17-clinical-summary-five-sections.md (Glen, 17 Oct).

Every condition row carries Functional Formulations, Infoceuticals, Foods to
emphasize, Foods to avoid, Nutrients and Fullscript supplements. The shared content lives in
biofield_clinical_section_item, one row per (condition, section, item). What one
client has ticked, and any score or dose Glen edits for that client, lives in
biofield_clinical_section_pick.

Functional Formulations is the row's existing remedy list. Its ticks stay in
biofield_clinical_selection.remedies, the store the causal chain and the program
already read, so this module only adds ratings to it and never a second tick store.

Three rules Glen approved the same day ("all three"):
- An item he has not approved shows "not reviewed", and shows only on the client it
  was added for (origin_test) until he approves it into the shared catalog.
- An avoids-botanicals switch per client hides botanical items, apart from the
  client's named exceptions and anything already ticked.
- A food one checked condition emphasizes and another checked condition avoids is
  flagged on both.

Each section also has an "All" box (Glen, 2026-10-17): "Check the box and all the
items in that group are added. Uncheck, and only the ones individually checked are
added. Unchecking doesn't remove checks from those checked manually." The same rule
as the PRL All box, so each tick remembers how it was made (`via`).
"""
import os
import re

from dashboard.biofield_clinical_checklist import _norm, forgotten_remedies


FORMULATIONS = "functional_formulations"
INFOCEUTICALS = "infoceuticals"
FULLSCRIPT = "fullscript"
NUTRIENTS = "nutrients"
BOTANICALS = "botanicals"
HEALING_TOOLS = "healing_tools"
ESSENTIAL_OILS = "essential_oils"
SECTIONS = (
    (FORMULATIONS, "Functional Formulations"),
    (INFOCEUTICALS, "Infoceuticals"),
    ("foods_emphasize", "Foods to emphasize"),
    ("foods_avoid", "Foods to avoid"),
    (NUTRIENTS, "Nutrients"),
    # Glen, 2026-10-18: "yes, a Botanicals section beside Nutrients". Nutrients hold
    # vitamins, minerals, fats and amino acids; botanical ingredients go here.
    (BOTANICALS, "Botanicals"),
    # Glen, 2026-10-18: an Essential Oils section, printed under Remedies after
    # Botanicals. Each oil carries its route, "inhale and skin" or "inhale only".
    (ESSENTIAL_OILS, "Essential Oils"),
    # Glen, 2026-10-18: devices and therapies such as Kloud, Molecular Hydrogen,
    # Microwater, Photobiomodulation and Microcurrent. Not botanicals.
    (HEALING_TOOLS, "Healing Tools"),
    (FULLSCRIPT, "Fullscript supplements"),
)
SECTION_KEYS = tuple(key for key, _ in SECTIONS)
SCORES = ("overall", "research", "uniqueness")
# Both are the row's remedy list: their ticks live in biofield_clinical_selection.
REMEDY_SECTIONS = (FORMULATIONS, INFOCEUTICALS)
# Sections whose items carry an editable dose; a healing tool's is its usage line.
DOSED_SECTIONS = (NUTRIENTS, BOTANICALS, ESSENTIAL_OILS, HEALING_TOOLS)

# Glen, 2026-10-17: Infoceuticals get a section of their own, apart from Functional
# Formulations. MRs are not infoceuticals (Glen, same day), and neither are ERs.
_INFOCEUTICAL_CODE = re.compile(r"^((ED|EI|ES|ET|MB)\d+|BFA)\b", re.I)
_NOT_INFOCEUTICAL = re.compile(r"^(MR|ER)\d+\b", re.I)
# Only a real E4L infoceutical code: "NES Infoceutical ..." has none, not "NES".
_CODE = re.compile(r"^((ED|EI|ES|ET|MB)\d+|BFA|ESR)\b")


def is_infoceutical(name):
    name = str(name or "").strip()
    if _NOT_INFOCEUTICAL.match(name):
        return False
    return bool(_INFOCEUTICAL_CODE.match(name)) or \
        bool(re.search(r"\binfoceuticals?\b", name, re.I))


def infoceutical_code(name):
    """"EI10 Circulation/... Infoceutical" -> "EI10"; "ESR Emotional ..." -> "ESR"."""
    m = _CODE.match(str(name or "").strip().upper())
    return m.group(1) if m else ""


def fullscript_store_url():
    """The store front door, never a product page. Same rule and same config as
    app._fullscript_dispensary_url: attribution of a new sign-up through a product
    link is unconfirmed, so every Fullscript item links here."""
    slug = (os.environ.get("FULLSCRIPT_DISPENSARY_SLUG", "") or "remedymatch").strip()
    return f"https://us.fullscript.com/welcome/{slug}/store-start"


def ensure_schema(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_section_item (
        item_key TEXT NOT NULL, section TEXT NOT NULL,
        name_key TEXT NOT NULL, name TEXT NOT NULL,
        brand TEXT NOT NULL DEFAULT '', dose TEXT NOT NULL DEFAULT '',
        overall INTEGER, research INTEGER, uniqueness INTEGER,
        reason TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
        botanical INTEGER NOT NULL DEFAULT 0, lowers_bp INTEGER,
        reviewed INTEGER NOT NULL DEFAULT 0,
        origin_test TEXT NOT NULL DEFAULT '',
        food_keys TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (item_key, section, name_key)
    )""")
    cols = {row[1] for row in
            cx.execute("PRAGMA table_info(biofield_clinical_section_item)").fetchall()}
    for col in ("origin_test", "food_keys", "explanation", "route", "route_reason"):
        if col not in cols:
            cx.execute(f"ALTER TABLE biofield_clinical_section_item "
                       f"ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    # The Plan report's paragraph for the item, written once by clinical and approved
    # once by Glen for every client (Glen, 2026-10-18). Unapproved, it never prints.
    if "explanation_approved" not in cols:
        cx.execute("ALTER TABLE biofield_clinical_section_item "
                   "ADD COLUMN explanation_approved INTEGER NOT NULL DEFAULT 0")
    # A botanical the switch greys out, unhidden by hand for this client only, in one
    # condition's section (Glen, 2026-10-18: "per client and reversible").
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_unhide (
        test_id TEXT NOT NULL, item_key TEXT NOT NULL, section TEXT NOT NULL,
        name_key TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, item_key, section, name_key)
    )""")
    # An explanation for a ticked shared-list remedy that is not an item in the
    # condition's file (clinical's `extra_explanations`, by name). Approved only.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_extra_explanation (
        item_key TEXT NOT NULL, section TEXT NOT NULL, name_key TEXT NOT NULL,
        name TEXT NOT NULL, explanation TEXT NOT NULL,
        PRIMARY KEY (item_key, section, name_key)
    )""")
    # The second tick column on Infoceuticals (Glen, 2026-10-18): recommend this
    # infoceutical whenever it shows purple on a scan. Shared by every client, for that
    # condition and infoceutical. code is the E4L code, so a respelled name still matches.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_purple_watch (
        item_key TEXT NOT NULL, name_key TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '', set_on_test TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (item_key, name_key)
    )""")
    # Each purple infoceutical the page ticked by itself on one scan (Glen, 2026-10-18:
    # "tick purple infoceuticals automatically on every new scan"). Recorded once, so a
    # tick taken off by hand stays off. added is 0 when it was already ticked.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_autotick (
        test_id TEXT NOT NULL, scan_id TEXT NOT NULL, item_key TEXT NOT NULL,
        name_key TEXT NOT NULL, added INTEGER NOT NULL DEFAULT 0,
        ticked_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, scan_id, item_key, name_key)
    )""")
    # A condition row whose saved selection an auto tick created, from nothing ticked.
    # Emptied again by hand, the row is removed, so the Plan does not list it.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_autotick_row (
        test_id TEXT NOT NULL, item_key TEXT NOT NULL,
        PRIMARY KEY (test_id, item_key)
    )""")
    # Whether a section's All box is ticked, per client and condition.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_section_all (
        test_id TEXT NOT NULL, item_key TEXT NOT NULL, section TEXT NOT NULL,
        all_on INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, item_key, section)
    )""")
    # One row per client test: is the switch on, and which botanicals are allowed.
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_botanical (
        test_id TEXT PRIMARY KEY, avoid INTEGER NOT NULL DEFAULT 0,
        exceptions TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # checked is NULL for Functional Formulations: their ticks live in
    # biofield_clinical_selection. A NULL score or dose means "use the catalog's".
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_clinical_section_pick (
        test_id TEXT NOT NULL, item_key TEXT NOT NULL, section TEXT NOT NULL,
        name_key TEXT NOT NULL, checked INTEGER,
        overall INTEGER, research INTEGER, uniqueness INTEGER,
        dose TEXT, note TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_id, item_key, section, name_key)
    )""")
    # via: 'all' when the section's All box ticked it, '' otherwise. For Functional
    # Formulations the tick itself is in biofield_clinical_selection; this row only
    # remembers which of those remedies the All box added.
    pick_cols = {row[1] for row in
                 cx.execute("PRAGMA table_info(biofield_clinical_section_pick)").fetchall()}
    if "via" not in pick_cols:
        cx.execute("ALTER TABLE biofield_clinical_section_pick "
                   "ADD COLUMN via TEXT NOT NULL DEFAULT ''")


def _score(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 10 else None


def rating_code(overall, research, uniqueness):
    """Three digits, 10 written as X: (10, 7, 4) -> "X74". Blank unless all three exist."""
    scores = [_score(v) for v in (overall, research, uniqueness)]
    if None in scores:
        return ""
    return "".join("X" if s == 10 else str(s) for s in scores)


def parse_rating(code):
    """"X74" -> (10, 7, 4). Anything that is not three of 1-9 or X gives None."""
    code = str(code or "").strip().upper()
    if len(code) != 3:
        return None
    out = []
    for ch in code:
        if ch == "X":
            out.append(10)
        elif ch in "123456789":
            out.append(int(ch))
        else:
            return None
    return tuple(out)


def sort_items(items):
    """Highest rating first: Overall, then Research, then Uniqueness. X counts as 10.
    Unrated items follow the rated ones in the order they arrived."""
    def key(pair):
        index, item = pair
        scores = [_score(item.get(s)) for s in SCORES]
        if None in scores:
            return (1, 0, 0, 0, index)
        return (0, -scores[0], -scores[1], -scores[2], index)
    return [item for _, item in sorted(enumerate(items), key=key)]


def upsert_item(cx, label, section, item, origin_test=""):
    """Write one shared catalog item. Returns "added" or "updated".

    A new item records the client it was added for; until Glen approves it, only
    that client sees it. Updating an item never changes its origin or its review."""
    ensure_schema(cx)
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown section {section!r}")
    item_key, name = _norm(label), str(item.get("name") or "").strip()[:160]
    name_key = _norm(name)
    if not item_key or not name_key:
        raise ValueError("a condition and an item name are required")
    exists = cx.execute(
        "SELECT 1 FROM biofield_clinical_section_item "
        "WHERE item_key=? AND section=? AND name_key=?",
        (item_key, section, name_key)).fetchone()
    lowers_bp = item.get("lowers_bp")
    if exists and ("explanation" not in item or "explanation_approved" not in item):
        # A field the file leaves out (an older or partial file) keeps what is stored;
        # a field it carries changes it, so a file can withdraw an approval alone.
        text, approved = cx.execute(
            "SELECT explanation,explanation_approved FROM biofield_clinical_section_item "
            "WHERE item_key=? AND section=? AND name_key=?",
            (item_key, section, name_key)).fetchone()
        item = dict(item)
        item.setdefault("explanation", text)
        item.setdefault("explanation_approved", approved == 1)
    cx.execute("""INSERT INTO biofield_clinical_section_item
        (item_key,section,name_key,name,brand,dose,overall,research,uniqueness,
         reason,source,botanical,lowers_bp,reviewed,origin_test,food_keys,explanation,
         explanation_approved,route,route_reason,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(item_key,section,name_key) DO UPDATE SET
          name=excluded.name,brand=excluded.brand,dose=excluded.dose,
          overall=excluded.overall,research=excluded.research,
          uniqueness=excluded.uniqueness,reason=excluded.reason,
          source=excluded.source,botanical=excluded.botanical,
          lowers_bp=excluded.lowers_bp,food_keys=excluded.food_keys,
          explanation=excluded.explanation,
          explanation_approved=excluded.explanation_approved,
          route=excluded.route,route_reason=excluded.route_reason,
          updated_at=CURRENT_TIMESTAMP""",
               (item_key, section, name_key, name,
                str(item.get("brand") or "").strip()[:160],
                str(item.get("dose") or "").strip()[:200] if section in DOSED_SECTIONS else "",
                _score(item.get("overall")), _score(item.get("research")),
                _score(item.get("uniqueness")),
                str(item.get("reason") or "").strip()[:400],
                str(item.get("source") or "").strip()[:600],
                1 if item.get("botanical") else 0,
                None if lowers_bp is None else (1 if lowers_bp else 0),
                1 if item.get("reviewed") else 0, str(origin_test or ""),
                ",".join(_food_keys(item.get("food_keys"))),
                str(item.get("explanation") or "").strip()[:1200],
                1 if item.get("explanation_approved") is True else 0,
                str(item.get("route") or "").strip()[:40],
                str(item.get("route_reason") or "").strip()[:400]))
    return "updated" if exists else "added"


def _food_keys(value):
    """The plain foods an item covers, e.g. ["black tea", "green tea"]. Content
    names are phrases ("Black or green tea with meals"), so conflicts match on
    these rather than on the whole name."""
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for raw in value or []:
        key = _norm(raw)
        if key and key not in out:
            out.append(key)
    return out


def set_extra_explanations(cx, label, extras):
    """Replace one condition's stored extra explanations with the approved entries of
    `extras` ({name: {section, explanation, explanation_approved}}). Returns
    (applied, waiting). The caller commits."""
    ensure_schema(cx)
    if extras is None:
        return 0, 0   # a file without the key leaves what is stored
    item_key = _norm(label)
    cx.execute("DELETE FROM biofield_clinical_extra_explanation WHERE item_key=?",
               (item_key,))
    applied = waiting = 0
    for name, entry in (extras or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        text = str(entry.get("explanation") or "").strip()
        section = entry.get("section") or FORMULATIONS
        if entry.get("explanation_approved") is not True or not text \
                or section not in SECTION_KEYS:
            waiting += 1
            continue
        cx.execute("INSERT OR REPLACE INTO biofield_clinical_extra_explanation "
                   "VALUES (?,?,?,?,?)",
                   (item_key, section, _norm(name), str(name).strip(), text[:1200]))
        applied += 1
    return applied, waiting


def approve_item(cx, label, section, name):
    """Glen's approval: the item joins the shared catalog for every client."""
    ensure_schema(cx)
    cur = cx.execute("UPDATE biofield_clinical_section_item SET reviewed=1,"
                     "updated_at=CURRENT_TIMESTAMP WHERE item_key=? AND section=? "
                     "AND name_key=?", (_norm(label), section, _norm(name)))
    if not cur.rowcount and section == INFOCEUTICALS:
        # Loaded under Functional Formulations before Infoceuticals had a section.
        cur = cx.execute("UPDATE biofield_clinical_section_item SET reviewed=1,"
                         "updated_at=CURRENT_TIMESTAMP WHERE item_key=? AND section=? "
                         "AND name_key=?", (_norm(label), FORMULATIONS, _norm(name)))
    cx.commit()
    return bool(cur.rowcount)


def botanical_setting(cx, test_id):
    ensure_schema(cx)
    row = cx.execute("SELECT avoid,exceptions FROM biofield_clinical_botanical "
                     "WHERE test_id=?", (str(test_id),)).fetchone()
    return {"avoid": bool(row and row[0]), "exceptions": (row[1] if row else "") or ""}


def save_botanical_setting(cx, test_id, avoid=None, exceptions=None):
    ensure_schema(cx)
    cx.execute("INSERT INTO biofield_clinical_botanical (test_id) VALUES (?) "
               "ON CONFLICT(test_id) DO NOTHING", (str(test_id),))
    if avoid is not None:
        cx.execute("UPDATE biofield_clinical_botanical SET avoid=?,"
                   "updated_at=CURRENT_TIMESTAMP WHERE test_id=?",
                   (1 if avoid else 0, str(test_id)))
    if exceptions is not None:
        cx.execute("UPDATE biofield_clinical_botanical SET exceptions=?,"
                   "updated_at=CURRENT_TIMESTAMP WHERE test_id=?",
                   (str(exceptions).strip()[:300], str(test_id)))
    cx.commit()


def _exception_keys(text):
    return [k for k in (_norm(part) for part in str(text or "").split(",")) if k]


def _allowed_botanical(name, exception_keys):
    # "ginger" allows "Gingerol" and "Ginger root"; "milk thistle" allows
    # "Milk thistle seed". Matched on the item's name, the only thing on the row.
    key = _norm(name)
    return any(e in key for e in exception_keys)


def set_unhidden(cx, test_id, label, section, name, on):
    """Unhide (on) or hide again one greyed botanical for this client, in one
    condition's section. Hiding it again also unticks it, as the switch would."""
    ensure_schema(cx)
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown section {section!r}")
    item_key, name_key = _norm(label), _norm(name)
    if not item_key or not name_key:
        raise ValueError("a condition and an item name are required")
    args = (str(test_id), item_key, section, name_key)
    if on:
        cx.execute("INSERT OR IGNORE INTO biofield_clinical_unhide "
                   "(test_id,item_key,section,name_key) VALUES (?,?,?,?)", args)
    else:
        cx.execute("DELETE FROM biofield_clinical_unhide WHERE test_id=? AND item_key=? "
                   "AND section=? AND name_key=?", args)
        if section not in REMEDY_SECTIONS:
            cx.execute("UPDATE biofield_clinical_section_pick SET checked=0,via='' "
                       "WHERE test_id=? AND item_key=? AND section=? AND name_key=?", args)
    cx.commit()


def set_watched(cx, label, name, on, test_id=""):
    """Tick (on) or untick one infoceutical's purple watch in one condition, for every
    client. test_id only records which page it was set from."""
    ensure_schema(cx)
    item_key, name_key = _norm(label), _norm(name)
    if not item_key or not name_key:
        raise ValueError("a condition and an item name are required")
    if on:
        cx.execute("INSERT OR REPLACE INTO biofield_clinical_purple_watch "
                   "(item_key,name_key,code,name,set_on_test) VALUES (?,?,?,?,?)",
                   (item_key, name_key, infoceutical_code(name) or "", str(name).strip(),
                    str(test_id)))
    else:
        code = infoceutical_code(name) or ""
        cx.execute("DELETE FROM biofield_clinical_purple_watch WHERE item_key=? "
                   "AND (name_key=? OR (code<>'' AND code=?))", (item_key, name_key, code))
    cx.commit()


def _watched(cx, item_key):
    """The watched infoceuticals of one condition, as (name keys, codes)."""
    rows = cx.execute("SELECT name_key,code FROM biofield_clinical_purple_watch "
                      "WHERE item_key=?", (item_key,)).fetchall()
    return {r[0] for r in rows}, {r[1].upper() for r in rows if r[1]}


# Glen, 2026-10-18: "only scans dated from today". Older scans are never auto-ticked.
AUTOTICK_FROM = "2026-10-18"


def _autoticked(cx, test_id, scan_id, item_key):
    """{name_key: added} for what the page handled by itself on this scan."""
    return {r[0]: bool(r[1]) for r in cx.execute(
        "SELECT name_key,added FROM biofield_clinical_autotick WHERE test_id=? "
        "AND scan_id=? AND item_key=?", (str(test_id), str(scan_id), item_key))}


def _auto_added(cx, test_id, item_key):
    """The names the page itself ticked on any scan, and no hand save has touched
    since. Its tag stays while that tick does, scan after scan."""
    return {r[0] for r in cx.execute(
        "SELECT DISTINCT name_key FROM biofield_clinical_autotick WHERE test_id=? "
        "AND item_key=? AND added=1", (str(test_id), item_key))}


def _autotick_date_ok(scan_date):
    """True only for a real YYYY-MM-DD date on or after AUTOTICK_FROM."""
    import datetime
    try:
        day = datetime.date.fromisoformat(str(scan_date or "").strip()[:10])
    except ValueError:
        return False
    return day >= datetime.date.fromisoformat(AUTOTICK_FROM)


def row_ticks(item):
    """The remedies a checklist row shows ticked, as the page draws them: the saved
    set, or else the chain remedy that covers it."""
    if item.get("selection_saved"):
        return list(item.get("selected_remedies") or [])
    covered = str(item.get("covered_by") or "").strip()
    return [covered] if covered else []


def auto_tick_purple(cx, test_id, label, sections, chosen, scan_id, scan_date):
    """Tick each infoceutical that is purple on this scan, in one condition row.

    Only a scan dated AUTOTICK_FROM or later. Only an entry whose purple is True, so
    unread colours tick nothing. Each is recorded once per scan: one unticked by hand
    is never ticked again, and nothing is ever unticked here. `chosen` is the row's
    ticks as the page derived them; a saved selection read now wins over it. Returns
    the names added. It never commits: the caller commits the whole page at once."""
    if not scan_id or not _autotick_date_ok(scan_date):
        return []
    ensure_schema(cx)
    from dashboard.biofield_clinical_proposals import _key, save_selection, selections
    item_key = _norm(label)
    saved = selections(cx, test_id)
    current = saved.get(_key(label), list(chosen or []))
    current_keys = {_norm(n) for n in current}
    # One infoceutical spelled two ways is still one: purple is read by its code.
    current_codes = {(infoceutical_code(n) or "").upper() for n in current} - {""}
    info = next((s for s in sections or [] if s.get("key") == INFOCEUTICALS), None)
    added = []
    for entry in (info or {}).get("items") or []:
        key = _norm(entry.get("name"))
        if entry.get("purple") is not True or entry.get("hidden"):
            continue
        code = (entry.get("code") or "").upper()
        ticked = key in current_keys or bool(code and code in current_codes)
        # The row claims the item for this scan. A row already there means it was
        # handled before, by this page or another tab, so it is left as it is.
        claimed = cx.execute(
            "INSERT OR IGNORE INTO biofield_clinical_autotick "
            "(test_id,scan_id,item_key,name_key,added) VALUES (?,?,?,?,?)",
            (str(test_id), str(scan_id), item_key, key, 0 if ticked else 1)).rowcount
        if claimed and not ticked:
            added.append(entry["name"])
            current_keys.add(key)
            if code:
                current_codes.add(code)
    if added:
        if _key(label) not in saved and not current:
            cx.execute("INSERT OR IGNORE INTO biofield_clinical_autotick_row "
                       "(test_id,item_key) VALUES (?,?)", (str(test_id), item_key))
        save_selection(cx, test_id, label, list(current) + added)
    return added


def clear_auto_marks(cx, test_id, label, before, after):
    """A hand save changed these remedies, so none of them is the page's own tick
    any more: one ticked again by hand must not read "auto"."""
    ensure_schema(cx)
    before_keys = {_norm(r) for r in before or []}
    after_keys = {_norm(r) for r in after or []}
    for key in before_keys ^ after_keys:
        cx.execute("UPDATE biofield_clinical_autotick SET added=0 WHERE test_id=? "
                   "AND item_key=? AND name_key=?", (str(test_id), _norm(label), key))
    if not after_keys and cx.execute(
            "DELETE FROM biofield_clinical_autotick_row WHERE test_id=? AND item_key=?",
            (str(test_id), _norm(label))).rowcount:
        # Nothing was ticked before the auto tick made this row: emptied, it goes.
        from dashboard.biofield_clinical_proposals import _key
        cx.execute("DELETE FROM biofield_clinical_selection WHERE test_id=? AND item_key=?",
                   (str(test_id), _key(label)))
    elif after_keys:
        # A hand save that keeps ticks makes the row the practitioner's own.
        cx.execute("DELETE FROM biofield_clinical_autotick_row WHERE test_id=? "
                   "AND item_key=?", (str(test_id), _norm(label)))


def _unhidden(cx, test_id, item_key):
    return {(r[0], r[1]) for r in cx.execute(
        "SELECT section,name_key FROM biofield_clinical_unhide WHERE test_id=? AND item_key=?",
        (str(test_id), item_key))}


def save_pick(cx, test_id, label, section, name, checked=None, rating=None,
              dose=None, note=None, commit=True):
    """Record one client's tick, edited rating, edited dose or note on one item.
    Only the fields passed change; the rest keep what was saved. commit=False lets
    a dry-run loader roll the whole run back."""
    ensure_schema(cx)
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown section {section!r}")
    if section in REMEDY_SECTIONS and checked is not None:
        raise ValueError("Functional Formulations and Infoceuticals ticks are saved as "
                         "the row's remedies")
    item_key, name_key = _norm(label), _norm(name)
    if not item_key or not name_key:
        raise ValueError("a condition and an item name are required")
    scores = (None, None, None)
    if rating is not None:
        scores = parse_rating(rating)
        if scores is None:
            raise ValueError("a rating is three of 1-9 or X, such as X74")
    cx.execute("""INSERT INTO biofield_clinical_section_pick
        (test_id,item_key,section,name_key,updated_at)
        VALUES (?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(test_id,item_key,section,name_key) DO NOTHING""",
               (str(test_id), item_key, section, name_key))
    sets, args = [], []
    if checked is not None:
        # A tick by hand is the practitioner's own, so the All box never takes it back.
        sets.append("checked=?,via=''")
        args.append(1 if checked else 0)
        if not checked:
            _set_all(cx, test_id, item_key, section, False)
    if rating is not None:
        sets.append("overall=?,research=?,uniqueness=?")
        args.extend(scores)
    if dose is not None:
        sets.append("dose=?")
        args.append(str(dose).strip()[:200])
    if note is not None:
        sets.append("note=?")
        args.append(str(note).strip()[:600])
    if sets:
        cx.execute("UPDATE biofield_clinical_section_pick SET "
                   + ",".join(sets) + ",updated_at=CURRENT_TIMESTAMP "
                   "WHERE test_id=? AND item_key=? AND section=? AND name_key=?",
                   args + [str(test_id), item_key, section, name_key])
    if commit:
        cx.commit()


def _set_all(cx, test_id, item_key, section, on):
    cx.execute("INSERT INTO biofield_clinical_section_all (test_id,item_key,section,all_on) "
               "VALUES (?,?,?,?) ON CONFLICT(test_id,item_key,section) DO UPDATE SET "
               "all_on=excluded.all_on,updated_at=CURRENT_TIMESTAMP",
               (str(test_id), item_key, section, 1 if on else 0))


def _all_on(cx, test_id, item_key):
    return {r[0] for r in cx.execute(
        "SELECT section FROM biofield_clinical_section_all "
        "WHERE test_id=? AND item_key=? AND all_on=1", (str(test_id), item_key))}


def _set_via(cx, test_id, item_key, section, name_key, via):
    cx.execute("INSERT INTO biofield_clinical_section_pick "
               "(test_id,item_key,section,name_key,via) VALUES (?,?,?,?,?) "
               "ON CONFLICT(test_id,item_key,section,name_key) DO UPDATE SET "
               "via=excluded.via,updated_at=CURRENT_TIMESTAMP",
               (str(test_id), item_key, section, name_key, via))


def _added_by_all(cx, test_id, item_key, section):
    return {r[0] for r in cx.execute(
        "SELECT name_key FROM biofield_clinical_section_pick WHERE test_id=? AND "
        "item_key=? AND section=? AND via='all'", (str(test_id), item_key, section))}


def _greyed(cx, test_id, item_key, section, ticked):
    """Name keys the client's avoid-botanicals switch greys out in one section, so
    All skips them whatever the page sends (Glen, 2026-10-18)."""
    setting = botanical_setting(cx, test_id)
    if not setting.get("avoid"):
        return set()
    exceptions = _exception_keys(setting.get("exceptions"))
    unhidden = _unhidden(cx, test_id, item_key)
    return {e["name_key"] for e in _catalog(cx, item_key)
            if e["section"] == section and e["botanical"]
            and (e["reviewed"] or e["origin_test"] in ("", str(test_id)))
            and e["name_key"] not in ticked and (section, e["name_key"]) not in unhidden
            and not _allowed_botanical(e["name"], exceptions)}


def select_all(cx, test_id, label, section, on, names, remedies=()):
    """The section's All box. `names` are the items the page shows in that section,
    so a botanical the client's switch hides is never ticked by it.

    On: every shown item not yet ticked is ticked and marked as added by the box.
    Off: only the items the box added are unticked; ticks made by hand stay.

    For Functional Formulations and Infoceuticals, `remedies` is the row's current
    tick set and the result is the new set, which the caller saves as the row's
    remedies. For the other sections the result is None."""
    ensure_schema(cx)
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown section {section!r}")
    item_key = _norm(label)
    if not item_key:
        raise ValueError("a condition is required")
    test_id = str(test_id)
    shown = []
    for raw in names or []:
        name = str(raw or "").strip()[:160]
        if name and _norm(name) not in {_norm(n) for n in shown}:
            shown.append(name)
    if section in REMEDY_SECTIONS:
        chosen = [str(r or "").strip() for r in remedies or [] if str(r or "").strip()]
        chosen_keys = {_norm(r) for r in chosen}
        if on:
            greyed = _greyed(cx, test_id, item_key, section, chosen_keys)
            for name in shown:
                if _norm(name) not in chosen_keys and _norm(name) not in greyed:
                    chosen.append(name)
                    chosen_keys.add(_norm(name))
                    _set_via(cx, test_id, item_key, section, _norm(name), "all")
        else:
            added = _added_by_all(cx, test_id, item_key, section)
            chosen = [r for r in chosen if _norm(r) not in added]
            for key in added:
                _set_via(cx, test_id, item_key, section, key, "")
        _set_all(cx, test_id, item_key, section, on)
        return chosen
    catalog = {e["name_key"] for e in _catalog(cx, item_key) if e["section"] == section}
    picks = _picks(cx, test_id, item_key)
    if on:
        ticked = {k for (sec, k), p in picks.items() if sec == section and p.get("checked")}
        greyed = _greyed(cx, test_id, item_key, section, ticked)
        for name in shown:
            key = _norm(name)
            if key not in catalog or key in greyed:
                continue
            if not (picks.get((section, key)) or {}).get("checked"):
                save_pick(cx, test_id, label, section, name, checked=True, commit=False)
                _set_via(cx, test_id, item_key, section, key, "all")
    else:
        for key in _added_by_all(cx, test_id, item_key, section):
            cx.execute("UPDATE biofield_clinical_section_pick SET checked=0,via='',"
                       "updated_at=CURRENT_TIMESTAMP WHERE test_id=? AND item_key=? "
                       "AND section=? AND name_key=?", (test_id, item_key, section, key))
    _set_all(cx, test_id, item_key, section, on)
    return None


def checked_names(cx, test_id, label, section, names):
    """Which of `names` are ticked in a non-remedy section, so the page can redraw
    the All box's effect in place instead of reloading and losing its place."""
    picks = _picks(cx, str(test_id), _norm(label))
    return [n for n in names or []
            if (picks.get((section, _norm(str(n or "")))) or {}).get("checked")]


def remedies_changed(cx, test_id, label, before, after):
    """The row's remedies were saved by hand. A remedy unticked by hand is no longer
    one the All box added, and the box itself unticks, as it does in the other
    sections and in PRL."""
    ensure_schema(cx)
    clear_auto_marks(cx, test_id, label, before, after)
    item_key = _norm(label)
    after_keys = {_norm(r) for r in after or []}
    removed = [r for r in before or [] if _norm(r) not in after_keys]
    for section in REMEDY_SECTIONS:
        for key in _added_by_all(cx, test_id, item_key, section) - after_keys:
            _set_via(cx, test_id, item_key, section, key, "")
    infoceutical_keys = _infoceutical_keys(cx, item_key)
    for name in removed:
        section = INFOCEUTICALS if (is_infoceutical(name) or _norm(name)
                                    in infoceutical_keys) \
            and not _NOT_INFOCEUTICAL.match(str(name).strip()) else FORMULATIONS
        _set_all(cx, test_id, item_key, section, False)


def _infoceutical_keys(cx, item_key):
    return {r[0] for r in cx.execute(
        "SELECT name_key FROM biofield_clinical_section_item WHERE item_key=? "
        "AND section=?", (item_key, INFOCEUTICALS))}


def _catalog(cx, item_key):
    rows = cx.execute(
        "SELECT section,name_key,name,brand,dose,overall,research,uniqueness,"
        "reason,source,botanical,lowers_bp,reviewed,origin_test,food_keys,route,"
        "route_reason FROM biofield_clinical_section_item WHERE item_key=?",
        (item_key,)).fetchall()
    cols = ("section", "name_key", "name", "brand", "dose", "overall", "research",
            "uniqueness", "reason", "source", "botanical", "lowers_bp", "reviewed",
            "origin_test", "food_keys", "route", "route_reason")
    return [dict(zip(cols, row)) for row in rows]


def _picks(cx, test_id, item_key):
    rows = cx.execute(
        "SELECT section,name_key,checked,overall,research,uniqueness,dose,note "
        "FROM biofield_clinical_section_pick WHERE test_id=? AND item_key=?",
        (str(test_id), item_key)).fetchall()
    return {(r[0], r[1]): {"checked": r[2], "overall": r[3], "research": r[4],
                           "uniqueness": r[5], "dose": r[6], "note": r[7]} for r in rows}


def _apply_pick(entry, pick):
    entry = dict(entry)
    entry["catalog_rating"] = rating_code(entry.get("overall"), entry.get("research"),
                                          entry.get("uniqueness"))
    if pick:
        if pick.get("overall") is not None:
            for s in SCORES:
                entry[s] = pick[s]
        if pick.get("dose") is not None:
            entry["dose"] = pick["dose"]
        entry["note"] = pick.get("note") or ""
        entry["checked"] = bool(pick.get("checked"))
    entry.setdefault("note", "")
    entry.setdefault("checked", False)
    entry["rating"] = rating_code(entry.get("overall"), entry.get("research"),
                                  entry.get("uniqueness"))
    entry["lowers_bp"] = bool(entry.get("lowers_bp"))
    entry["botanical"] = bool(entry.get("botanical"))
    entry["reviewed"] = bool(entry.get("reviewed"))
    entry.setdefault("conflicts", [])
    return entry


def sections_for(cx, test_id, label, remedies=(), chosen_remedies=(), botanical=None,
                 marks=None):
    """The eight sections for one condition row, each sorted highest rating first.

    `remedies` is the row's existing remedy list. Formulations the content adds
    follow it, unless Glen deleted them from this condition. `chosen_remedies` is
    the row's saved tick set. Nothing is ticked that the practitioner did not tick.
    Each section reports how many botanical items the client's switch hid.

    The row's remedies split in two: an infoceutical shows under Infoceuticals, the
    rest under Functional Formulations. `marks` (e4l_scan_marks.infoceutical_marks)
    marks each infoceutical the client's scan recommends, and each that is purple.
    """
    ensure_schema(cx)
    item_key = _norm(label)
    test_id = str(test_id)
    setting = botanical if botanical is not None else botanical_setting(cx, test_id)
    exceptions = _exception_keys(setting.get("exceptions"))
    catalog = [e for e in _catalog(cx, item_key)
               if e["reviewed"] or e["origin_test"] in ("", test_id)]
    picks = _picks(cx, test_id, item_key)
    all_on = _all_on(cx, test_id, item_key)
    unhidden = _unhidden(cx, test_id, item_key)
    watched_keys, watched_codes = _watched(cx, item_key)
    autoticked = _auto_added(cx, test_id, item_key)
    by_section = {key: [] for key in SECTION_KEYS}
    rated, rated_info = {}, {}
    for entry in catalog:
        if entry["section"] == FORMULATIONS:
            rated[entry["name_key"]] = entry
        elif entry["section"] == INFOCEUTICALS:
            rated_info[entry["name_key"]] = entry
        elif entry["section"] in by_section:
            pick = picks.get((entry["section"], entry["name_key"]))
            by_section[entry["section"]].append(_apply_pick(entry, pick))
    chosen = {str(name).strip().lower() for name in chosen_remedies or []}
    names, seen = [], set()
    for name in remedies or []:
        if _norm(name) not in seen:
            seen.add(_norm(name))
            names.append(name)
    deleted = forgotten_remedies(cx, label)
    for key, entry in list(rated.items()) + list(rated_info.items()):
        if key not in seen and key not in deleted:
            seen.add(key)
            names.append(entry["name"])
    for name in names:
        key = _norm(name)
        section = INFOCEUTICALS if (key in rated_info or is_infoceutical(name)) \
            and not _NOT_INFOCEUTICAL.match(str(name).strip()) else FORMULATIONS
        # A remedy already on the row but never rated counts as reviewed: it was in
        # the catalog before this content, so it carries no "not reviewed" mark.
        # Infoceuticals loaded under formulations before their own section existed
        # keep that rating.
        base = rated_info.get(key) or rated.get(key) or {
                                  "section": section, "name_key": key, "brand": "",
                                  "dose": "", "overall": None, "research": None,
                                  "uniqueness": None, "reason": "", "source": "",
                                  "botanical": 0, "lowers_bp": None, "reviewed": 1,
                                  "origin_test": ""}
        pick = picks.get((section, key))
        if section == INFOCEUTICALS and (FORMULATIONS, key) in picks:
            # A rating or note saved while it sat under Functional Formulations still
            # shows; anything saved under Infoceuticals since wins, field by field.
            pick = dict(picks[(FORMULATIONS, key)], **{
                k: v for k, v in (pick or {}).items() if v is not None})
        entry = _apply_pick(dict(base, name=name), pick)
        # The row's remedy name, as the existing list spells it, is what the tick
        # store and the bottles chip key on.
        entry["checked"] = str(name).strip().lower() in chosen
        if section == INFOCEUTICALS:
            code = infoceutical_code(name)
            entry["code"] = code
            entry["watched"] = key in watched_keys or bool(
                code and code.upper() in watched_codes)
            # Ticked by the page because it was purple, and still ticked.
            entry["auto_ticked"] = bool(entry["checked"] and key in autoticked)
            if marks:
                # No code, no answer: unknown, never "not recommended".
                recommended = marks.get("recommended") if code else None
                entry["recommended"] = None if recommended is None else code in recommended
                purple = marks.get("purple") if code else None
                entry["purple"] = None if purple is None else code in purple
                # Watched and purple: recommend it on this scan.
                entry["watch_hit"] = bool(entry["watched"] and entry["purple"])
                # The bar's own colour (Glen, 2026-10-18: "Just show an icon with
                # the color"). None is unknown; [] means the code is not on the scan.
                colours = marks.get("colours") if code else None
                entry["colours"] = None if colours is None else colours.get(code, [])
        by_section[section].append(entry)
    if marks and marks.get("colours") is not None:
        # Nutrients carry the colour of each scan field they name (Glen, 2026-10-18).
        # A botanical shows one only when the scan has a field it names.
        from dashboard.e4l_nutrient_fields import fields_for
        for entry in by_section[NUTRIENTS] + by_section[BOTANICALS]:
            entry["scan_colours"] = [(field, marks["colours"][field])
                                     for field in fields_for(entry["name"])
                                     if field in marks["colours"]]
    out = []
    for key, title in SECTIONS:
        items, hidden = by_section[key], 0
        if setting.get("avoid"):
            for entry in items:
                # A ticked item stays: hiding it would hide a choice already made.
                if entry["botanical"] and not entry["checked"] \
                        and not _allowed_botanical(entry["name"], exceptions):
                    if (key, _norm(entry["name"])) in unhidden:
                        entry["unhidden"] = True
                    else:
                        # Glen, 2026-10-18: "Show the 'hidden' items as greyed out",
                        # each with a button to unhide it. Greyed, it cannot be ticked.
                        entry["hidden"] = True
                        hidden += 1
        # An item added after All was ticked is not ticked, so the box no longer
        # reads as ticked: it would claim every item is in when one is not.
        out.append({"key": key, "title": title, "items": sort_items(items),
                    "botanical_hidden": hidden,
                    "watch_hits": sum(1 for e in items if e.get("watch_hit")),
                    "marks_errors": _marks_errors(marks, key),
                    "all_on": key in all_on and all(e["checked"] for e in items
                                                    if not e.get("hidden"))})
    return out


def _marks_errors(marks, key=INFOCEUTICALS):
    """What could not be read, as lines for the page, so an unread mark never
    looks like a clean "no"."""
    out = []
    if key in (NUTRIENTS, BOTANICALS):
        if marks and marks.get("colours") is None:
            out.append(("Colours could not be read from the scan PDF",
                        marks.get("purple_error") or ""))
        return out
    if key != INFOCEUTICALS:
        return out
    if marks and marks.get("recommended") is None:
        out.append(("Recommended could not be read from the scan",
                    marks.get("recommended_error") or ""))
    if marks and marks.get("purple") is None:
        out.append(("Colours could not be read from the scan PDF",
                    marks.get("purple_error") or ""))
    return out


def flag_food_conflicts(rows):
    """Mark a food one checked row emphasizes and another checked row avoids.
    `rows` is [(label, is_checked, sections)]; each clashing item gets the other
    row's label in its `conflicts` list. An item matches on its food_keys, or on
    its whole normalised name when it has none."""
    emphasize, avoid = {}, {}
    for label, is_checked, sections in rows:
        if not is_checked:
            continue
        for section in sections or []:
            target = {"foods_emphasize": emphasize, "foods_avoid": avoid}.get(section["key"])
            if target is None:
                continue
            for entry in section["items"]:
                for key in _food_keys(entry.get("food_keys")) or [_norm(entry["name"])]:
                    target.setdefault(key, []).append((label, entry))
    count, seen = 0, set()
    for key in set(emphasize) & set(avoid):
        for label, entry in emphasize[key]:
            for other, against in avoid[key]:
                # One pair of items is one conflict, however many foods they share.
                if other == label or (id(entry), id(against)) in seen:
                    continue
                seen.add((id(entry), id(against)))
                if other not in entry["conflicts"]:
                    entry["conflicts"].append(other)
                if label not in against["conflicts"]:
                    against["conflicts"].append(label)
                count += 1
    return count


def checked_items(cx, test_id):
    """Every ticked food, nutrient and Fullscript item for one client, by condition.
    Formulations are left out: the report already carries them through the chain."""
    ensure_schema(cx)
    rows = cx.execute(
        "SELECT p.item_key,p.section,i.name,i.brand,COALESCE(p.dose,i.dose) "
        "FROM biofield_clinical_section_pick p JOIN biofield_clinical_section_item i "
        "ON i.item_key=p.item_key AND i.section=p.section AND i.name_key=p.name_key "
        "WHERE p.test_id=? AND p.checked=1", (str(test_id),)).fetchall()
    return [{"item_key": r[0], "section": r[1], "name": r[2], "brand": r[3], "dose": r[4]}
            for r in rows]
