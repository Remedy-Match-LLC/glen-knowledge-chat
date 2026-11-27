"""Purchase orders (history) — FMP-migrated. PO header + line items + receiving.
Line items reference Phase-1 ingredients / Phase-3a materials / Phase-2 products.
Mirrors dashboard/ingredient_catalog.py."""
from __future__ import annotations
import json
import sqlite3
from datetime import date
from dashboard.ingredient_catalog import _connect
from dashboard import dbwrite


def init_purchase_orders_schema(cx: sqlite3.Connection) -> None:
    from dashboard import db
    pg = db.backend_of(cx) == "postgres"
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS purchase_orders (
              id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              fmp_id TEXT, supplier_id INTEGER REFERENCES suppliers(id),
              supplier_name TEXT, vendor_po_no TEXT, po_date TEXT, status TEXT,
              tax DOUBLE PRECISION, shipping_amount DOUBLE PRECISION, shipper TEXT, tracking_number TEXT,
              due_date TEXT, posted_date TEXT, qb_id TEXT,
              extras TEXT, notes TEXT,
              created_at TEXT DEFAULT (now()::text), updated_at TEXT DEFAULT (now()::text)
            )""")
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS purchase_orders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fmp_id TEXT, supplier_id INTEGER REFERENCES suppliers(id),
              supplier_name TEXT, vendor_po_no TEXT, po_date TEXT, status TEXT,
              tax DOUBLE PRECISION, shipping_amount DOUBLE PRECISION, shipper TEXT, tracking_number TEXT,
              due_date TEXT, posted_date TEXT, qb_id TEXT,
              extras TEXT, notes TEXT,
              created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
            )""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_po_fmp ON purchase_orders(fmp_id) WHERE fmp_id IS NOT NULL")
    _add_po_number_column(cx)
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_po_number ON purchase_orders(po_number) "
               "WHERE po_number IS NOT NULL")
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS po_items (
              id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              fmp_id TEXT, po_id INTEGER REFERENCES purchase_orders(id),
              item_kind TEXT, item_label TEXT,
              ingredient_id INTEGER REFERENCES ingredients(id),
              material_id INTEGER REFERENCES materials(id),
              fmp_product_id TEXT, sku TEXT,
              qty DOUBLE PRECISION, qty_unit TEXT, qty_left DOUBLE PRECISION, cost DOUBLE PRECISION,
              extras TEXT, notes TEXT,
              created_at TEXT DEFAULT (now()::text), updated_at TEXT DEFAULT (now()::text)
            )""")
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS po_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fmp_id TEXT, po_id INTEGER REFERENCES purchase_orders(id),
              item_kind TEXT, item_label TEXT,
              ingredient_id INTEGER REFERENCES ingredients(id),
              material_id INTEGER REFERENCES materials(id),
              fmp_product_id TEXT, sku TEXT,
              qty DOUBLE PRECISION, qty_unit TEXT, qty_left DOUBLE PRECISION, cost DOUBLE PRECISION,
              extras TEXT, notes TEXT,
              created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
            )""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_poitems_fmp ON po_items(fmp_id) WHERE fmp_id IS NOT NULL")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_poitems_po ON po_items(po_id)")
    if pg:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS po_receiving (
              id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              fmp_id TEXT, po_id INTEGER REFERENCES purchase_orders(id),
              po_item_id INTEGER REFERENCES po_items(id),
              qty_received DOUBLE PRECISION, received_size TEXT, extras TEXT,
              created_at TEXT DEFAULT (now()::text), updated_at TEXT DEFAULT (now()::text)
            )""")
    else:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS po_receiving (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fmp_id TEXT, po_id INTEGER REFERENCES purchase_orders(id),
              po_item_id INTEGER REFERENCES po_items(id),
              qty_received DOUBLE PRECISION, received_size TEXT, extras TEXT,
              created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
            )""")
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_porec_fmp ON po_receiving(fmp_id) WHERE fmp_id IS NOT NULL")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_porec_po ON po_receiving(po_id)")
    cx.commit()


def _add_po_number_column(cx) -> None:
    """Add purchase_orders.po_number if missing, on SQLite or Postgres.
    Same race-safe shape as biofield_requests.add_column: commit the CREATEs first so
    a rollback cannot undo them, and a loser of a concurrent ALTER re-checks and carries on."""
    from dashboard import db as _db
    if _db.column_exists(cx, "purchase_orders", "po_number"):
        return
    cx.commit()
    try:
        cx.execute("ALTER TABLE purchase_orders ADD COLUMN po_number INTEGER")
        cx.commit()
    except Exception:
        cx.rollback()
        if not _db.column_exists(cx, "purchase_orders", "po_number"):
            raise


