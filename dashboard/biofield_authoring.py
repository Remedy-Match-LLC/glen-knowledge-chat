"""Increment 4a: native authoring store for the local Biofield Analysis tool.

Lets Glen author a biofield test in the app instead of FileMaker. A test is a
header + causal-chain rows entered directly (streamlined vs FMP's stress->promote
flow). `authored_report` returns the SAME shape as `biofield_report.causal_chain_report`
so the schedule, narrative, and your-voice audio all work on authored tests unchanged.

Authored test ids are prefixed "a" (e.g. "a7") so the viewer can tell them apart
from the numeric FMP-snapshot ids. Local + writable; PHI stays on the Mac.
"""
import datetime
import difflib
import functools
import json
import os
import re
import sqlite3

from dashboard import db
from dashboard import dbwrite
from dashboard.ambiguous_product_names import is_ambiguous_product_name
from dashboard.biofield_schedule import build_schedule
from dashboard.biofield_dimensions import DEPTH_KEY, depth_label, depth_match, get_tag

_PRODUCTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "products.json")


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _num(tid):
    return int(str(tid).lstrip("a") or 0)


def _clean_product_name(name):
    """FMP product names carry a trailing '*' as Glen's internal 'intending to
    discontinue' marker — the product is still active and sellable. Drop it so the
    picker name matches the sellable catalog and the stress-coverage map, which
    both store the clean name. Mirrors scripts/fmp_catalog_import.clean_name."""
    return (name or "").strip().rstrip("*").strip()


def _is_discontinue_intent(name):
    """True when a product carries the trailing-'*' discontinue-intent marker."""
    return (name or "").strip().endswith("*")


def init_auth_tables(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_auth_tests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT,
        date_test TEXT, created_at TEXT, updated_at TEXT)""")
    # Terrain reading from the spoken BSI ('phase P' + 'location of the N is X').
    # Nullable: FMP-snapshot and older authored tests carry no BSI phase.
    # no_charge: run the analysis without charging for it, which -- by keeping the
    # biofield-analysis line off the invoice -- also withholds the care window, the
    # 30-day month and member pricing, all of which key off that line.
    # e4l_client_id: the E4L client the intake is for. Held so "Check E4L now"
    # can key on the id after a reload instead of falling back to matching by
    # name -- Judith Tom is "Judith Ann Tom" on her own PDF, so the name is the
    # weaker key for exactly the clients who need it.
    # bsi_a..bsi_d: the four Biological Score Index numbers, "A x B to C x D", each
    # 1-21 or NULL (live session segments, plan 2026-11-07). Regulation is computed
    # from them on read (terrain_phase.regulation_level), never stored.
    for col in ("phase INTEGER", "location TEXT",
                "bsi_a INTEGER", "bsi_b INTEGER", "bsi_c INTEGER", "bsi_d INTEGER",
                "e4l_client_id TEXT",
                "no_charge INTEGER NOT NULL DEFAULT 0",
                # terrain_review: JSON {field: "review"|"unmatched"} for terrain fields
                # the hands-free recording wrote that Glen has not yet set or confirmed
                # by hand (ruling 7). NULL or {} when there is nothing to review.
                "terrain_review TEXT",
                # bsi_location: where the BSI was read. Glen, 15 Nov 2026: shown "21 x 3
                # Liver to 1 x 1", spoken "BSI 21 times 3 to 1 times 1, location Liver".
                # Separate from `location`, the terrain location after the phase.
                "bsi_location TEXT"):
        try:
            cx.execute(f"ALTER TABLE biofield_auth_tests ADD COLUMN {col}")
        except Exception:
            pass
    cx.execute("""CREATE TABLE IF NOT EXISTS biofield_auth_chain(
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, layer INTEGER,
        head TEXT, most_affected TEXT, remedy TEXT, dosage TEXT, frequency TEXT,
        timing TEXT, sort_seq INTEGER, created_at TEXT, updated_at TEXT,
        confirmed INTEGER DEFAULT 1, origin TEXT NOT NULL DEFAULT 'live')""")
    try:
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN confirmed INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN origin TEXT NOT NULL DEFAULT 'live'")
    except Exception:
        pass
    try:
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN updated_at TEXT")
    except Exception:
        pass
    try:
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN schedule_slot TEXT")
    except Exception:
        pass
    try:
        # Bottles to bill for this line; NULL means 1 (Glen 2026-09-25).
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN bottles INTEGER")
    except Exception:
        pass
    try:
        # Per-layer stress codes (JSON list), carried from the synthesis/reveal so a
        # layer knows its own patterns even when the coverage map doesn't link them.
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN codes TEXT")
    except Exception:
        pass
    try:
        # Balanced mark for the LAYER (Glen, 14 November 2026: "layer only"). Set on
        # every row of a card, the way head and tail are. It never touches a stress's
        # balanced state: stresses stay marked by remedy coverage.
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN layer_balanced INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        # Which of a row's fields came from a short recording that matched nothing
        # (JSON list of head, most_affected, remedy), plus "other" when the row was
        # already unconfirmed for another reason (Interpret, a scan). The row stays
        # unconfirmed while the list is not empty. See apply_voice_fields.
        cx.execute("ALTER TABLE biofield_auth_chain ADD COLUMN unmatched_fields TEXT")
    except Exception:
        pass
    # Backfill: a pre-existing row was never edited, so updated_at == created_at.
    # Seeding it with _now() instead would mark every historical row as "edited".
    cx.execute("UPDATE biofield_auth_chain SET updated_at=created_at "
               "WHERE updated_at IS NULL OR updated_at=''")
    cx.commit()


def create_test(cx, name, email, date):
    init_auth_tables(cx)
    new_id = dbwrite.insert_returning_id(cx,
        "INSERT INTO biofield_auth_tests(name,email,date_test,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        ((name or "").strip(), (email or "").strip().lower(), (date or "").strip(),
         _now(), _now()))
    cx.commit()
    return "a" + str(new_id)


def update_header(cx, tid, name=None, email=None, date=None, client_id=None):
    init_auth_tables(cx)
    sets, vals = [], []
    if name is not None:
        sets.append("name=?"); vals.append((name or "").strip())
    if email is not None:
        sets.append("email=?"); vals.append((email or "").strip().lower())
    if date is not None:
        sets.append("date_test=?"); vals.append((date or "").strip())
    if client_id is not None:
        # An empty string clears it: choosing a different client must be able to
        # REMOVE a stale id, or the check keeps querying the previous person.
        sets.append("e4l_client_id=?"); vals.append(str(client_id).strip())
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(_now())
    vals.append(_num(tid))
    cx.execute(f"UPDATE biofield_auth_tests SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()


BSI_COLS = ("bsi_a", "bsi_b", "bsi_c", "bsi_d")


def update_terrain(cx, tid, phase=None, location=None, bsi=None, bsi_location=None):
    """Persist the scan's terrain reading (BSI 'phase P' + 'location X') on the test.
    Per-field, like update_header: a pass that read no phase/location must not clobber
    a value captured earlier. phase is coerced to an int 1-5 (else ignored).
    bsi is the four BSI numbers [A, B, C, D]. They are saved only when all four are
    ints 1-21; otherwise all four stored values are left alone. bsi_location is saved
    with them and only with them: a location with no valid BSI is not saved. The
    hand-set route (set_terrain_field) stays per field."""
    from dashboard.terrain_phase import phase_num, bsi_num
    init_auth_tables(cx)
    sets, vals = [], []
    p = phase_num(phase)
    if p is not None:
        sets.append("phase=?"); vals.append(p)
    if (location or "").strip():
        sets.append("location=?"); vals.append(location.strip())
    # All or nothing: a partial reading such as [15, 10, None, None] merged with the
    # stored C and D would make a BSI nobody spoke, and a Regulation level from it.
    nums = [bsi_num(v) for v in bsi] if isinstance(bsi, (list, tuple)) else []
    if len(nums) == 4 and None not in nums:
        for col, n in zip(BSI_COLS, nums):
            sets.append(f"{col}=?"); vals.append(n)
        if (bsi_location or "").strip():
            sets.append("bsi_location=?"); vals.append(bsi_location.strip())
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(_now())
    vals.append(_num(tid))
    cx.execute(f"UPDATE biofield_auth_tests SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()


TERRAIN_FIELDS = ("phase", "location") + BSI_COLS + ("bsi_location",)
# The text fields matched against the stress list; the rest are numbers.
LOCATION_FIELDS = ("location", "bsi_location")


def _terrain_review(raw):
    try:
        d = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in d.items() if k in TERRAIN_FIELDS} if isinstance(d, dict) else {}


def get_terrain_review(cx, tid):
    """{field: "review"|"unmatched"} for the terrain fields waiting for Glen's review."""
    init_auth_tables(cx)
    row = cx.execute("SELECT terrain_review FROM biofield_auth_tests WHERE id=?",
                     (_num(tid),)).fetchone()
    return _terrain_review(row[0] if row else None)


