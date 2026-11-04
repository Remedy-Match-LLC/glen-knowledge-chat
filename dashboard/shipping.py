"""Shipping — USPS Flat Rate auto-update + box-fit matrix for manual orders.

Three tables on the existing chat_log.db (persistent /data disk on Render):

  bottle_types   — Glen/Rae catalog of SKU shapes (e.g., "dropper 1oz")
  box_capacity   — per (bottle_type, box_size) → max qty that fits
  usps_rates     — historical USPS prices + Glen's charged price after the
                   rounding rule (up to the next whole dollar). Each rate row
                   carries `confirmed_by`. The daily watcher applies a USPS
                   change at once and emails Glen (Glen, 2026-11-03: "at
                   once"); a manual entry at /admin/shipping is still staged
                   and confirmed by hand.
                   The `usps_retail_cents` column holds the price the charge
                   follows: USPS Commercial Pricing since 2026-11-03.
  usps_watch_state — the watcher's last good read and last failure alert.

Public surface used by app.py routes + the order-entry page:

  init_shipping_schema(cx)      — idempotent migration
  round_to_dollar(cents)        — Glen's rounding rule
  pick_box({bottle_name: qty})  — smallest S/M/L box that fits the order
  quote({bottle_name: qty})     — pick_box + current charged shipping cost
  get_current_rates()           — latest confirmed rate per box size
  list_bottle_types() / add_bottle_type() / delete_bottle_type()
  get_capacity_matrix() / set_box_capacity()
  propose_rate_update() / list_pending_rate_updates() / confirm_rate_update()
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dashboard import packing
from dashboard import dbwrite


BOX_SIZES = ("S", "M", "L")
PENDING = "pending"


class UnknownBottleType(Exception):
    """Raised when an order references a bottle type not in the catalog."""


# ── Path resolution ───────────────────────────────────────────────────────────

def _default_db_path() -> str:
    """Same resolution rule as app.LOG_DB so tests + prod both work."""
    base = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent))
    return str(Path(base) / "chat_log.db")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    from dashboard import db
    cx = db.connect(db_path or _default_db_path())
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    return cx


# ── Schema ────────────────────────────────────────────────────────────────────

# Current confirmed rates as of the April 26, 2026 USPS price adjustment
# (runs through Jan 17, 2027). These get seeded on first init so the order
# tool works immediately after deploy. Glen can override via /admin/shipping.
_DEFAULT_RATES_2026_04_26 = [
    # (box_size, usps_retail_cents, source_url, effective_date)
    ("S", 1265, "https://www.usps.com/business/prices.htm", "2026-04-26"),
    ("M", 2295, "https://www.usps.com/business/prices.htm", "2026-04-26"),
    ("L", 3150, "https://www.usps.com/business/prices.htm", "2026-04-26"),
]

# ── Bottle vocabulary ────────────────────────────────────────────────────────
# THE NAMES BELOW ARE PROD'S. Prod's `bottle_types` table was hand-built through
# /admin/shipping; `_STANDARD_BOTTLES` only ever seeds a FRESH catalog, so prod never
# received the repo's old private names ('30cap', '5ml', ...). A bottle_type prod does
# not recognise is exactly as broken as none: pick_boxes raises UnknownBottleType,
# quote() returns no rate, and _shipping_for_cart silently drops the WHOLE cart to the
# coarse qty rule. Never introduce a name outside PROD_BOTTLE_NAMES without creating it
# in prod first. Verified against GET /api/shipping/bottles on 2026-07-09.
PROD_BOTTLE_NAMES = frozenset({
    "30 Caps", "120 caps", "180 caps", "360 caps", "30 g", "120 g",
    "30ml", "Dropper 5 mL", "Dropper 30 mL", "Dropper 50 mL",
    # Created in prod 2026-07-09. Before that the catalog named bottles prod did not
    # have, so `neem-oil-rollon` and `hand-cradle` silently fell back to the qty rule.
    "100ml", "15ml", "30roll", "handcradle",
    # Created in prod 2026-07-10 (id 17) for the wicking toothbrush (Ø30 x H160).
    "toothbrush",
    # Created in prod 2026-07-10 (ids 18-20) for device/accessory SKUs. `own-box` is a
    # generic oversized type: too big for USPS flat rate, so the packer routes it to its
    # own parcel (never mis-rated, never the "default" placeholder).
    "harmony-laser", "dowsing-rods", "own-box",
    # Created in prod 2026-07-10 (ids 21-23) for the book/device batch add: paperback
    # books, the NIR intranasal clip, and the DENAS PCM Pro. (miHealth + Kloud mats are
    # drop-shipped by the vendor, so they use `own-box`, not a dedicated type.)
    "book", "nasal-clip", "denas",
    # Created in prod 2026-07-11 (id 24) for the two nightlight SKUs (5 x 5 x 6 cm each).
    "nightlight",
    # One Step tub. Unlike the rows above (hand-created via /admin/shipping), this one is
    # backfilled into prod's bottle_types by init_shipping_schema on deploy — the same
    # code-guaranteed ensure-insert used for 30ml — so prod knows it without a manual step.
    "one-step",
    # Glen, 2026-09-23: the jar for the small pure powders (5-MTHF, Adenosyl Cobalamin).
    # Backfilled on deploy like one-step, so prod knows it without a manual step.
    "Small powder jar",
})

# Types the catalog needs that prod's library lacks. Empty: the last four were created.
# A non-empty set means someone introduced a bottle the packer cannot resolve — create
# it in prod (POST /api/shipping/bottles) before shipping the baseline.
PENDING_BOTTLE_NAMES = frozenset()

# The cello-refill packing unit (see `packing_bottle_type` below). Deliberately NOT
# added to PROD_BOTTLE_NAMES/PENDING_BOTTLE_NAMES: those two sets track names that
# appear as a *product's* `bottle_type` in the catalog (verified against prod's live
# library / staged for creation there). `cello-refill` is never a product's stored
# bottle_type — it's computed per cart line from the `format` field — so it doesn't
# belong to that vocabulary. Prod's `bottle_types` table is hand-built and
# authoritative at runtime; Rae must add this row (dims or capacity) via
# /admin/shipping before prod can rate it. The `_STANDARD_BOTTLES` seed below makes
# it testable and keeps a fresh/dev catalog resolvable; if the row is absent in prod,
# `pick_boxes` raises UnknownBottleType for it exactly like any other missing type —
# `quote()` catches that internally (see below) and returns shipping_cents: None,
# it never propagates out to a caller's except.
CELLO_BOTTLE_TYPE = "cello-refill"

# Old repo-private names -> prod's. Applied by init_shipping_schema to any pre-existing
# catalog so a dev DB stops speaking a vocabulary prod never had. `100cos` predates both.
_LEGACY_BOTTLE_RENAMES = {
    "100cos": "30 g",
    "30g": "30 g",
    "30cap": "30 Caps",
    "120cap": "120 caps",
    "5ml": "Dropper 5 mL",
    "50ml": "Dropper 50 mL",
}

# Bottle types with measured dims (Ø_mm, H_mm) = cm x 10. Dims for the ten prod names
# are prod's own measurements, read from its bottle_types table.
_STANDARD_BOTTLES = [
    ("30 Caps", "100 ml wide-mouth (30 caps)", 51, 90),
    ("120 caps", "250 ml wide-mouth (120 caps / pure powder)", 72, 100),
    ("180 caps", "180 caps", 80, 155),
    ("360 caps", "360 caps", 93, 200),
    ("30 g", "100 ml cosmetic jar (30 g powder)", 65, 75),
    ("120 g", "120 g jar", 72, 100),
    ("30ml", "30 ml dropper (infoceutical)", 40, 110),
    ("Dropper 5 mL", "5 ml dropper (eye drops)", 23, 75),
    ("Dropper 30 mL", "30 ml dropper", 32, 100),
    ("Dropper 50 mL", "50 ml dropper", 35, 135),
    # Created in prod 2026-07-09 (dims below are what was created there).
    ("100ml", "100 ml dropper", 50, 160),
    ("15ml", "15 ml dropper", 30, 100),
    ("30roll", "30 ml roll-on", 40, 100),
    # Shipping proxy: the hand cradle is a flat device, not a bottle, but the packer
    # models cylinders. These dims resolve one cradle to the USPS Medium Flat Rate box
    # (M, $22.95) — the default for a $297 device shipped with its protective packaging.
    # Rae can manually drop to Small at pack time when shipping the cradle bare.
    ("handcradle", "ZYTO Hand Cradle — ships USPS Medium Flat Rate", 80, 100),
    # Shipping proxy: a wicking toothbrush is a flat item, not a cylinder. Glen's dims
    # (16 x 3 cm) as a bounding cylinder (Ø30 x H160). Created in prod 2026-07-10 (id 17).
    ("toothbrush", "Wicking toothbrush — 16 x 3 cm (cylinder bounding)", 30, 160),
    # Device/accessory SKUs, created in prod 2026-07-10 (ids 18-20).
    ("harmony-laser", "Harmony Laser — 6 x 6 x 20 cm (→ USPS Medium)", 60, 200),
    ("dowsing-rods", "Dowsing rods — small flat item (assumed ~20 cm)", 35, 200),
    # Oversized: bigger than the L box on every axis, so pick_box returns None and the
    # item is quoted as its own parcel. Used for the water ionizer ("ships own box").
    ("own-box", "Own-parcel oversized item (ships in its own box)", 250, 450),
    # Book/device batch add 2026-07-10. Books are flat, not cylinders; a single paperback
    # bounds to Ø160 x H40 so one/few resolve to USPS Medium (Rae ships Media Mail or
    # adjusts at pack time for larger quantities). The nasal clip is a small item; the
    # DENAS PCM Pro is a handheld device boxed like the Harmony laser.
    ("book", "Paperback book — single/few → USPS Medium; Rae adjusts / Media Mail", 160, 40),
    ("nasal-clip", "NIR intranasal clip — small item", 30, 60),
    ("denas", "DENAS PCM Pro microcurrent — handheld boxed device", 60, 160),
    # Nightlights: 5 x 5 x 6 cm each. The 50 mm width exceeds Small's usable interior
    # (50 - 5 margin = 45 mm), so a single unit bounds to USPS Medium; 2-3 still fit one
    # Medium. Created in prod 2026-07-11 (id 24). Shared by both nightlight SKUs.
    ("nightlight", "Therapeutic / Biocompatible nightlight — 5 x 5 x 6 cm → USPS Medium", 50, 60),
    # One Step meal-replacement tub. Real tub ~Ø140 x H190 mm; per Glen it ships USPS
    # Medium (single) / Large (bulk) and needs no packing wrap. A true Ø140 exceeds every
    # box interior and would rate as None, so this is a SHIPPING PROXY: Ø120 lets the 3+
    # multi-box geometry place units. The 1-vs-2 box choice is pinned by an explicit
    # capacity cap seeded below (Medium=1, Large=2) — geometry alone would tile two into
    # one Medium, but two tubs need a Large.
    ("one-step", "One Step tub — ~Ø140 x H190 real; per Glen USPS Medium (1) / Large (2), no packing — proxy Ø120 + cap M=1/L=2", 120, 190),
    # Glen measured it 2026-09-23: jar Ø35 x H30 mm. Its scoop is a thin stick,
    # 70 x 2 x 5 mm (about 0.7 cm3), that slides between jars, so the jar alone is the
    # packing cylinder. (The 500 mg scoop, 1.5 x 1 x 6 cm, goes with the 30 g and 45 g
    # powders, not this jar.)
    ("Small powder jar", "Small powder jar Ø35 x H30 mm (~29 mL); its scoop is a thin 70x2x5 mm stick packed alongside. Glen 2026-09-23", 35, 30),
    # Cello refill pack: a 30-cap cellophane pouch (no rigid bottle), approximated as a
    # tight bounding cylinder — packs smaller than the "30 Caps" rigid bottle (Ø51x90) it
    # replaces for refill-format lines. NOT YET created in prod; Rae must add this row
    # via /admin/shipping before prod can rate it (see CELLO_BOTTLE_TYPE note above).
    (CELLO_BOTTLE_TYPE, "Cellophane refill pack (30 caps, no bottle)", 35, 60),
]
# Live prod values (GET /api/shipping/packing-settings). wrap_mm set to 5 on
# 2026-07-16: at wrap 6 the Ø40 glass droppers (30ml infoceutical, 30roll) came to
# 46mm and missed Small's 45mm usable width by 1mm → bumped to Medium (inflated
# shipping). Glen confirmed they physically fit Small; wrap 5 (40+5=45) resolves
# them to Small. Audit: ONLY 30ml + 30roll change (M→S); all caps/jars/devices
# unchanged. Bottle dims are left as measured (glass — real sizes). Prod DB value
# is authoritative at runtime; this is the dev/fresh-DB fallback.
_PACKING_DEFAULTS = {"wrap_mm": 5, "box_margin_mm": 5}
_PACKING_KEYS = ("wrap_mm", "box_margin_mm")


def init_shipping_schema(cx: sqlite3.Connection) -> None:
    """Create the three shipping tables. Idempotent.

    Seeds the April 26 2026 USPS Flat Rate prices on first init (only when
    the usps_rates table is empty) so the order tool works immediately on
    a fresh deploy.
    """
    from dashboard import db
    from dashboard.dbwrite import insert_or_ignore
    pg = db.backend_of(cx) == "postgres"
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS bottle_types (
                id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name        TEXT    NOT NULL UNIQUE,
                notes       TEXT,
                created_at  TEXT    NOT NULL DEFAULT (now()::text)
            )
        """)
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS bottle_types (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                notes       TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
    cx.execute("""
        CREATE TABLE IF NOT EXISTS box_capacity (
            bottle_type_id  INTEGER NOT NULL,
            box_size        TEXT    NOT NULL CHECK (box_size IN ('S','M','L')),
            qty             INTEGER NOT NULL CHECK (qty > 0),
            PRIMARY KEY (bottle_type_id, box_size),
            FOREIGN KEY (bottle_type_id) REFERENCES bottle_types(id) ON DELETE CASCADE
        )
    """)
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS usps_rates (
                id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                box_size            TEXT    NOT NULL CHECK (box_size IN ('S','M','L')),
                usps_retail_cents   INTEGER NOT NULL,
                charged_cents       INTEGER NOT NULL,
                effective_date      TEXT    NOT NULL,
                source_url          TEXT,
                confirmed_by        TEXT,
                confirmed_at        TEXT,
                created_at          TEXT    NOT NULL DEFAULT (now()::text)
            )
        """)
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS usps_rates (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                box_size            TEXT    NOT NULL CHECK (box_size IN ('S','M','L')),
                usps_retail_cents   INTEGER NOT NULL,
                charged_cents       INTEGER NOT NULL,
                effective_date      TEXT    NOT NULL,
                source_url          TEXT,
                confirmed_by        TEXT,
                confirmed_at        TEXT,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_usps_rates_size_date "
        "ON usps_rates(box_size, effective_date)"
    )

    # Add dimension columns to bottle_types if missing (idempotent migration)
    if pg:
        cols = {r[0] for r in cx.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='bottle_types' AND table_schema=current_schema()"
        ).fetchall()}
    else:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(bottle_types)")}
    if "diameter_mm" not in cols:
        cx.execute("ALTER TABLE bottle_types ADD COLUMN diameter_mm INTEGER")
    if "height_mm" not in cols:
        cx.execute("ALTER TABLE bottle_types ADD COLUMN height_mm INTEGER")

    # Migrate legacy names onto PROD's vocabulary (see PROD_BOTTLE_NAMES). Prod itself is
    # already on these names; this repairs dev/older catalogs so one vocabulary exists
    # everywhere. Never clobber a target that already exists.
    have = {r[0] for r in cx.execute("SELECT name FROM bottle_types").fetchall()}
    for _old, _new in _LEGACY_BOTTLE_RENAMES.items():
        if _old in have and _new not in have:
            cx.execute("UPDATE bottle_types SET name=? WHERE name=?", (_new, _old))
            have.discard(_old); have.add(_new)
    # Ensure 30ml exists with dims (insert if missing) — only on existing (non-empty) catalogs;
    # fresh DBs get it via the seed block below.
    if have and "30ml" not in have:
        cx.execute("INSERT INTO bottle_types (name, notes, diameter_mm, height_mm) "
                   "VALUES ('30ml', '30 ml dropper (infoceutical)', 40, 110)")
    # Same backfill for the One Step tub, so prod's existing catalog rates it after deploy
    # without a manual /admin/shipping step (dims + rationale: see _STANDARD_BOTTLES).
    if have and "one-step" not in have:
        cx.execute("INSERT INTO bottle_types (name, notes, diameter_mm, height_mm) "
                   "VALUES ('one-step', 'One Step tub — proxy Ø120 x H190 → USPS Medium (1) / Large (bulk)', 120, 190)")
    # And the small powder jar (Glen 2026-09-23), for the same reason.
    if have and "Small powder jar" not in have:
        cx.execute("INSERT INTO bottle_types (name, notes, diameter_mm, height_mm) "
                   "VALUES (?, ?, ?, ?)", ("Small powder jar", "Small powder jar Ø35 x H30 mm (~29 mL); its scoop is a thin 70x2x5 mm stick packed alongside. Glen 2026-09-23", 35, 30))

    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS product_bottle_types (
                slug         TEXT PRIMARY KEY,
                bottle_type  TEXT NOT NULL,
                updated_at   TEXT NOT NULL DEFAULT (now()::text)
            )
        """)
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS product_bottle_types (
                slug         TEXT PRIMARY KEY,
                bottle_type  TEXT NOT NULL,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    cx.execute("""
        CREATE TABLE IF NOT EXISTS usps_watch_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cx.execute("""
        CREATE TABLE IF NOT EXISTS packing_settings (
            key   TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
    """)
    for k, v in _PACKING_DEFAULTS.items():
        insert_or_ignore(cx, "packing_settings", ["key", "value"], [k, v],
                          conflict_cols=["key"])

    # Seed the standard bottle types with dims only on a fresh catalog
    has_bottles = cx.execute("SELECT 1 FROM bottle_types LIMIT 1").fetchone()
    if not has_bottles:
        for name, notes, d_mm, h_mm in _STANDARD_BOTTLES:
            cx.execute(
                "INSERT INTO bottle_types (name, notes, diameter_mm, height_mm) "
                "VALUES (?, ?, ?, ?)",
                (name, notes, d_mm, h_mm),
            )

    # One Step capacity caps (Glen 2026-07-22). The tub is bulky, not a tight cylinder:
    # pure geometry would tile TWO into one Medium. Cap it — Medium holds 1, Large holds 2
    # — so 1 unit -> Medium and 2 -> a single Large. The proxy dims above still drive the
    # 3+ multi-box split (geometry then re-split under these caps). Runs on fresh AND
    # existing catalogs (the row exists via the seed or the backfill above); insert-or-
    # ignore so a later manual /admin/shipping tweak is never clobbered.
    _os = cx.execute("SELECT id FROM bottle_types WHERE name='one-step'").fetchone()
    if _os:
        _osid = _os[0]
        for _bs, _q in (("M", 1), ("L", 2)):
            insert_or_ignore(cx, "box_capacity",
                             ["bottle_type_id", "box_size", "qty"],
                             [_osid, _bs, _q],
                             conflict_cols=["bottle_type_id", "box_size"])

    # Small powder jar, Small-box count (2026-09-23), DERIVED from geometry (20 fit a
    # Small), not counted by Rae. Seeding it lets a mixed order use the fractional
    # matrix (e.g. 4 jars + 4 x 30 Caps = 4/20 + 4/6 -> Small) instead of falling to
    # geometry, which over-boxes the Ø51 30-cap bottle to Medium. M and L stay with
    # geometry, as they do for 30 Caps. Insert-or-ignore: a later /admin/shipping edit
    # by Rae wins.
    _sj = cx.execute("SELECT id FROM bottle_types WHERE name='Small powder jar'").fetchone()
    if _sj:
        insert_or_ignore(cx, "box_capacity", ["bottle_type_id", "box_size", "qty"],
                         [_sj[0], "S", 20], conflict_cols=["bottle_type_id", "box_size"])

    # Mixed FF + Infoceutical Small-box fit, confirmed by Rae 2026-08-11:
    # four 30-capsule FF bottles plus two 30ml Infoceuticals fit one Small.
    # Both types therefore occupy one-sixth of a Small in the fractional mixed-
    # order model: 4/6 + 2/6 = 1.0. Prod already has Rae's 30 Caps S=6 row,
    # while 30ml previously had dimensions only; ensuring both makes the rule
    # deterministic on prod and fresh databases. INSERT OR IGNORE preserves any
    # later operator adjustment made through /admin/shipping.
    for _type_name in ("30 Caps", "30ml"):
        _bt = cx.execute(
            "SELECT id FROM bottle_types WHERE name=?", (_type_name,)).fetchone()
        if _bt:
            insert_or_ignore(cx, "box_capacity",
                             ["bottle_type_id", "box_size", "qty"],
                             [_bt[0], "S", 6],
                             conflict_cols=["bottle_type_id", "box_size"])

    # First-run seed: only if the table is empty
    has_any = cx.execute("SELECT 1 FROM usps_rates LIMIT 1").fetchone()
    if not has_any:
        now = datetime.now(timezone.utc).isoformat()
        for size, retail, source, eff in _DEFAULT_RATES_2026_04_26:
            cx.execute("""
                INSERT INTO usps_rates
                    (box_size, usps_retail_cents, charged_cents, effective_date,
                     source_url, confirmed_by, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (size, retail, round_to_dollar(retail), eff, source, "seed", now))

    cx.commit()


# ── Rounding rule ─────────────────────────────────────────────────────────────

def round_to_dollar(cents: int) -> int:
    """Round up to the next whole dollar; a whole dollar stays. Glen, 2026-11-03: "We
    round the exact price up to the next even dollar amount" ($32.75 -> $33). It was
    the nearest dollar before, which charged $32 for a $32.49 box."""
    dollars, remainder = divmod(int(cents), 100)
    if remainder:
        dollars += 1
    return dollars * 100


# ── Box-fit logic ─────────────────────────────────────────────────────────────

def _capacity_lookup(cx: sqlite3.Connection) -> Dict[str, Dict[str, int]]:
    """Returns {bottle_name: {S: qty, M: qty, L: qty}}."""
    out: Dict[str, Dict[str, int]] = {}
    rows = cx.execute("""
        SELECT bt.name AS name, bc.box_size AS size, bc.qty AS qty
        FROM bottle_types bt
        JOIN box_capacity bc ON bc.bottle_type_id = bt.id
    """).fetchall()
    for r in rows:
        out.setdefault(r["name"], {})[r["size"]] = r["qty"]
    return out


def _expand_items(bottles_by_type, dims):
    """Flatten {type: qty} into a list of (d_mm, h_mm) plus a parallel list of
    type names (same order) so caps can be checked per box."""
    items, names = [], []
    for name, qty in bottles_by_type.items():
        d = dims[name]
        for _ in range(int(qty)):
            items.append(d)
            names.append(name)
    return items, names


def _caps_ok(box_size, names, placed_idx, caps):
    """True if the placed subset honours every (type, box) override cap."""
    from collections import Counter
    counts = Counter(names[i] for i in placed_idx)
    for tname, n in counts.items():
        cap = caps.get(tname, {}).get(box_size)
        if cap is not None and n > cap:
            return False
    return True


def pick_boxes(bottles_by_type, db_path: Optional[str] = None):
    """Box selection. Returns a list of box sizes, or None.

    Precedence — the capacity matrix is authoritative wherever Rae has filled it
    in (app.py documents the catalog as taking precedence):

    - If every requested type has a capacity-matrix row -> use those counts
      (fractional fill, multi-box for large loads). This wins even for a type
      that ALSO has dimensions. Why it must: a 30-cap FF bottle is Ø51, wider
      than the Small box's 50 mm interior, so geometric packing places 0 in
      Small and over-boxes every 1-6 bottle order to Medium — but the matrix
      (S=6) is the operator's ground truth.
    - Else if every requested type has dimensions -> geometric split (multi-box),
      honouring any (type, box) override caps from box_capacity.
    - Else (a mix with no full coverage either way) -> legacy single-box
      fractional.
    Raises UnknownBottleType if a type is in neither the dims set nor the
    capacity matrix.
    """
    if not bottles_by_type:
        return None
    dims = get_bottle_dims(db_path=db_path)
    with _connect(db_path) as cx:
        caps = _capacity_lookup(cx)

    for name in bottles_by_type:
        if name not in dims and name not in caps:
            raise UnknownBottleType(name)

    # Matrix first for the common single-box case: if every requested type has a
    # matrix row AND the whole load fits one box by Rae's counts, use that box —
    # even for a type that also has dims. This is the fix: a 30-cap bottle is Ø51,
    # wider than the Small box's 50mm interior, so geometry places 0 in Small and
    # over-boxes 1-6 bottle orders to Medium, but the matrix (S=6) is ground truth.
    # A load too big for a single matrix box falls through to geometry (correct
    # multi-box split for dimmed types) / the legacy caps-only path, unchanged.
    if all(name in caps for name in bottles_by_type):
        single = _pick_box_fractional(bottles_by_type, caps)
        if single:
            return [single]

    if all(name in dims for name in bottles_by_type):
        settings = get_packing_settings(db_path=db_path)
        wrap, margin = settings["wrap_mm"], settings["box_margin_mm"]
        items, names = _expand_items(bottles_by_type, dims)
        # Geometric split; then verify each chosen box honours override caps.
        boxes = packing.split_into_boxes(items, wrap_mm=wrap, box_margin_mm=margin)
        if boxes is None:
            return None
        if not _caps_violated(items, names, caps, wrap, margin):
            return boxes
        return _split_with_caps(items, names, caps, wrap, margin)

    # Mixed: some type has dims but no matrix row, and not all are capped.
    # Legacy single-box fractional (raises UnknownBottleType for an uncapped
    # type, caught upstream -> qty rule), exactly as before this change.
    legacy = _pick_box_fractional(bottles_by_type, caps)
    return [legacy] if legacy else None


def _split_with_caps(items, names, caps, wrap, margin):
    """Greedy split that also respects override caps per box. Fills boxes one at
    a time, choosing the largest placement that satisfies caps; sizes the final
    box down. Returns list of box sizes or None."""
    remaining = list(range(len(items)))
    out = []
    while remaining:
        sub_items = [items[i] for i in remaining]
        sub_names = [names[i] for i in remaining]
        # smallest single box that fits all AND honours caps
        chosen = None
        for s in packing.BOX_ORDER:
            placed = packing.fit_subset(sub_items, packing.BOXES_MM[s],
                                        wrap_mm=wrap, box_margin_mm=margin)
            if len(placed) == len(sub_items) and _caps_ok(s, sub_names, placed, caps):
                chosen = s
                break
        if chosen:
            out.append(chosen)
            break
        # else pack into an L, dropping any bottle that would break a cap
        placed = packing.fit_subset(sub_items, packing.BOXES_MM["L"],
                                    wrap_mm=wrap, box_margin_mm=margin)
        placed = _trim_to_caps("L", sub_names, placed, caps)
        if not placed:
            return None
        out.append("L")
        placed_global = {remaining[k] for k in placed}
        remaining = [i for i in remaining if i not in placed_global]
    return out


def _trim_to_caps(box_size, names, placed_idx, caps):
    """Drop indices from a placed set until every cap is honoured."""
    from collections import Counter
    placed = set(placed_idx)
    counts = Counter(names[i] for i in placed)
    for tname, n in list(counts.items()):
        cap = caps.get(tname, {}).get(box_size)
        if cap is not None and n > cap:
            drop = [i for i in placed if names[i] == tname][cap:]
            placed.difference_update(drop)
    return placed


def _caps_violated(items, names, caps, wrap, margin):
    """Quick check: would the cap-free split place more of any type in a single
    box than its cap allows? Conservative — if any single box could exceed a
    cap, return True to route through _split_with_caps."""
    if not caps:
        return False
    # If no cap applies to any present type, nothing to enforce.
    present = set(names)
    return any(present & set(caps) for _ in (0,)) and any(
        any(s in caps.get(t, {}) for s in packing.BOX_ORDER) for t in present
    )


def _pick_box_fractional(bottles_by_type, caps):
    """Legacy fractional-fill: smallest box where sum(qty/capacity) <= 1.0."""
    for name in bottles_by_type:
        if name not in caps:
            raise UnknownBottleType(name)
    for size in BOX_SIZES:
        total_fill = 0.0
        ok = True
        for name, qty in bottles_by_type.items():
            cap = caps[name].get(size)
            if cap is None or cap <= 0:
                ok = False
                break
            total_fill += qty / cap
        if ok and total_fill <= 1.0:
            return size
    return None


def pick_box(bottles_by_type, db_path: Optional[str] = None):
    """Smallest single box that fits, or None (multi-box -> None). Geometric
    when all types have dims; legacy fractional otherwise."""
    boxes = pick_boxes(bottles_by_type, db_path=db_path)
    if boxes and len(boxes) == 1:
        return boxes[0]
    return None


# ── Rate retrieval ────────────────────────────────────────────────────────────

def get_current_rates(db_path: Optional[str] = None) -> Dict[str, dict]:
    """Latest confirmed rate per box size.

    Returns {S: {usps_retail_cents, charged_cents, effective_date, ...}, M: ..., L: ...}
    """
    out: Dict[str, dict] = {}
    with _connect(db_path) as cx:
        for size in BOX_SIZES:
            row = cx.execute("""
                SELECT id, box_size, usps_retail_cents, charged_cents,
                       effective_date, source_url, confirmed_by, confirmed_at
                FROM usps_rates
                WHERE box_size = ? AND confirmed_by IS NOT NULL AND confirmed_by != ?
                ORDER BY effective_date DESC, id DESC
                LIMIT 1
            """, (size, PENDING)).fetchone()
            if row:
                out[size] = dict(row)
    return out


# ── Quote (used by the order-entry page) ──────────────────────────────────────

def quote(bottles_by_type, db_path: Optional[str] = None) -> dict:
    """pick_boxes + current rates -> a single UI/checkout payload.

    Single box: {box_size, box_sizes:[size], shipping_cents, box_breakdown}.
    Multi box:  box_sizes has >1 entry; shipping_cents is the summed charged
                rate; box_breakdown lists each box + its charged_cents.
    """
    try:
        boxes = pick_boxes(bottles_by_type, db_path=db_path)
    except UnknownBottleType as e:
        return {"box_size": None, "box_sizes": [], "shipping_cents": None,
                "error": f"Unknown bottle type: {e}"}
    if not boxes:
        return {
            "box_size": None, "box_sizes": [], "shipping_cents": None,
            "error": ("Order is empty" if not bottles_by_type
                      else "Order exceeds available flat-rate boxes — "
                           "split shipment or use custom shipping."),
        }
    rates = get_current_rates(db_path=db_path)
    breakdown = []
    total = 0
    for size in boxes:
        if size not in rates:
            return {"box_size": size, "box_sizes": boxes, "shipping_cents": None,
                    "error": f"No confirmed USPS rate for {size} — check /admin/shipping."}
        cents = rates[size]["charged_cents"]
        breakdown.append({"box_size": size, "charged_cents": cents})
        total += cents
    return {
        "box_size": boxes[0],               # back-compat single-box field
        "box_sizes": boxes,
        "shipping_cents": total,
        "box_breakdown": breakdown,
        "rate_effective_date": rates[boxes[0]]["effective_date"],
    }


# ── CRUD: bottle_types ────────────────────────────────────────────────────────

def add_bottle_type(
    name: str,
    diameter_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    with _connect(db_path) as cx:
        new_id = dbwrite.insert_returning_id(
            cx,
            "INSERT INTO bottle_types (name, notes, diameter_mm, height_mm) "
            "VALUES (?, ?, ?, ?)",
            (name, notes, diameter_mm, height_mm),
        )
        cx.commit()
        return int(new_id)


def list_bottle_types(db_path: Optional[str] = None) -> List[dict]:
    with _connect(db_path) as cx:
        rows = cx.execute(
            "SELECT id, name, notes, diameter_mm, height_mm, created_at "
            "FROM bottle_types ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_bottle_type(bottle_type_id: int, db_path: Optional[str] = None) -> None:
    with _connect(db_path) as cx:
        cx.execute("DELETE FROM bottle_types WHERE id = ?", (bottle_type_id,))
        cx.commit()


def update_bottle_type(
    bottle_type_id: int,
    name: str,
    diameter_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    with _connect(db_path) as cx:
        cx.execute(
            "UPDATE bottle_types SET name=?, notes=?, diameter_mm=?, height_mm=? "
            "WHERE id=?",
            (name, notes, diameter_mm, height_mm, bottle_type_id),
        )
        cx.commit()


# ── CRUD: box_capacity ────────────────────────────────────────────────────────

def set_box_capacity(
    bottle_type_id: int,
    box_size: str,
    qty: int,
    db_path: Optional[str] = None,
) -> None:
    """Upsert capacity for one (bottle_type, box_size) cell."""
    if box_size not in BOX_SIZES:
        raise ValueError(f"box_size must be one of {BOX_SIZES}, got {box_size!r}")
    with _connect(db_path) as cx:
        cx.execute("""
            INSERT INTO box_capacity (bottle_type_id, box_size, qty)
            VALUES (?, ?, ?)
            ON CONFLICT (bottle_type_id, box_size) DO UPDATE SET qty = excluded.qty
        """, (bottle_type_id, box_size, qty))
        cx.commit()


def get_capacity_matrix(db_path: Optional[str] = None) -> List[dict]:
    """Returns one row per bottle type with S/M/L columns (None if unset).

    Shape: [{"id": 1, "name": "dropper 1oz", "notes": ..., "S": 6, "M": 12, "L": 24}, ...]
    """
    with _connect(db_path) as cx:
        bottles = cx.execute(
            "SELECT id, name, notes FROM bottle_types ORDER BY name"
        ).fetchall()
        caps = cx.execute(
            "SELECT bottle_type_id, box_size, qty FROM box_capacity"
        ).fetchall()
    by_id: Dict[int, Dict[str, Optional[int]]] = {
        b["id"]: {"S": None, "M": None, "L": None} for b in bottles
    }
    for c in caps:
        by_id[c["bottle_type_id"]][c["box_size"]] = c["qty"]
    return [
        {"id": b["id"], "name": b["name"], "notes": b["notes"], **by_id[b["id"]]}
        for b in bottles
    ]


def get_bottle_dims(db_path: Optional[str] = None) -> Dict[str, tuple]:
    """{name: (diameter_mm, height_mm)} for types that have both dims set."""
    with _connect(db_path) as cx:
        rows = cx.execute(
            "SELECT name, diameter_mm, height_mm FROM bottle_types "
            "WHERE diameter_mm IS NOT NULL AND height_mm IS NOT NULL"
        ).fetchall()
    return {r["name"]: (r["diameter_mm"], r["height_mm"]) for r in rows}


def list_product_bottle_overrides(db_path=None):
    with _connect(db_path) as cx:
        rows = cx.execute("SELECT slug, bottle_type FROM product_bottle_types").fetchall()
    return {r["slug"]: r["bottle_type"] for r in rows}


def set_product_bottle_override(slug, bottle_type, db_path=None):
    with _connect(db_path) as cx:
        cx.execute(
            "INSERT INTO product_bottle_types (slug, bottle_type, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT (slug) DO UPDATE SET bottle_type=excluded.bottle_type, "
            "updated_at=datetime('now')",
            (slug, bottle_type),
        )
        cx.commit()


def clear_product_bottle_override(slug, db_path=None):
    with _connect(db_path) as cx:
        cx.execute("DELETE FROM product_bottle_types WHERE slug=?", (slug,))
        cx.commit()


class UnknownBundleComponent(Exception):
    """A bundle lists a component name that matches no catalog product."""


def bundle_component_products(product, catalog):
    """A bundle's `bundle_component_slugs` (or, as fallback, `bundle_components`
    names) -> the catalog product dicts they name.

    A bundle is ONE catalog line holding several physical bottles, and carries no
    `bottle_type` of its own. Packing it as a single item counts one bottle instead
    of N: the box is undersized and the shipping undercharged. Callers expand a
    bundle through this and pack the components.

    Matching is EXACT and case-insensitive, never fuzzy — on a money path a near-name
    could pack and bill the wrong SKU (ES1 vs ES13). An unresolvable component raises:
    a bundle whose contents cannot be identified cannot be packed correctly by a human
    either, so this must surface, not fall back to the one-bottle undercharge.

    Non-bundle products have no components and return []."""
    p = product or {}
    if not p.get("bundle"):
        return []
    # Prefer explicit slug+qty components (the money-path source of truth): resolve
    # each {slug, qty} by slug and expand to qty copies so the packer counts the right
    # number of bottles. Falls back to name-based bundle_components for bundles authored
    # before slugs existed. Catalog includes inactive records on purpose (a bundle must
    # stay packable after a component is retired), so slug lookup uses the catalog as-is.
    slug_comps = p.get("bundle_component_slugs")
    if slug_comps:
        by_slug = {}
        for item in catalog or []:
            s = item.get("slug")
            if s:
                by_slug.setdefault(s, item)
        out = []
        for comp in slug_comps:
            hit = by_slug.get(comp.get("slug"))
            if hit is None:
                raise UnknownBundleComponent(
                    f"bundle {p.get('slug') or p.get('name')!r} names an unknown "
                    f"component slug: {comp.get('slug')!r}")
            out.extend([hit] * max(1, int(comp.get("qty", 1))))
        return out
    names = p.get("bundle_components") or []
    if not names:
        raise UnknownBundleComponent(
            f"bundle {p.get('slug') or p.get('name')!r} lists no components")
    by_name = {}
    for item in catalog or []:
        key = (item.get("name") or "").strip().lower()
        if key:
            by_name.setdefault(key, item)
    out = []
    for name in names:
        hit = by_name.get((name or "").strip().lower())
        if hit is None:
            raise UnknownBundleComponent(
                f"bundle {p.get('slug') or p.get('name')!r} names an unknown "
                f"component: {name!r}")
        out.append(hit)
    return out


def is_shippable(product) -> bool:
    """True when a catalog product is a physical good the packer should count.

    Services (`service`), info-only listings (`info_only`), digital goods
    (`digital` — ebooks, audiobooks), and vendor-shipped goods (`vendor_shipped`
    — the Kloud PEMF mats ship from Centropix, the NES miHealth from NES) have
    no bottle we pack, so counting them would push the "default" placeholder
    type into quote() and charge for a phantom bottle.

    `vendor_shipped` cannot be expressed as `flat_shipping_cents: 0`: the caller
    tests `_flat > 0`, so a zero falls through to the box packer, and an
    own-box device that quote() cannot fit drops the whole cart to the coarse
    qty rule — undercharging a heavy device at the small-box rate.
    Anything else stays shippable — a physical product with a missing bottle
    mapping must still fail loudly rather than silently ship for free.

    `digital` is its own flag rather than reusing `service`: a digital good is
    genuinely purchasable (unlike `info_only`) and is not a consultation
    (unlike `service`), and the print edition of the same title ships normally,
    so an ebook must not inherit the book's postage."""
    p = product or {}
    return not (p.get("service") or p.get("info_only") or p.get("digital")
                or p.get("vendor_shipped"))


UNKNOWN_BOTTLE_TYPE = "unknown"

# What a CLIENT-facing cart packs an unspecified item as. Glen, 2026-08-20: single
# ingredient Pure Powders go in the bottle used for 120 caps unless the powder is
# expensive, in which case it is a SMALLER bottle -- "so the larger bottle can be
# the fallback for shipping size." That is this row: 250 ml wide-mouth, 72 x 100 mm,
# already described in _STANDARD_BOTTLES as "120 caps / pure powder".
#
# Deliberately NOT the largest type in the table. `own-box` (250 x 450) is oversized
# and routes to its own parcel, which would wildly overcharge a client for a single
# powder. This is the smallest bottle that still covers the whole powder line.
#
# It is a floor, not an answer: an item genuinely bigger than this (a salt tub, a
# litre of water) is still under-rated here, so every substitution is reported in
# the cart's `packaging_review` so an operator can enter the real dimensions.
FALLBACK_BOTTLE_TYPE = "120 caps"


def resolve_bottle_type(slug, product, db_path=None):
    try:
        with _connect(db_path) as cx:
            row = cx.execute(
                "SELECT bottle_type FROM product_bottle_types WHERE slug=?", (slug,)
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # A brand-new or temporarily redirected database may be consulted before
        # the shipping migration has run. Catalog metadata is the documented
        # fallback, so absence of this optional override table must not make a
        # client checkout fail with HTTP 500.
        if "no such table" not in str(exc).lower():
            raise
        row = None
    if row and row["bottle_type"]:
        return row["bottle_type"]
    # Missing packaging data is not a bottle-size guess. Keep it explicit so
    # callers can stop auto-pricing and ask an operator to enter dimensions.
    return (product or {}).get("bottle_type") or UNKNOWN_BOTTLE_TYPE


# Cart-line formats that ship as a cello (cellophane) refill pack instead of a rigid
# bottle. Matches the `format` id used by `_FORMATS` in app.py (id "refill").
CELLO_FORMATS = frozenset({"refill"})


def packing_bottle_type(product, fmt):
    """Packing key for a cart line. Cello/refill lines resolve to the tighter
    cello unit; everything else keeps its product bottle_type."""
    if (fmt or "").strip().lower() in CELLO_FORMATS:
        return CELLO_BOTTLE_TYPE
    return resolve_bottle_type((product or {}).get("slug"), product)


def get_packing_settings(db_path: Optional[str] = None) -> Dict[str, int]:
    with _connect(db_path) as cx:
        rows = cx.execute("SELECT key, value FROM packing_settings").fetchall()
    out = dict(_PACKING_DEFAULTS)
    out.update({r["key"]: r["value"] for r in rows})
    return {k: int(out[k]) for k in _PACKING_KEYS}


def set_packing_setting(key: str, value: int, db_path: Optional[str] = None) -> None:
    if key not in _PACKING_KEYS:
        raise ValueError(f"key must be one of {_PACKING_KEYS}, got {key!r}")
    if int(value) < 0:
        raise ValueError("padding value must be >= 0")
    with _connect(db_path) as cx:
        cx.execute(
            "INSERT INTO packing_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, int(value)),
        )
        cx.commit()


# ── Rate update flow (manual approval) ────────────────────────────────────────

def propose_rate_update(
    box_size: str,
    usps_retail_cents: int,
    source_url: str,
    effective_date: str,
    db_path: Optional[str] = None,
) -> int:
    """Stage a new rate row with confirmed_by='pending'. Glen confirms via UI."""
    if box_size not in BOX_SIZES:
        raise ValueError(f"box_size must be one of {BOX_SIZES}, got {box_size!r}")
    charged = round_to_dollar(usps_retail_cents)
    with _connect(db_path) as cx:
        new_id = dbwrite.insert_returning_id(cx, """
            INSERT INTO usps_rates
                (box_size, usps_retail_cents, charged_cents, effective_date,
                 source_url, confirmed_by, confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
        """, (box_size, usps_retail_cents, charged, effective_date, source_url, PENDING))
        cx.commit()
        return int(new_id)


def list_pending_rate_updates(db_path: Optional[str] = None) -> List[dict]:
    with _connect(db_path) as cx:
        rows = cx.execute("""
            SELECT id, box_size, usps_retail_cents, charged_cents,
                   effective_date, source_url, created_at
            FROM usps_rates
            WHERE confirmed_by = ?
            ORDER BY created_at DESC
        """, (PENDING,)).fetchall()
        return [dict(r) for r in rows]


def confirm_rate_update(
    rate_id: int,
    confirmed_by: str,
    db_path: Optional[str] = None,
) -> None:
    """Mark a pending rate row as live."""
    if confirmed_by == PENDING:
        raise ValueError("confirmed_by cannot be 'pending'")
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as cx:
        cx.execute(
            "UPDATE usps_rates SET confirmed_by = ?, confirmed_at = ? WHERE id = ?",
            (confirmed_by, now, rate_id),
        )
        cx.commit()


def delete_rate(rate_id: int, db_path: Optional[str] = None) -> str:
    """Un-set a rate row (a wrong entry) so the box size reverts to its previous
    confirmed rate. Refuses to delete the last remaining confirmed rate for a size
    (which would leave that size with no price). Returns the deleted row's box_size.
    Raises ValueError if the row is missing or is the only fallback for its size."""
    with _connect(db_path) as cx:
        row = cx.execute("SELECT box_size, confirmed_by FROM usps_rates WHERE id = ?",
                         (rate_id,)).fetchone()
        if not row:
            raise ValueError(f"no rate with id {rate_id}")
        box_size = row["box_size"]
        # A confirmed rate may only be un-set when another confirmed rate exists for
        # the same size to fall back to; a pending row can always be removed.
        if row["confirmed_by"] not in (None, PENDING):
            others = cx.execute(
                "SELECT COUNT(*) FROM usps_rates WHERE box_size = ? AND id != ? "
                "AND confirmed_by IS NOT NULL AND confirmed_by != ?",
                (box_size, rate_id, PENDING)).fetchone()[0]
            if others == 0:
                raise ValueError(
                    f"can't un-set the only confirmed rate for size {box_size} "
                    f"(there would be no fallback price)")
        cx.execute("DELETE FROM usps_rates WHERE id = ?", (rate_id,))
        cx.commit()
        return box_size


# ── USPS rate watcher ────────────────────────────────────────────────────────
# Scrapes a USPS retail-prices page and compares to current confirmed rates.
# Pure parsing is split from network fetch so it's unit-testable. Source URL
# can be overridden via USPS_PRICES_URL env var if the page structure shifts.

import re

# The Priority Mail page lists each Flat Rate package with its Commercial Pricing and
# its Post Office price. business/prices.htm stopped listing the domestic boxes (by
# 2026-11-03 it showed only International ones), so a read there failed or matched an
# International price, and the charge stayed at July's figure.
USPS_PRICES_URL = os.environ.get(
    "USPS_PRICES_URL",
    "https://www.usps.com/ship/priority-mail.htm",
)

# A scraped rate that moves more than this fraction from the current confirmed rate
# is treated as a likely mis-scrape (USPS page layout shift) and flagged for manual
# review rather than auto-proposed. Flat-rate box prices don't jump 40% year to year.
MAX_RATE_JUMP_PCT = 0.40

# Each name is followed by its Commercial Pricing figure on the page, then "Commercial
# Pricing" and the Post Office price. The charge follows the commercial price (Glen's $33
# for a $32.75 Large box). Medium matches its first listing, "Medium Flat Rate Box — 1".
# Up to the price, without crossing into another package's name or price: a package
# whose own price vanished must not read the next card's (review round 2).
_SAME_CARD = r"(?:(?!Flat\s+Rate|\$).){0,40}?"

_SIZE_PATTERNS = [
    # S is our internal Small Box packed inside a USPS Flat Rate Padded Envelope.
    # Price the carrier package actually mailed, not USPS's Small Flat Rate Box.
    ("S", re.compile(r"(?:Padded\s+Flat\s+Rate\s+Envelope|Flat\s+Rate\s+Padded\s+Envelope)" + _SAME_CARD + r"\$(\d+\.\d{2})\s*Commercial\s+Pricing", re.I)),
    ("M", re.compile(r"Medium\s+Flat\s+Rate\s+Box" + _SAME_CARD + r"\$(\d+\.\d{2})\s*Commercial\s+Pricing", re.I)),
    ("L", re.compile(r"Large\s+Flat\s+Rate\s+Box" + _SAME_CARD + r"\$(\d+\.\d{2})\s*Commercial\s+Pricing", re.I)),
]


def _page_text(html: str) -> str:
    """The page's words with tags removed, entities decoded and spaces collapsed, so a
    price split across tags reads as one line."""
    import html as _html
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html or "", flags=re.I | re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def _parse_usps_html(html: str) -> Dict[str, int]:
    """Extract the padded-envelope/Medium/Large Commercial Pricing in cents.

    Raises ValueError if any size is missing — caller should treat that as
    "source page changed, need a manual update via /admin/shipping".
    """
    text = _page_text(html)
    out: Dict[str, int] = {}
    for size, pat in _SIZE_PATTERNS:
        m = pat.search(text)
        if m:
            dollars, cents = m.group(1).split(".")
            out[size] = int(dollars) * 100 + int(cents)
    missing = [s for s in ("S", "M", "L") if s not in out]
    if missing:
        raise ValueError(
            f"USPS price scrape missing sizes: {missing}. "
            f"Source page format may have changed — update USPS_PRICES_URL "
            f"or use the manual-propose form at /admin/shipping."
        )
    return out


def box_size_for_charged_cents(charged_cents: int, db_path: Optional[str] = None):
    """Resolve the single S/M/L tier represented by an order's stored shipping charge.

    Searches confirmed rate history (not only today's rate), because an order may
    have been paid before a rate change and labeled afterward. Returns None when the
    charge is custom, multi-box, free, or ambiguously used by multiple tiers, so label
    buying never guesses a carrier package.
    """
    target = int(charged_cents or 0)
    if target <= 0:
        return None
    with _connect(db_path) as cx:
        rows = cx.execute(
            "SELECT DISTINCT box_size FROM usps_rates "
            "WHERE charged_cents=? AND confirmed_by IS NOT NULL AND confirmed_by != ?",
            (target, PENDING),
        ).fetchall()
    matches = [r["box_size"] for r in rows]
    return matches[0] if len(matches) == 1 else None


def fetch_usps_retail_prices(timeout: int = 30) -> Dict[str, int]:
    """Fetch + parse current USPS Flat Rate retail prices in cents.

    Stdlib-only so it works in the cron container (no extra deps).
    """
    import urllib.request
    req = urllib.request.Request(
        USPS_PRICES_URL,
        # usps.com refuses some non-browser agents, so send a browser one.
        headers={"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return _parse_usps_html(html)


WATCHER = "usps-watcher"
_SIZE_NAMES = {"S": "Padded Flat Rate Envelope (our Small)", "M": "Medium Flat Rate Box",
               "L": "Large Flat Rate Box"}
SHIPPING_ADMIN_HINT = "https://illtowell.com/admin/shipping"

# Who is told. Glen, 2026-11-03: "When their price goes up we should know within a day."
USPS_WATCH_ALERT_TO = os.environ.get("USPS_WATCH_ALERT_TO", "drglenswartwout@gmail.com")
# A read that keeps failing alerts on its first day, then once a week until it reads.
_FAILURE_REALERT_DAYS = 7


def _watch_get(key: str, db_path: Optional[str] = None) -> Optional[str]:
    with _connect(db_path) as cx:
        row = cx.execute("SELECT value FROM usps_watch_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _watch_set(key: str, value: str, db_path: Optional[str] = None) -> None:
    with _connect(db_path) as cx:
        cx.execute("INSERT INTO usps_watch_state (key, value) VALUES (?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
        cx.commit()


def _watch_alert(subject: str, body: str) -> bool:
    """Email Glen. True only when Gmail returned a message id: a skipped send (pytest, a
    suppressed address) is not a sent alert (review round 1). Never raises: an alert
    failure must not undo an applied rate."""
    try:
        from dashboard import inbox as _inbox
        res = _inbox.send_email(USPS_WATCH_ALERT_TO, subject, body) or {}
        if not res.get("id"):
            print(f"[usps-rate-check] alert not sent: {res!r}", flush=True)
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[usps-rate-check] alert not sent: {e!r}", flush=True)
        return False


def _failure_alert_due(today: str, db_path: Optional[str] = None) -> bool:
    last = _watch_get("last_failure_alert", db_path=db_path)
    if not last:
        return True
    try:
        from datetime import date
        gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except ValueError:
        return True
    return gap >= _FAILURE_REALERT_DAYS


def check_usps_rates(
    today: Optional[str] = None,
    db_path: Optional[str] = None,
    notify: bool = True,
) -> dict:
    """Watcher entry point — called daily from /cron/usps-rate-check.

    Compares the USPS Commercial Pricing on the page with the latest confirmed rate
    per box size. A size whose price moved is applied at once (Glen, 2026-11-03: "at
    once"), confirmed as `usps-watcher`, and Glen is emailed what changed. A move of
    more than MAX_RATE_JUMP_PCT is not applied: it is flagged and emailed, because it
    is far more likely a page change than a USPS price. A page that cannot be read is
    emailed on its first day and weekly while it stays unreadable.

    Returns a summary dict for the cron log:
        {checked_at, scraped: {S, M, L}, applied: [...], unchanged: [...],
         flagged: [...], errors: [...], alerted: bool}
    """
    today = today or datetime.now(timezone.utc).date().isoformat()
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scraped": None,
        "applied": [],
        "unchanged": [],
        "flagged": [],
        "errors": [],
        "alerted": False,
    }
    try:
        scraped = fetch_usps_retail_prices()
        summary["scraped"] = scraped
    except Exception as e:
        summary["errors"].append(f"fetch failed: {e}")
        unsent = _watch_get("unsent_alert", db_path=db_path) or ""
        if notify and (unsent or _failure_alert_due(today, db_path=db_path)):
            # An applied change whose email failed rides along (review round 2).
            summary["alerted"] = _watch_alert(
                "USPS price check could not read the USPS page",
                "The daily USPS Flat Rate price check could not read today's prices.\n\n"
                f"Page: {USPS_PRICES_URL}\nError: {e}\n\n"
                "Shipping keeps charging the last prices it read until this is fixed.\n"
                "The platform tab owns the fix: dashboard/shipping.py, _parse_usps_html.\n"
                f"Current prices: {SHIPPING_ADMIN_HINT}"
                + (f"\n\nNot sent earlier:\n{unsent}" if unsent else ""))
            if summary["alerted"]:
                _watch_set("last_failure_alert", today, db_path=db_path)
                _watch_set("unsent_alert", "", db_path=db_path)
        return summary

    _watch_set("last_good_read", today, db_path=db_path)
    _watch_set("last_failure_alert", "", db_path=db_path)
    current = get_current_rates(db_path=db_path)
    lines = []
    for size, retail_cents in scraped.items():
        cur = current.get(size)
        # A hand-entered rate dated ahead of today outranks any row dated today, so
        # applying would repeat daily. Leave it and say so (review round 1).
        if cur and str(cur.get("effective_date") or "") > today:
            summary["flagged"].append({
                "box_size": size, "scraped_cents": retail_cents,
                "current_cents": cur["usps_retail_cents"],
                "reason": f"a rate dated {cur['effective_date']} is set ahead"})
            continue
        if (cur and cur["usps_retail_cents"] == retail_cents
                and int(cur["charged_cents"]) == round_to_dollar(retail_cents)):
            summary["unchanged"].append(size)
            continue
        # Plausibility guard: the USPS page layout can shift so the scraper grabs an
        # adjacent (wrong) price. A confirmed rate that jumps more than MAX_RATE_JUMP_PCT
        # is almost certainly a mis-scrape, so FLAG it instead of charging it (the
        # 2026-07 incident: $12.65 -> $33.75). A first-ever rate (no current) is not gated.
        if cur and cur.get("usps_retail_cents"):
            base = cur["usps_retail_cents"]
            if abs(retail_cents - base) > base * MAX_RATE_JUMP_PCT:
                summary["flagged"].append({
                    "box_size": size, "scraped_cents": retail_cents, "current_cents": base,
                    "reason": (f"it moved more than {int(MAX_RATE_JUMP_PCT*100)}%, which is "
                               "more likely a USPS page change than a price. Review it at "
                               "/admin/shipping"),
                })
                continue
        try:
            rid = propose_rate_update(
                box_size=size,
                usps_retail_cents=retail_cents,
                source_url=USPS_PRICES_URL,
                effective_date=today,
                db_path=db_path,
            )
            confirm_rate_update(rid, confirmed_by=WATCHER, db_path=db_path)
            old_charge = int(cur["charged_cents"]) if cur else None
            new_charge = round_to_dollar(retail_cents)
            summary["applied"].append({"id": rid, "box_size": size, "retail_cents": retail_cents,
                                       "charged_cents": new_charge,
                                       "previous_charged_cents": old_charge})
            lines.append(
                f"{_SIZE_NAMES.get(size, size)}: USPS ${retail_cents/100:.2f}"
                + (f" (was ${cur['usps_retail_cents']/100:.2f})" if cur else "")
                + f". We now charge ${new_charge/100:.0f}"
                + (f" (was ${old_charge/100:.0f})." if old_charge is not None
                   and old_charge != new_charge else ", unchanged."))
        except Exception as e:
            summary["errors"].append(f"{size} apply failed: {e}")
    # A flag or an error is told once, not every day it stays (review round 1).
    told = set(filter(None, (_watch_get("told", db_path=db_path) or "").split("\n")))
    still = set()
    for f in summary["flagged"]:
        key = f"flag:{f['box_size']}:{f['scraped_cents']}:{f['current_cents']}"
        still.add(key)
        if key in told:
            continue
        lines.append(f"{_SIZE_NAMES.get(f['box_size'], f['box_size'])}: the page read "
                     f"${f['scraped_cents']/100:.2f} against ${f['current_cents']/100:.2f}. "
                     f"Not applied: {f['reason']}. Please check it.")
    for err in summary["errors"]:
        key = "error:" + err
        still.add(key)
        if key not in told:
            lines.append(f"Not applied: {err}")
    # A change already applied must still reach Glen if today's email fails: keep the
    # text and send it with the next run (review round 2).
    unsent = _watch_get("unsent_alert", db_path=db_path) or ""
    if notify and (lines or unsent):
        changed = any(a["charged_cents"] != a["previous_charged_cents"] for a in summary["applied"])
        text = "\n".join(lines)
        if unsent:
            earlier = "\n".join(ln for ln in unsent.split("\n") if ln and ln not in lines)
            if earlier:
                text = (text + "\n\n" if text else "") + "Not sent earlier:\n" + earlier
        summary["alerted"] = _watch_alert(
            "USPS shipping price changed" if (changed or unsent) else "USPS shipping price check",
            text + "\n\nSource: " + USPS_PRICES_URL
            + "\nNew orders use the new charge now. Invoices already made keep theirs.\n"
            + f"Current prices: {SHIPPING_ADMIN_HINT}")
        if summary["alerted"]:
            _watch_set("unsent_alert", "", db_path=db_path)
        else:
            # Keep each line once, however many days the email fails (review round 1).
            kept = [ln for ln in (unsent.split("\n") if unsent else []) if ln]
            kept += [ln for ln in lines if ln not in kept]
            _watch_set("unsent_alert", "\n".join(kept), db_path=db_path)
            still = told & still            # not told yet: try again tomorrow
    if notify:   # a run that emails nobody tells nobody (review round 2)
        _watch_set("told", "\n".join(sorted(still)), db_path=db_path)
    return summary