# Highest FileMaker PO number, read live 2026-11-26 (Remedy Match.fmp12, table po,
# 129 POs, highest id_pk 1119). New POs continue from here: Glen, "yes, continue the
# FileMaker numbers".
FMP_LAST_PO_NUMBER = 1119


def _numeric(v):
    t = str(v if v is not None else "").strip()
    return int(t) if t.isdigit() else None


def _next_po_number(cx) -> int:
    top = FMP_LAST_PO_NUMBER
    for (n,) in cx.execute("SELECT MAX(po_number) FROM purchase_orders").fetchall():
        if n is not None:
            top = max(top, int(n))
    for (f,) in cx.execute("SELECT fmp_id FROM purchase_orders WHERE fmp_id IS NOT NULL").fetchall():
        n = _numeric(f)
        if n is not None:
            top = max(top, n)
    return top + 1


def assign_po_number(cx, po_id) -> int:
    """The PO's printed number, assigned on first use and never changed after.
    An imported FileMaker PO keeps its FileMaker number. Any other PO gets one past the
    highest number in use (floor FMP_LAST_PO_NUMBER). Called only when a PDF is made,
    so an abandoned draft burns no number. Two concurrent callers cannot share a
    number: the unique index refuses the loser, who recomputes and tries again."""
    from dashboard import db as _db

    def current():
        r = cx.execute("SELECT po_number, fmp_id FROM purchase_orders WHERE id=?",
                       (po_id,)).fetchone()
        if r is None:
            raise LookupError(f"purchase order {po_id} not found")
        return r[0], r[1]

    num, fmp = current()
    if num is not None:
        return int(num)
    imported = _numeric(fmp)
    for _ in range(8):
        n = imported if imported is not None else _next_po_number(cx)
        try:
            cx.execute("UPDATE purchase_orders SET po_number=? WHERE id=? AND po_number IS NULL",
                       (n, po_id))
            cx.commit()
        except _db.IntegrityError:
            cx.rollback()
            if imported is not None:
                raise RuntimeError(f"FileMaker PO number {n} is already used by another PO")
            continue
        num, _f = current()
        if num is not None:
            return int(num)
    raise RuntimeError("could not assign a PO number after 8 tries")


def _clean_lines(lines):
    """Validate hand-entered PO lines. Raises ValueError with a plain message."""
    if not lines:
        raise ValueError("add at least one line")
    out = []
    for i, ln in enumerate(lines, 1):
        ln = ln or {}
        label = str(ln.get("label") or "").strip()
        if not label:
            raise ValueError(f"line {i}: description is required")
        try:
            qty = float(ln.get("qty"))
        except (TypeError, ValueError):
            raise ValueError(f"line {i}: quantity must be a number")
        if not qty > 0:
            raise ValueError(f"line {i}: quantity must be more than 0")
        c = ln.get("cost")
        if c in (None, ""):
            cost = None
        else:
            try:
                cost = float(c)
            except (TypeError, ValueError):
                raise ValueError(f"line {i}: unit cost must be a number")
            if cost < 0:
                raise ValueError(f"line {i}: unit cost cannot be negative")
        mat = ln.get("material_id")
        ing = ln.get("ingredient_id")
        mat = int(mat) if mat not in (None, "") else None
        ing = int(ing) if ing not in (None, "") else None
        kind = "material" if mat is not None else ("ingredient" if ing is not None else "other")
        unit = str(ln.get("qty_unit") or "").strip() or None
        out.append((kind, label, ing, mat, qty, unit, cost))
    return out


def _insert_lines(cx, po_id, clean):
    for kind, label, ing, mat, qty, unit, cost in clean:
        cx.execute(
            "INSERT INTO po_items (po_id, item_kind, item_label, ingredient_id, material_id, "
            "qty, qty_unit, cost) VALUES (?,?,?,?,?,?,?,?)",
            (po_id, kind, label, ing, mat, qty, unit, cost))


def create_po(cx, supplier_id, lines, notes=None, po_date=None):
    """Create a draft PO by hand. Lines: [{label, qty, qty_unit, cost?, material_id?,
    ingredient_id?}], cost being the unit cost. Returns the new PO id."""
    try:
        sid = int(supplier_id)
    except (TypeError, ValueError):
        raise ValueError("pick a supplier")
    sup = cx.execute("SELECT company FROM suppliers WHERE id=?", (sid,)).fetchone()
    if sup is None:
        raise ValueError("supplier not found")
    clean = _clean_lines(lines)
    po_date = (str(po_date).strip() if po_date else "") or date.today().isoformat()
    po_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO purchase_orders (supplier_id, supplier_name, po_date, status, notes) "
        "VALUES (?,?,?,'draft',?)",
        (sid, sup[0] or "", po_date, (notes or None)))
    _insert_lines(cx, po_id, clean)
    cx.commit()
    return po_id