def mark_terrain_review(cx, tid, fields, status=None):
    """Set (status "review" or "unmatched") or clear (status None) the review mark on
    terrain fields. A hands-free write marks; a hand edit clears (ruling 7)."""
    left = get_terrain_review(cx, tid)
    for f in fields:
        if status:
            left[f] = status
        else:
            left.pop(f, None)
    cx.execute("UPDATE biofield_auth_tests SET terrain_review=? WHERE id=?",
               (json.dumps(left, sort_keys=True) if left else None, _num(tid)))
    cx.commit()
    return left


def set_terrain_bsi(cx, tid, values, handsfree=False):
    """All four BSI numbers at once, as the hands-free "BSI 12 by 8 to 9 by 6" says
    them. All or nothing, like update_terrain: raises ValueError unless there are
    exactly four numbers, each 1 to 21, and then writes nothing. The BSI location is
    its own field (set_terrain_field "bsi_location"), sent after this one."""
    from dashboard.terrain_phase import bsi_num
    nums = [bsi_num(v) for v in values] if isinstance(values, (list, tuple)) else []
    if len(nums) != 4 or None in nums:
        raise ValueError("the BSI needs four numbers, each 1 to 21")
    update_terrain(cx, tid, bsi=nums)
    mark_terrain_review(cx, tid, BSI_COLS, "review" if handsfree else None)
    return nums


def set_terrain_field(cx, tid, field, value, handsfree=False, matched=True):
    """One terrain field, set by hand on the editor page. Unlike update_terrain, an
    empty value CLEARS the field: Glen choosing blank is a decision, not a pass that
    heard nothing. Raises ValueError on an unknown field or an out-of-range number.

    A hand edit clears the field's review mark. handsfree=True (the hands-free
    recording) sets it instead: "unmatched" for a location (terrain or BSI) not on the
    stress list (matched=False), else "review"."""
    from dashboard.terrain_phase import phase_num, bsi_num
    if field not in TERRAIN_FIELDS:
        raise ValueError(f"unknown terrain field: {field}")
    init_auth_tables(cx)
    raw = "" if value is None else str(value).strip()
    if raw == "":
        stored = None
    elif field in LOCATION_FIELDS:
        stored = raw
    elif field == "phase":
        stored = phase_num(raw)
        if stored is None:
            raise ValueError("phase must be 1 to 5")
    else:
        stored = bsi_num(raw)
        if stored is None:
            raise ValueError("each BSI number must be 1 to 21")
    cx.execute(f"UPDATE biofield_auth_tests SET {field}=?,updated_at=? WHERE id=?",
               (stored, _now(), _num(tid)))
    cx.commit()
    status = None
    if handsfree and stored is not None:
        status = "unmatched" if (field in LOCATION_FIELDS and not matched) else "review"
    mark_terrain_review(cx, tid, [field], status)
    return stored


def terrain_state(rep):
    """The terrain fields of an authored_report, plus Regulation and its warnings."""
    from dashboard.terrain_phase import regulation_level, bsi_warnings
    bsi = list(rep.get("bsi") or [None] * 4)
    r = regulation_level(*bsi)
    return {"phase": rep.get("phase"), "location": rep.get("location") or "",
            "bsi": bsi, "bsi_location": rep.get("bsi_location") or "",
            "review": dict(rep.get("terrain_review") or {}),
            "regulation": ({"level": r[0], "name": r[1]} if r else None),
            "warnings": bsi_warnings(*bsi)}


def add_chain_row(cx, tid, layer, head, most_affected, remedy,
                  dosage="", frequency="", timing="", confirmed=1, origin="live", codes=None,
                  commit=True):
    """commit=False is for a batch: the caller has run init_auth_tables first and
    commits once at the end, so a failure part way leaves nothing behind."""
    if commit:
        init_auth_tables(cx)
    now = _now()   # born unedited: updated_at == created_at
    codes_json = json.dumps([str(c).strip() for c in (codes or []) if str(c).strip()])
    new_id = dbwrite.insert_returning_id(cx,
        "INSERT INTO biofield_auth_chain(test_id,layer,head,most_affected,remedy,"
        "dosage,frequency,timing,sort_seq,created_at,updated_at,confirmed,origin,codes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_num(tid), layer, (head or "").strip(), (most_affected or "").strip(),
         (remedy or "").strip(), dosage or "", frequency or "", timing or "", 0, now, now,
         1 if confirmed else 0, (origin or "live"), codes_json))
    if commit:
        cx.commit()
    return new_id


def get_no_charge(cx, tid):
    init_auth_tables(cx)
    row = cx.execute("SELECT no_charge FROM biofield_auth_tests WHERE id=?",
                     (_num(tid),)).fetchone()
    return bool(row and row[0])


def set_no_charge(cx, tid, on):
    """Per test, never per client: comping one analysis must not comp the next."""
    init_auth_tables(cx)
    cx.execute("UPDATE biofield_auth_tests SET no_charge=?,updated_at=? WHERE id=?",
               (1 if on else 0, _now(), _num(tid)))
    cx.commit()
    return bool(on)


def confirm_row(cx, rid):
    init_auth_tables(cx)
    cx.execute("UPDATE biofield_auth_chain SET confirmed=1, unmatched_fields=NULL WHERE id=?",
               (rid,))
    cx.commit()


VOICE_FIELDS = ("head", "most_affected", "remedy")


def _unmatched(raw):
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return set()
    return {str(x) for x in v} if isinstance(v, list) else set()


def apply_voice_fields(cx, tid, rid, voice):
    """Record which fields of one row a short recording filled, and whether each
    matched (Glen, 14 November 2026, ruling 7), then set confirmed from that.

    voice = {field: matched} for head, most_affected or remedy. A match clears only
    the field it filled. The row is confirmed only when no field is still unmatched,
    and never by a match alone when it was unconfirmed for another reason: that
    reason is kept as "other" until Glen confirms the row. Like confirm_row this is
    not a value edit, so updated_at is left alone. Returns the new confirmed value,
    or None when the row is not on this test or nothing was given."""
    init_auth_tables(cx)
    voice = {k: bool(v) for k, v in (voice or {}).items() if k in VOICE_FIELDS}
    if not voice:
        return None
    row = cx.execute("SELECT confirmed, unmatched_fields FROM biofield_auth_chain "
                     "WHERE id=? AND test_id=?", (int(rid), _num(tid))).fetchone()
    if not row:
        return None
    left = _unmatched(row[1])
    if row[0] == 0 and not left:
        left.add("other")              # unconfirmed before any recording touched it
    for f, ok in voice.items():
        (left.discard if ok else left.add)(f)
    confirmed = 0 if left else 1
    cx.execute("UPDATE biofield_auth_chain SET confirmed=?, unmatched_fields=? "
               "WHERE id=? AND test_id=?",
               (confirmed, json.dumps(sorted(left)), int(rid), _num(tid)))
    cx.commit()
    return confirmed


HANDSFREE_MARK = "handsfree"


def mark_handsfree(cx, tid, rid):
    """A row written or changed by the hands-free recording goes in unconfirmed, even
    when every field matched (Glen, 14 November 2026, ruling 7). The mark joins
    unmatched_fields, so a later short recording that matches a field still leaves
    the row for review; only Confirm, Confirm all or placing the card clears it.
    Returns True when the row is on this test."""
    init_auth_tables(cx)
    row = cx.execute("SELECT unmatched_fields FROM biofield_auth_chain "
                     "WHERE id=? AND test_id=?", (int(rid), _num(tid))).fetchone()
    if not row:
        return False
    left = _unmatched(row[0])
    left.add(HANDSFREE_MARK)
    cx.execute("UPDATE biofield_auth_chain SET confirmed=0, unmatched_fields=? "
               "WHERE id=? AND test_id=?", (json.dumps(sorted(left)), int(rid), _num(tid)))
    cx.commit()
    return True


def set_layer_balanced(cx, tid, rids, balanced):
    """Tick or clear the Balanced box of one layer card: layer_balanced on every row
    id given that belongs to this test (Glen, 14 November 2026, rulings 2 and 3).

    Layer only. This writes biofield_auth_chain and nothing else: no stress's
    manual_balanced, no layer-stress link, no coverage row."""
    init_auth_tables(cx)
    ids = []
    for r in rids or []:
        try:
            ids.append(int(r))
        except (TypeError, ValueError):
            continue
    n = 0
    for rid in ids:
        n += cx.execute("UPDATE biofield_auth_chain SET layer_balanced=? "
                        "WHERE id=? AND test_id=?",
                        (1 if balanced else 0, rid, _num(tid))).rowcount
    cx.commit()
    return n


def confirm_all(cx, tid):
    init_auth_tables(cx)
    cx.execute("UPDATE biofield_auth_chain SET confirmed=1, unmatched_fields=NULL "
               "WHERE test_id=?", (_num(tid),))
    # Confirm all covers the terrain panel's hands-free values too.
    cx.execute("UPDATE biofield_auth_tests SET terrain_review=NULL WHERE id=?", (_num(tid),))
    cx.commit()


def delete_test(cx, tid):
    init_auth_tables(cx)
    cx.execute("DELETE FROM biofield_auth_chain WHERE test_id=?", (_num(tid),))
    cx.execute("DELETE FROM biofield_auth_tests WHERE id=?", (_num(tid),))
    for t in ("biofield_notes", "biofield_narratives", "biofield_video_scripts"):
        try:
            cx.execute(f"DELETE FROM {t} WHERE test_id=?", (str(tid),))
        except Exception:
            pass
    cx.commit()


_SMALL_WORDS = {"in", "of", "the", "a", "an", "and", "or", "with", "for", "to", "by", "on"}


def _title_case_name(s):
    """Title-case a free-text name without mangling product codes or small words.
    'reverse age' -> 'Reverse Age', 'head and tail' -> 'Head and Tail', and a token
    carrying a digit or an internal capital (e.g. 'MB5', 'B12', 'pH') is left as-is."""
    s = (s or "").strip()
    if not s:
        return s
    words = s.split()
    out = []
    for i, w in enumerate(words):
        if any(c.isdigit() for c in w) or any(c.isupper() for c in w[1:]):
            out.append(w)                      # preserve codes / intentional casing
        elif i > 0 and w.lower() in _SMALL_WORDS:
            out.append(w.lower())              # keep small connector words lowercase
        else:                                  # capitalize each hyphen/slash chunk
            out.append(re.sub(r"[A-Za-z]+",
                              lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), w))
    return " ".join(out)


def _norm_name(s):
    """Compare FMP names and catalog titles on the same footing: no trailing
    discontinue-'*', no case, whitespace (incl. FMP's embedded newlines) collapsed."""
    return re.sub(r"\s+", " ", _clean_product_name(s or "")).strip().lower()


@functools.lru_cache(maxsize=1)
def _deprecated_catalog_names():
    """FMP product names whose catalog record is `inactive` (a deprecated duplicate).

    Never auto-correct a spoken remedy onto one of these: its slug is unsellable, so
    the remedy would be carried into the chain as a dead end. products.json keeps the
    ORIGINAL FMP name in `pinecone_title` even after the display `name` is renamed,
    which is what lets us match an FMP row to its catalog record here.

    Concretely: FMP holds both 'Neuro+ Eye Drops' (retired) and 'Neuro Eye Drops\\n
    ACES+GL Lite Eye Drops' (live). Saying "neuro eye drops" fuzzy-matches the short
    retired name. Excluding it makes the live record win."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return frozenset()                       # no catalog -> filter nothing
    out = set()
    for p in products.values():
        if not p.get("inactive"):
            continue
        for key in ("pinecone_title", "name"):
            v = _norm_name(p.get(key))
            if v:
                out.add(v)
    # A name that a LIVE product also carries must never be excluded. The pairs rule
    # moves a retired twin's name onto its survivor, so from 2026-09-16 five names are
    # held by both: "MSM Syntropy Powder", "Flow Ease Powder", "SeaAmino Powder",
    # "Crucifer Complex Powder" and "Hydrolyzed Collagen Powder". Without this the
    # survivor's own name is dropped from the candidate pool and the matcher drifts to a
    # neighbour, which is exactly the ES1 -> ES5 Auto-Immune failure this module was
    # written to stop. Excluding it protects nothing: the name resolves to a live,
    # sellable product.
    return frozenset(out - set(_active_catalog_names()))


def _sellable_names(names):
    """Drop catalog-deprecated duplicates from the fuzzy-match candidate pool."""
    dep = _deprecated_catalog_names()
    return [n for n in names if _norm_name(n) not in dep]


@functools.lru_cache(maxsize=1)
def _active_catalog_names():
    """Normalized display names of every LIVE catalog product.

    The candidate pool used to be FMP's product names alone. A survivor that exists
    only in the catalog — `es1-lymph` has the storefront URL and no `fmp_id` — was
    therefore unmatchable, so retiring its misnamed FMP twin stranded the remedy and
    the matcher drifted to a neighbour (ES1 -> ES5 Auto-Immune). Carrying live catalog
    names alongside FMP's makes the survivor reachable."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return frozenset()                       # no catalog -> add nothing
    return frozenset(
        v for p in products.values() if not p.get("inactive")
        for v in (_norm_name(p.get("name")),) if v)