def replace_draft_lines(cx, po_id, lines):
    """Replace every line on a draft PO. Refuses any PO that is not a draft."""
    r = cx.execute("SELECT status FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if r is None:
        raise LookupError(f"purchase order {po_id} not found")
    if (r[0] or "") != "draft":
        raise ValueError("only a draft purchase order can have its lines changed")
    if cx.execute("SELECT 1 FROM po_receiving WHERE po_id=? LIMIT 1", (po_id,)).fetchone():
        raise ValueError("this purchase order already has receiving records")
    clean = _clean_lines(lines)
    cx.execute("DELETE FROM po_items WHERE po_id=?", (po_id,))
    _insert_lines(cx, po_id, clean)
    cx.commit()
    return len(clean)


def search_purchase_orders(q="", limit=50, offset=0, db_path=None):
    with _connect(db_path) as cx:
        rows = cx.execute("""
            SELECT po.*, sup.company AS supplier_company FROM purchase_orders po
            LEFT JOIN suppliers sup ON sup.id = po.supplier_id
            WHERE po.vendor_po_no LIKE ? OR sup.company LIKE ?
               OR CAST(po.po_number AS TEXT) LIKE ?
            ORDER BY po.po_date DESC, po.id DESC LIMIT ? OFFSET ?
        """, (f"%{q}%", f"%{q}%", f"%{q}%", int(limit), int(offset))).fetchall()
    return [dict(r) for r in rows]


def get_purchase_order(po_id, db_path=None):
    with _connect(db_path) as cx:
        r = cx.execute("""
            SELECT po.*, sup.company AS supplier_company FROM purchase_orders po
            LEFT JOIN suppliers sup ON sup.id = po.supplier_id WHERE po.id=?
        """, (po_id,)).fetchone()
    return dict(r) if r else None


def list_po_items(po_id, db_path=None):
    with _connect(db_path) as cx:
        rows = cx.execute("""
            SELECT pi.*, ing.name AS ingredient_canonical, mat.name AS material_name
            FROM po_items pi
            LEFT JOIN ingredients ing ON ing.id = pi.ingredient_id
            LEFT JOIN materials mat ON mat.id = pi.material_id
            WHERE pi.po_id = ? ORDER BY pi.id
        """, (po_id,)).fetchall()
    return [dict(r) for r in rows]


def list_po_receiving(po_id, db_path=None):
    with _connect(db_path) as cx:
        rows = cx.execute("SELECT * FROM po_receiving WHERE po_id=? ORDER BY id", (po_id,)).fetchall()
    return [dict(r) for r in rows]


def create_draft_po(cx, supplier_id, supplier_name, lines):
    """Create a draft purchase order + its line items from reorder-report lines.
    `cx` is an open sqlite3 connection. Lines missing ingredient_id or suggested_qty are
    skipped; price_per_unit may be None (cost stored NULL). Returns {po_id, line_count}."""
    today = date.today().isoformat()
    vendor_po_no = "DRAFT-" + today.replace("-", "") + "-" + str(supplier_id)
    po_id = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO purchase_orders (supplier_id, supplier_name, vendor_po_no, po_date, status) "
        "VALUES (?,?,?,?,'draft')",
        (supplier_id, supplier_name or "", vendor_po_no, today))
    n = 0
    for ln in (lines or []):
        ing_id = ln.get("ingredient_id")
        qty = ln.get("suggested_qty")
        if ing_id is None or qty is None:
            continue
        c = ln.get("price_per_unit")
        cost = float(c) if c not in (None, "") else None
        extras = json.dumps({k: ln.get(k) for k in ("unit_size", "packs", "est_cost")})
        cx.execute(
            "INSERT INTO po_items (po_id, item_kind, item_label, ingredient_id, qty, qty_unit, cost, extras) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (po_id, "ingredient", ln.get("ingredient") or "", int(ing_id),
             float(qty), ln.get("unit"), cost, extras))
        n += 1
    cx.commit()
    return {"po_id": po_id, "line_count": n}


_PO_CURATED = {"notes"}
_ITEM_CURATED = {"notes"}


def _update_allowed(table, row_id, fields, allowed, db_path):
    cols = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=datetime('now')"
    with _connect(db_path) as cx:
        cx.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*cols.values(), row_id))
        cx.commit()


def update_po_curated(po_id, fields, db_path=None):
    _update_allowed("purchase_orders", po_id, fields, _PO_CURATED, db_path)


def update_po_item_curated(item_id, fields, db_path=None):
    _update_allowed("po_items", item_id, fields, _ITEM_CURATED, db_path)