@functools.lru_cache(maxsize=1)
def _superseded_name_map():
    """Retired display name -> its survivor's canonical name.

    Excluding a retired name from the pool stops it resolving to a DEAD END, but the
    matcher then picks the nearest live stranger. When the clinician says a retired
    name exactly, we know precisely what they meant: redirect to the survivor."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return {}
    out = {}
    for p in products.values():
        if not p.get("inactive"):
            continue
        tgt = products.get((p.get("superseded_by") or "").strip())
        if not tgt or tgt.get("inactive"):
            continue                             # no survivor, or a dead one: no redirect
        for key in ("pinecone_title", "name"):
            v = _norm_name(p.get(key))
            if v:
                out[v] = tgt.get("name")
    return out


@functools.lru_cache(maxsize=1)
def _catalog_alias_map():
    """Every spellable alias of a live catalog product -> its canonical display name.

    A product answers to BOTH its `name` and its `pinecone_title`. They diverge when a
    record is renamed but its vector title is pinned: `es1-lymph` is now named
    "ES1 Lymph and Immune Energetic Star Infoceutical" while its title stays "ES1".
    Without the title as an alias, saying the bare code "ES1" fuzzy-matched **ES15**
    (Heavy Metals). It also answers to its `aliases` (see _catalog_exact_aliases),
    indexed last so an alias never shadows a real name. Matching an alias resolves to
    the canonical name, never to the alias."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return {}
    out = {}
    for p in products.values():
        if p.get("inactive"):
            continue
        canon = (p.get("name") or "").strip()
        if not canon:
            continue
        for key in ("name", "pinecone_title"):
            v = _norm_name(p.get(key))
            if v:
                out.setdefault(v, canon)
    for v, canon in _catalog_exact_aliases().items():
        out.setdefault(v, canon)
    return out


@functools.lru_cache(maxsize=1)
def _catalog_exact_aliases():
    """A live product's `aliases` (names it had before a rename, short forms) -> its name.

    EXACT match only, never a fuzzy target. Round 1 of the ES1 review, 2026-10-02: in
    the fuzzy pool the short alias "ES1 Immune" scored about 0.9 against "ES5 Immune",
    so a neighbour's spelling landed on ES1, the drift this guards against.

    Opt-in per product (`report_aliases: true`). Round 3: read for every product, the
    alias "BFA" put the store-link-less BFA duplicate on reports and invoices, where a
    bare "BFA" is left for Rae today. Which BFA is meant is Glen's call, so only a
    product that opts in changes."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return {}
    live = [p for p in products.values() if not p.get("inactive")]
    # A real name or title always beats an alias, as in _catalog_alias_map.
    taken = {_norm_name(p.get(k)) for p in live for k in ("name", "pinecone_title")}
    out = {}
    for p in live:
        if p.get("report_aliases") is not True:
            continue
        canon = (p.get("name") or "").strip()
        for a in (p.get("aliases") or []):
            v = _norm_name(a) if canon and isinstance(a, str) else ""
            if v and v not in taken:
                out.setdefault(v, canon)
    return out


def _catalog_names_for_pool():
    """Live catalog aliases as displayable strings (the pool matches case-insensitively)."""
    try:
        with open(_PRODUCTS_JSON) as f:
            products = (json.load(f).get("products") or {})
    except Exception:
        return []
    out = []
    for p in products.values():
        if p.get("inactive"):
            continue
        for key in ("name", "pinecone_title"):
            v = (p.get(key) or "").strip()
            if v:
                out.append(v)
    return out


def _best_match(spoken, names, cutoff):
    """Case-insensitive closest match: ASR lowercases, so we compare on lowercase and
    map back to the canonical-cased name. Returns None when nothing is close enough."""
    # A "+" is scored as the spoken word "plus": ASR writes "ocuheal plus eye drops",
    # which otherwise scored 0.872 against "OcuHeal Eye Drops" and 0.85 against
    # "OcuHeal+ Eye Drops", so dictated OcuHeal+ was billed as OcuHeal (2026-09-24).
    low = lambda s: " ".join(str(s or "").lower().replace("+", " plus ").split())
    by_low = {}
    for n in names:
        by_low.setdefault(low(n), n)           # first canonical spelling wins
    hit = difflib.get_close_matches(low(spoken), list(by_low), n=1, cutoff=cutoff)
    return by_low[hit[0]] if hit else None


def _token_match(spoken, names, cutoff):
    """Match a distinctive spoken token (e.g. 'Sobopla') to the catalog product whose
    name CONTAINS it as a word — for cases where the clinician says only the unique
    part of a long product name. Returns the canonical name only when exactly ONE
    product qualifies, so a common shared word ('Essence') stays ambiguous and is
    left to the Title-Case fallback. Single distinctive token only."""
    sp = (spoken or "").strip().lower()
    if len(sp) < 5 or " " in sp:               # too short / multi-word -> not a distinctive token
        return None
    hits = set()
    for n in names:
        for t in re.findall(r"[A-Za-z0-9]+", n.lower()):
            if t == sp or (len(t) >= 5 and difflib.SequenceMatcher(None, sp, t).ratio() >= cutoff):
                hits.add(n)
                break
    return next(iter(hits)) if len(hits) == 1 else None


def exact_canonical_name(cx, name):
    """A name FileMaker no longer carries -> the live name it was renamed to. EXACT only.
    Anything else comes back unchanged (stripped).

    The scan reveal arrives with the remote synthesis's names. On 2026-10-03 it still said
    "Vitamin P Polyphenols", renamed to Vascular Integrity, so the row got no dosing and
    no invoice line. Round 1: redirecting every retired name rewrote 12 names FileMaker
    still sells, 8 to different dosing (Transform Powder -> Transform capsules). So a name
    redirects only when FileMaker lacks it AND has the target, which then carries dosing."""
    core = (name or "").strip()
    suffix = ""
    if core.lower().endswith(" in terrain restore"):
        core = core[: core.lower().rfind(" in terrain restore")].strip()
        suffix = " in Terrain Restore"     # the casing resolve_remedy_name writes
    if not core or cx is None or not _has(cx, "fmp_snap_products"):
        return (name or "").strip()
    live = {_norm_name(_clean_product_name(r[0])) for r in cx.execute(
        "SELECT product_name FROM fmp_snap_products "
        "WHERE TRIM(COALESCE(product_name,''))<>''").fetchall()}
    n = _norm_name(core)
    if n in live:
        return (name or "").strip()
    target = _superseded_name_map().get(n) or _catalog_exact_aliases().get(n)
    if not target or _norm_name(target) not in live:
        return (name or "").strip()
    return target + suffix


def resolve_remedy_name(cx, spoken, cutoff=0.82):
    """Best-effort auto-correct a (possibly ASR-mangled) remedy name to the closest
    catalog product (case-insensitive). Preserves an ' in Terrain Restore' suffix.
    Falls back to Title Case of the spoken name when there's no close catalog match.

    Deprecated duplicates (catalog `inactive`) are excluded from the candidate pool:
    FMP still marks them active, and their slugs are unsellable, so matching one would
    put a dead-end remedy on the chain."""
    return resolve_remedy_match(cx, spoken, cutoff)[0]


def resolve_remedy_match(cx, spoken, cutoff=0.82):
    """resolve_remedy_name, plus whether a known name matched: (value, matched).
    matched is False for the Title-Case fallback and for an ambiguous name left for a
    person (live session part 2, Glen 14 November: an unmatched phrase is kept and
    highlighted)."""
    spoken = (spoken or "").strip()
    if not spoken:
        return spoken, False
    suffix = ""
    core = spoken
    low = spoken.lower()
    if low.endswith("in terrain restore"):
        core = spoken[: low.rfind("in terrain restore")].strip()
        suffix = " in Terrain Restore"
    # A name that means two products ("Seacure") is left for a person, before any step.
    if is_ambiguous_product_name(core):
        return _title_case_name(core) + suffix, False
    # An EXACT retired name is unambiguous: redirect to its survivor before any fuzzy
    # matching, or the excluded name drifts onto the nearest live stranger.
    redirect = _superseded_name_map().get(_norm_name(core)) or _catalog_exact_aliases().get(_norm_name(core))
    if redirect:
        return redirect + suffix, True
    if _has(cx, "fmp_snap_products"):
        names = _sellable_names([r[0] for r in cx.execute(
            "SELECT DISTINCT product_name FROM fmp_snap_products "
            "WHERE TRIM(COALESCE(product_name,''))<>''").fetchall()])
        # Live catalog names join FMP's, so a catalog-only survivor is reachable.
        names = _sellable_names(list(dict.fromkeys(names + _catalog_names_for_pool())))
        # whole-string fuzzy first, then a distinctive-token match for long names.
        match = _best_match(core, names, cutoff) or _token_match(core, names, cutoff)
        if match:
            # An alias (e.g. the bare code "ES1") resolves to its canonical name.
            match = _catalog_alias_map().get(_norm_name(match), match)
            match = _clean_product_name(match)   # drop discontinue-intent '*'
            # Don't double the suffix when the matched name already carries it.
            if suffix and match.lower().endswith("in terrain restore"):
                return match, True
            return match + suffix, True
    return _title_case_name(core) + suffix, False


def resolve_stress_name(cx, spoken, cutoff=0.82):
    """Auto-correct a spoken stress / head-of-chain name to the closest stress term
    Glen has used before (case-insensitive), else Title Case the spoken name so stress
    names are always capitalized."""
    return resolve_stress_match(cx, spoken, cutoff)[0]


def resolve_stress_match(cx, spoken, cutoff=0.82, extra_names=()):
    """resolve_stress_name, plus whether a known name matched: (value, matched).

    extra_names join the pool AHEAD of the FileMaker terms, so their spelling wins a
    tie. match_term passes this client's detected stresses and Glen's added terms
    there; resolve_stress_name passes none, so its result is unchanged."""
    spoken = (spoken or "").strip()
    if not spoken:
        return spoken, False
    names = [n for n in (extra_names or ()) if (n or "").strip()]
    if _has_col(cx, "fmp_snap_client_active_main_stress", "main_stress"):
        names += [r[0] for r in cx.execute(
            "SELECT DISTINCT main_stress FROM fmp_snap_client_active_main_stress "
            "WHERE TRIM(COALESCE(main_stress,''))<>''").fetchall()]
    if names:
        match = _best_match(spoken, names, cutoff)
        if match:
            return match, True
    return _title_case_name(spoken), False


def update_chain_row(cx, rid, **fields):
    """Edit a row's VALUES. Bumps `updated_at` so an audit can tell a human edit from
    what the interpreter originally wrote (see was_edited). Confirming or re-ordering
    a row is not a value edit and deliberately leaves `updated_at` alone."""
    cols = ("layer", "head", "most_affected", "remedy", "dosage", "frequency",
            "timing", "schedule_slot", "bottles")
    sets, vals = [], []
    for k in cols:
        if k in fields:
            sets.append(f"{k}=?"); vals.append(fields[k])
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(_now())
    vals.append(rid)
    cx.execute(f"UPDATE biofield_auth_chain SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()


def was_edited(cx, rid):
    """True when a human has changed this row's VALUES since the interpreter wrote it.

    The dose audit needs exactly this: `updated_at > created_at` means the stored value
    is Glen's, not the model's.

    Deliberately excluded (they leave updated_at alone): confirm_row / confirm_all —
    reviewing is not editing, and bumping there would mark every confirmed row edited;
    and reorder_chain / arrange-cards, which move `layer` for positioning.

    Two limits, both erring toward UNDER-claiming edits (an audit must never excuse a
    fabrication by calling it a human edit):
      * rows predating the column were backfilled updated_at = created_at, so they read
        as unedited even if Glen had edited them;
      * `_now()` is second-resolution, so an edit within the same second as the insert
        is not detectable."""
    r = cx.execute("SELECT created_at, updated_at FROM biofield_auth_chain WHERE id=?",
                   (rid,)).fetchone()
    if not r:
        return False
    created, updated = (r[0] or ""), (r[1] or "")
    return bool(updated and created and updated > created)


def delete_chain_row(cx, rid):
    row = cx.execute("SELECT test_id FROM biofield_auth_chain WHERE id=?", (rid,)).fetchone()
    if row:
        from dashboard.biofield_stress import move_links_off
        move_links_off(cx, row[0], [rid])   # its Balances stay on the card
    cx.execute("DELETE FROM biofield_auth_chain WHERE id=?", (rid,))
    cx.commit()


def remove_remedy_preserving_layer(cx, tid, rid):
    """Remove one remedy without deleting its layer when it is the last remedy.

    A chain row carries both the layer's Head/Tail and a remedy.  Deleting the
    final row therefore used to erase the layer itself.  Keep that row as the
    layer anchor and clear only remedy-specific fields; when sibling remedies
    exist on the same stored layer, the row can safely be deleted.
    """
    row = cx.execute(
        "SELECT test_id, layer, head FROM biofield_auth_chain WHERE id=? AND test_id=?",
        (rid, _num(tid)),
    ).fetchone()
    if not row:
        return False
    head = (row[2] or "").strip()
    if head:
        sibling_count = cx.execute(
            "SELECT COUNT(*) FROM biofield_auth_chain "
            "WHERE test_id=? AND id<>? AND LOWER(TRIM(COALESCE(head,'')))=LOWER(?) "
            "AND TRIM(COALESCE(remedy,''))<>''",
            (row[0], rid, head),
        ).fetchone()[0]
    else:
        # Empty-head rows are deliberately rendered as separate layers.
        sibling_count = 0
    if sibling_count:
        # The row's Balances move to the rows left on its card. Leaving them on the
        # deleted row hid them: a50's stress 1914 balanced no layer after row 449 went.
        from dashboard.biofield_stress import move_links_off
        move_links_off(cx, tid, [rid])
        cx.execute("DELETE FROM biofield_auth_chain WHERE id=?", (rid,))
    else:
        cx.execute(
            "UPDATE biofield_auth_chain SET remedy='', dosage='', frequency='', "
            "timing='', schedule_slot='', updated_at=? WHERE id=?",
            (_now(), rid),
        )
    cx.commit()
    return True


def list_authored(cx):
    init_auth_tables(cx)
    cx.row_factory = sqlite3.Row
    rows = cx.execute("""
        SELECT t.id, t.name, t.email, t.date_test,
          (SELECT COUNT(*) FROM biofield_auth_chain c
             WHERE c.test_id=t.id AND TRIM(COALESCE(c.remedy,''))<>'') AS lc
        FROM biofield_auth_tests t ORDER BY t.id DESC""").fetchall()
    return [{"test_id": "a" + str(r["id"]), "name": r["name"] or "(unnamed)",
             "email": r["email"] or "", "date": r["date_test"] or "",
             "layer_count": r["lc"], "authored": True} for r in rows]


def _has(cx, table):
    if db.backend_of(cx) == "postgres":
        return cx.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=? LIMIT 1",
            (table,),).fetchone() is not None
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (table,)).fetchone() is not None


def _has_col(cx, table, col):
    """True only when `table` exists AND has column `col` (snapshot schemas vary)."""
    if not _has(cx, table):
        return False
    return db.column_exists(cx, table, col)


_CUSTOM_REMEDIES = (
    {
        "name": "Miasmatox Homeopathic Complex in Terrain Restore",
        "dosage": "10 drops",
        "frequency": "3 times a day",
        "timing": "30 minutes before food",
        "phase": "",
        "system": "",
        "discontinue_intent": False,
    },
)


def remedy_catalog(cx, q="", limit=20):
    """Search the snapshot plus locally maintained additions for the remedy picker."""
    query = (q or "").strip().lower()
    custom = [dict(r) for r in _CUSTOM_REMEDIES if query in r["name"].lower()]
    if not _has(cx, "fmp_snap_products"):
        return custom[:limit]
    cx.row_factory = sqlite3.Row
    like = f"%{(q or '').strip()}%"
    rows = cx.execute(
        "SELECT p.product_name AS name, p.dosage AS dosage, p.dosage_freq AS frequency, "
        "p.dosage_timing AS timing, "
        "(SELECT text FROM fmp_snap_products_phases ph WHERE ph.id_fk_product=p.id_pk LIMIT 1) AS phase, "
        "(SELECT text FROM fmp_snap_products_systems sy WHERE sy.id_fk_product=p.id_pk LIMIT 1) AS system "
        "FROM fmp_snap_products p "
        "WHERE TRIM(COALESCE(p.product_name,''))<>'' AND p.product_name LIKE ? "
        "ORDER BY p.product_name LIMIT ?", (like, limit)).fetchall()
    out = []
    for r in rows:
        d = {k: (r[k] or "") for k in ("name", "dosage", "frequency", "timing", "phase", "system")}
        # Surface the discontinue-intent marker as a flag, but hand the UI (and,
        # once picked, the chain + invoice + coverage) the clean name.
        d["discontinue_intent"] = _is_discontinue_intent(d["name"])
        d["name"] = _clean_product_name(d["name"])
        out.append(d)
    existing = {r["name"].lower() for r in out}
    out.extend(r for r in custom if r["name"].lower() not in existing)
    return sorted(out, key=lambda r: r["name"].lower())[:limit]


def infoceutical_names_by_code(cx):
    """{code: remedy-list name} for every infoceutical in the FileMaker product list, or None
    when there is no FileMaker list at all (an empty dict means the list carries none). The
    list the Intake's remedy picker and remedy_dosing read. Glen, 2026-10-01: a scan code
    becomes a remedy by its remedy-list name ("MB8 Love Infoceutical", not E4L's "Love
    Hologram"), so the dosing fills as it does when he picks from the dropdown. Only names
    that start with a code and say "Infoceutical". A product marked inactive in FileMaker is
    skipped; a discontinue-intent one is kept, as the picker keeps it (still sellable). When two
    names claim one code the first alphabetically wins."""
    import re as _re
    if not _has(cx, "fmp_snap_products"):
        return None                  # no FileMaker list at all: the caller may fall back
    cols = {r[1] for r in cx.execute("PRAGMA table_info(fmp_snap_products)")}
    where = "TRIM(COALESCE(product_name,''))<>''"
    if "active" in cols:
        where += " AND lower(TRIM(COALESCE(active,''))) <> 'no'"
    out = {}
    names = [r[0] for r in cx.execute(f"SELECT product_name FROM fmp_snap_products WHERE {where}")]
    for raw in sorted(names, key=lambda n: n.lower()):
        name = _clean_product_name(raw)
        m = _re.match(r"^([A-Z]{2,3}\d{1,3})\s", name)
        if m and "infoceutical" in name.lower() and is_infoceutical_code(m.group(1)):
            out.setdefault(m.group(1), name)
    return out


# The E4L families that are infoceuticals. ER (Rejuvenators, miHealth settings), MR, BFA,
# Environmental and Nutrition are findings, never remedies (Glen, 2026-10-01), whatever a
# product happens to be named. An allow-list, so a new family is excluded until named here.
INFOCEUTICAL_FAMILIES = ("ED", "EI", "ES", "ET", "MB")


def is_infoceutical_code(code):
    import re as _re
    m = _re.match(r"^([A-Z]{2,3})\d", (code or "").strip())
    return bool(m) and m.group(1) in INFOCEUTICAL_FAMILIES


# Doses Glen set in code, ahead of the FileMaker snapshot, so a fresh snapshot cannot
# overwrite them (Glen, 2026-10-25, formulation tab: "code"). B17 Max became a powder and
# FileMaker 890 still says 1 capsule; the three-field split is his ("yes", same day).
# Amygdalin products take one dose a day (Glen, 2026-10-31, production tab). B17 Max went
# from up to 2 level scoops to 1, and Breast Support from up to 6 capsules to 1. Splits from
# production/05 Formulations/b17-max/2026-10-31/one-dose-a-day.md.
_B17_MAX_STEPS = ("Increase slowly, as guided, up to 1 level scoop once a day. Do not "
                  "exceed 1 level scoop a day. If you also take B17 Syntropy, take no more "
                  "than 1 capsule a day, and count it as half a scoop toward the 1-scoop "
                  "limit.")
_DOSE_OVERRIDES = {
    "b17 max": {"dosage": "1/4 level scoop", "frequency": "once a day",
                "timing": "with cooked food. " + _B17_MAX_STEPS},
    "breast support": {"dosage": "1 capsule", "frequency": "once a day",
                       "timing": "with food, or as guided."},
    # Glen, 2026-11-10: "Take 1 capsule up to twice daily, between meals or at bedtime, or as
    # guided." The capsule's FileMaker row (350) is named "Alkalize Powder", so the name never matched.
    "alkalize bicarbonate blend": {"dosage": "1 capsule", "frequency": "up to twice daily",
                                   "timing": "between meals or at bedtime, or as guided."},
}


# Reveal remedy names diverge from FMP product names in ways a suffix match can't
# bridge. Glen-confirmed aliases (reveal name lowercased -> FMP product name).
# Extend as new divergences surface. See reference_reveal_dose_backfill.
_DOSE_ALIASES = {
    "synergy c": "Vitamin C Syntropy",         # FileMaker 333 renamed 3/17/2026
    "neuro magnesium": "Focus Neuro-Magnesium Powder",
    "neuro-magnesium": "Focus Neuro-Magnesium Powder",
    "neuroceramides": "Myelin Repair Neuroceramides",
    "serene blue green": "Serenity Blue Green Balance",
    "aller-free aid for inhalant allergies": "AllerFree HomeoEnergetic Drops",
    "holy grail full spectrum m-10 ormus": "Holy Grail Full Spectrum Drops",
    "gi repair helicobacter pylori terrain support": "GI Repair",
    "rejuvenation": "Rejuv Infoceutical",
    # Short infoceutical names that the prefix match sent to a DIFFERENT product
    # (Glen, 2026-09-22, Peach Goddard's layer 9: "Energy" was dosed as Energy Flow
    # Flower Essence, 10 drops 3 times a day). "Sleep" was reaching Sleep Syntropy,
    # a capsule formula, and "Night" was reaching Night Vision.
    "energy": "Energy/Source Infoceutical Feelgood",
    "night": "Night Infoceutical",
    "sleep": "Sleep Infoceutical",
    # Six Synergy names became Syntropy (2026-09-30). The rewrite below turns "Seacure
    # Synergy" into "Seacure Syntropy", which FileMaker never carried: 381 is now
    # "SeaAmino Syntropy". FileMaker 395 was renamed that morning, so a snapshot taken
    # before then still says "Vitamin D Synergy": try the new name, then the old.
    "seacure synergy": "SeaAmino Syntropy",
    "seacure syntropy": "SeaAmino Syntropy",   # what the rewrite below would try
    "vitamin d synergy powder": ("Vitamin D Syntropy", "Vitamin D Synergy"),
    "vitamin d syntropy": ("Vitamin D Syntropy", "Vitamin D Synergy"),
}


_ALLER_FREE_SPELLING = re.compile(r"\bAller(-?)Free\b", re.I)


def _spelling_variants(nm):
    """`nm`, then the same name with the other AllerFree spelling. Glen ruled "Aller-Free"
    the correct spelling (2026-09-24) and the drops were renamed, but the FMP snapshot
    still says "AllerFree" until it refreshes. Trying both covers before and after."""
    swapped = _ALLER_FREE_SPELLING.sub(
        lambda m: "AllerFree" if m.group(1) else "Aller-Free", nm)
    return [nm] if swapped == nm else [nm, swapped]


def _dose_row(cx, name):
    """A fmp_snap_products dose row for `name`: exact, else the SHORTEST forward-
    suffix product ('Adrenal Syntropy' -> 'Adrenal Syntropy Powder'). None if neither.
    Each stage also tries the other AllerFree / Aller-Free spelling. The row carries
    `id_pk` when the snapshot has it, so a caller can tell which FileMaker row it is."""
    nm = _clean_product_name(name)
    if not nm:
        return None
    variants = _spelling_variants(nm)
    cols = "dosage, dosage_freq AS frequency, dosage_timing AS timing"
    if _has_col(cx, "fmp_snap_products", "id_pk"):
        cols += ", id_pk"
    for v in variants:
        r = cx.execute(
            f"SELECT {cols} "
            "FROM fmp_snap_products WHERE LOWER(TRIM(RTRIM(product_name,'* ')))=LOWER(TRIM(?)) LIMIT 1",
            (v,)).fetchone()
        if r:
            return r
    for v in variants:
        r = cx.execute(
            f"SELECT {cols} "
            "FROM fmp_snap_products WHERE LOWER(TRIM(RTRIM(product_name,'* '))) LIKE LOWER(?) "
            "ORDER BY LENGTH(product_name) ASC LIMIT 1", (v + " %",)).fetchone()
        if r:
            return r
    return None


def remedy_dosing(cx, name):
    """Default dosing for a product name, to auto-fill a chain remedy. Resolves the
    reveal's remedy name to an FMP product in stages so a new reveal auto-fills the
    same way the 2026-07 backfill did: exact -> shortest forward-suffix (short reveal
    name vs the fuller FMP name) -> Synergy->Syntropy rename -> a small alias map for
    genuinely divergent names. Returns {dosage, frequency, timing}; all '' when
    unresolved (infoceuticals / E4L drivers have no physical dose)."""
    blank = {"dosage": "", "frequency": "", "timing": ""}
    clean_name = _clean_product_name(name).lower()
    for remedy in _CUSTOM_REMEDIES:
        if clean_name == remedy["name"].lower():
            return {k: remedy[k] for k in blank}
    if clean_name in _DOSE_OVERRIDES:
        return dict(_DOSE_OVERRIDES[clean_name])
    if not _has(cx, "fmp_snap_products"):
        return blank
    # "Seacure" is two products; the prefix match would pick one of them.
    if is_ambiguous_product_name(_clean_product_name(name)):
        return blank
    cx.row_factory = sqlite3.Row
    # A named alias is a deliberate decision, so it outranks the loose prefix match
    # inside _dose_row. Checked after it, an alias never ran whenever the prefix
    # match found SOME product, which is exactly when it was wrong.
    prod = _DOSE_ALIASES.get(_clean_product_name(name).lower())
    r = None
    for target in ((prod,) if isinstance(prod, str) else prod or ()):
        r = _dose_row(cx, target)
        if r is not None:
            break
    if r is None:
        r = _dose_row(cx, name)
    if r is None and re.search(r"\bsynergy\b", name or "", re.I):
        r = _dose_row(cx, re.sub(r"\bsynergy\b", "Syntropy", name, flags=re.I))
    return {k: (r[k] or "") for k in blank} if r else blank


def merge_dosing(dosage, frequency, timing, defaults):
    """Fill each EMPTY dose field from the catalog default, INDEPENDENTLY.

    An all-or-nothing fill (`if not (dosage or frequency or timing)`) meant a single
    spoken field suppressed the catalog for the other two, leaving whatever the LLM
    guessed. Real case: "Fiber Cleanse, one a day" -> the model supplied timing
    "with food" while the catalog says "with extra water, away from beneficial oils".
    A spoken value always wins; a blank one always comes from the catalog."""
    d = defaults or {}
    return {"dosage": (dosage or "").strip() or (d.get("dosage") or ""),
            "frequency": (frequency or "").strip() or (d.get("frequency") or ""),
            "timing": (timing or "").strip() or (d.get("timing") or "")}


def stress_vocab(cx, q="", limit=20):
    """Stress-factor terms for autocomplete: FMP snapshot terms UNION custom
    (glen-added) terms, deduped case-insensitively, filtered by q."""
    like = f"%{(q or '').strip()}%"
    have_fmp = _has(cx, "fmp_snap_client_active_main_stress")
    have_custom = _has(cx, "custom_stress_vocab")
    if not have_fmp and not have_custom:
        return []
    parts, params = [], []
    if have_fmp:
        parts.append("SELECT main_stress AS term FROM fmp_snap_client_active_main_stress "
                     "WHERE TRIM(COALESCE(main_stress,''))<>'' AND main_stress LIKE ?")
        params.append(like)
    if have_custom:
        parts.append("SELECT term FROM custom_stress_vocab "
                     "WHERE TRIM(COALESCE(term,''))<>'' AND term LIKE ?")
        params.append(like)
    sql = ("SELECT term FROM (" + " UNION ".join(parts) + ") "
           "GROUP BY LOWER(term) ORDER BY term LIMIT ?")
    params.append(limit)
    return [r[0] for r in cx.execute(sql, params).fetchall()]


def stress_suggestions(cx, stress, limit=8):
    """Remedies historically used for a given stress factor, most-used first."""
    if not (_has(cx, "fmp_snap_client_remedy") and _has(cx, "fmp_snap_client_causal_chain")
            and _has(cx, "fmp_snap_client_active_main_stress")):
        return []
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT r.remedy AS remedy, COUNT(*) AS n "
        "FROM fmp_snap_client_remedy r "
        "JOIN fmp_snap_client_causal_chain cc ON cc.id_pk=r.id_fk_causal_chain "
        "JOIN fmp_snap_client_active_main_stress ams ON ams.id_pk=cc.id_fk_active_stress "
        "WHERE LOWER(TRIM(ams.main_stress))=LOWER(TRIM(?)) AND TRIM(COALESCE(r.remedy,''))<>'' "
        "GROUP BY r.remedy ORDER BY n DESC, r.remedy LIMIT ?", (stress or "", limit)).fetchall()
    return [{"remedy": r["remedy"], "count": r["n"]} for r in rows]


def ordered_chain(cx, tid):
    """Chain rows in display order, numbered by their stored layer.

    Rows with a Head or Tail but no remedy are retained as empty layer anchors.
    Unbalanced scan rows (origin='scan' AND confirmed=0) still carry zone='bottom'
    so the editor can style them as needs-review, but they are numbered in place
    like any other layer.  They used to be forced to the end, which meant a layer
    the practitioner deliberately added after them (stored layer 7, say) was
    hoisted above them and renumbered to 1.  Display `layer` = 1..k."""
    cx.row_factory = sqlite3.Row
    # layer_balanced arrives with init_auth_tables; a caller that never ran it reads 0.
    lb = ("COALESCE(layer_balanced,0)" if _has_col(cx, "biofield_auth_chain", "layer_balanced")
          else "0")
    rows = cx.execute(
        "SELECT id, layer, head, most_affected, remedy, dosage, frequency, timing, "
        f"schedule_slot, bottles, {lb} AS layer_balanced, "
        "confirmed, origin FROM biofield_auth_chain "
        "WHERE test_id=? AND (TRIM(COALESCE(remedy,''))<>'' "
        "OR TRIM(COALESCE(head,''))<>'' OR TRIM(COALESCE(most_affected,''))<>'')",
        (_num(tid),)).fetchall()

    def unbalanced_scan(r):
        return (r["origin"] == "scan") and (r["confirmed"] == 0)

    key = lambda r: (r["layer"] is None, r["layer"] if r["layer"] is not None else 0, r["id"])
    out = []
    for i, r in enumerate(sorted(rows, key=key), 1):
        out.append({"id": r["id"], "layer": i, "stored_layer": r["layer"],
                    "head": r["head"] or "",
                    "most_affected": r["most_affected"] or "", "remedy": r["remedy"] or "",
                    "dosage": r["dosage"] or "", "frequency": r["frequency"] or "",
                    "timing": r["timing"] or "",
                    "schedule_slot": r["schedule_slot"] or "",
                    "bottles": r["bottles"],
                    "confirmed": 0 if r["confirmed"] == 0 else 1,
                    "layer_balanced": 1 if r["layer_balanced"] else 0,
                    "origin": r["origin"] or "live",
                    "zone": "bottom" if unbalanced_scan(r) else "top"})
    return out


def reorder_chain(cx, tid, rid, new_layer):
    """Move row `rid` to position `new_layer` and renumber the chain contiguously.
    Needs-review scan rows keep that status; they are renumbered in place along with
    everything else rather than being held at the end."""
    ids = [l["id"] for l in ordered_chain(cx, tid)]
    if rid not in ids:
        return
    ids.remove(rid)
    pos = max(1, min(int(new_layer or 1), len(ids) + 1)) - 1
    ids.insert(pos, rid)
    for i, _id in enumerate(ids, 1):
        cx.execute("UPDATE biofield_auth_chain SET layer=? WHERE id=?", (i, _id))
    cx.commit()


def set_layer_order(cx, tid, groups):
    """groups = ordered list of layer groups, each a list of chain-row ids. Assign
    stored layer = group position (1-based) to every row in that group so ordered_chain
    presents the groups (and their remedies) in this order.

    Deliberately placing a card also confirms its rows (confirmed=1) so an unbalanced
    scan row honours its new position instead of snapping back to the bottom zone --
    arranging a card counts as reviewing it. Rows already confirmed are unaffected."""
    init_auth_tables(cx)               # unmatched_fields on an older table
    for i, rids in enumerate(groups or [], 1):
        for rid in rids or []:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            cx.execute("UPDATE biofield_auth_chain SET layer=?, confirmed=1, unmatched_fields=NULL "
                       "WHERE id=? AND test_id=?", (i, rid, _num(tid)))
    cx.commit()


def authored_report(cx, tid):
    init_auth_tables(cx)
    cx.row_factory = sqlite3.Row
    t = cx.execute("SELECT * FROM biofield_auth_tests WHERE id=?", (_num(tid),)).fetchone()
    layers = [{**l, "rid": l["id"]} for l in ordered_chain(cx, tid)]
    # Depth-of-penetration tags + reach match-check per layer (Increment 4b)
    for l in layers:
        sd = get_tag(cx, "auth_stress", l["rid"], DEPTH_KEY)
        rd = get_tag(cx, "auth_remedy", l["rid"], DEPTH_KEY)
        l["stress_depth"] = sd
        l["remedy_depth"] = rd
        l["depth_status"] = depth_match(sd, rd)
        l["depth_need"] = depth_label(cx, sd)
    schedule = build_schedule([
        {"name": l["remedy"], "dosage": l["dosage"], "frequency": l["frequency"],
         "timing": l["timing"], "schedule_slot": l["schedule_slot"],
         "source_rids": [l["rid"]]} for l in layers if (l["remedy"] or "").strip()])
    tk = t.keys() if t else []
    return {"test_id": str(tid),
            "client": {"name": (t["name"] if t else "") or "",
                       "email": (t["email"] if t else "") or "",
                       "e4l_client_id": ((t["e4l_client_id"] if t and "e4l_client_id" in tk
                                          else "") or "")},
            "date": (t["date_test"] if t else "") or "",
            "phase": (t["phase"] if "phase" in tk else None),
            "location": ((t["location"] if "location" in tk else "") or ""),
            "bsi": [(t[c] if c in tk else None) for c in BSI_COLS],
            "bsi_location": ((t["bsi_location"] if "bsi_location" in tk else "") or ""),
            "terrain_review": _terrain_review(t["terrain_review"]
                                              if "terrain_review" in tk else None),
            "layers": layers, "schedule": schedule}
