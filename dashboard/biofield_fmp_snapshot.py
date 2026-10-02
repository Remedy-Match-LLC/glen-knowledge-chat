"""Load FileMaker CSV exports (/tmp/fmp-export/<source>/*.csv) into the app DB as
snapshot tables `fmp_snap_<table>`.

Why: the FileMaker mirror in Supabase (us-east) is resource-crashed and unreliable,
and the brain-extract memory says not to depend on it at runtime. Instead we extract
the biofield + product tables straight from the local FileMaker file and snapshot
them here, so the Causal Chain Report tool reads a stable local copy.

All columns are TEXT (FileMaker exports are text). Each table is dropped + recreated
so reloads are idempotent. These rows are PII/PHI (client emails, names, chains) —
keep them in the app's runtime DB; never commit the snapshot to git.
"""
import csv
import sqlite3
from pathlib import Path

PREFIX = "fmp_snap_"


def _safe_ident(s):
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in (s or "").strip())
    if not out:
        out = "col"
    if out[0].isdigit():
        out = "_" + out
    return out


def _dedup(cols):
    seen, out = {}, []
    for c in cols:
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
        out.append(c)
    return out


def snapshot_csv_dir(export_dir, db_path, prefix=PREFIX):
    """Load every <table>.csv in export_dir into `<prefix><table>` (TEXT cols,
    full replace). Returns {table: row_count}."""
    export_dir = Path(export_dir)
    counts = {}
    with sqlite3.connect(db_path) as cx:
        for csv_path in sorted(export_dir.glob("*.csv")):
            table = csv_path.stem
            tname = prefix + _safe_ident(table)
            with csv_path.open(encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                cols = _dedup([_safe_ident(h) for h in header])
                cx.execute(f"DROP TABLE IF EXISTS {tname}")
                if not cols:
                    cx.execute(f"CREATE TABLE {tname} (_empty TEXT)")
                    counts[table] = 0
                    continue
                cx.execute(f"CREATE TABLE {tname} ("
                           + ", ".join(f'"{c}" TEXT' for c in cols) + ")")
                ph = ",".join("?" * len(cols))
                n, batch = 0, []
                for row in reader:
                    row = (row + [""] * len(cols))[:len(cols)]
                    batch.append(row)
                    n += 1
                    if len(batch) >= 1000:
                        cx.executemany(f"INSERT INTO {tname} VALUES ({ph})", batch)
                        batch = []
                if batch:
                    cx.executemany(f"INSERT INTO {tname} VALUES ({ph})", batch)
                counts[table] = n
        cx.commit()
    return counts


# ---------------------------------------------------------------------------
# Product refresh: re-read FileMaker's product tables without touching client data.
#
# Glen 2026-10-02: "refresh the product list first", as a refresher that can run again.
# The copy had not changed since 2026-06-23, so Vascular Integrity and every rename since
# were missing from the Clinical Summary picker.
#
# Chain rows store the remedy NAME, and dosing looks a product up by name. A FileMaker
# rename keeps its id_pk, so the refresh maps old name -> new name by id and moves the
# stored rows with it. Only the name changes: dose, frequency, timing and bottles stay.

PRODUCT_TABLES = ("products", "products_items", "products_phases", "products_systems")
REQUIRED_PRODUCT_COLS = ("id_pk", "product_name", "active", "type", "dosage",
                         "dosage_freq", "dosage_timing", "doses_per_bottle")
# Where a product name is stored as text. Each is (table, column).
STORED_NAME_COLUMNS = (("biofield_auth_chain", "remedy"),
                       ("biofield_auth_remedy_coverage", "remedy"))
_TR_SUFFIX = " in terrain restore"
STAGE_PREFIX = "fmp_stage_"
PREV_PREFIX = "fmp_prev_"


class RefreshRefused(Exception):
    """The new export failed a check, so the current copy was left untouched."""


def _clean(name):
    return (name or "").strip().rstrip("*").strip()


def _norm(name):
    return " ".join(_clean(name).split()).lower()


def _tables(cx):
    return {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(cx, table):
    return [r[1] for r in cx.execute(f"PRAGMA table_info({table})")]


def _check_stage(cx, current_count, min_ratio):
    have = _tables(cx)
    missing = [t for t in PRODUCT_TABLES if STAGE_PREFIX + t not in have]
    if missing:
        raise RefreshRefused(f"export is missing {missing}")
    cols = set(_cols(cx, STAGE_PREFIX + "products"))
    lacking = [c for c in REQUIRED_PRODUCT_COLS if c not in cols]
    if lacking:
        raise RefreshRefused(f"products export lacks columns {lacking}")
    n, blank, distinct = cx.execute(
        f"SELECT COUNT(*), SUM(TRIM(COALESCE(id_pk,''))=''), COUNT(DISTINCT id_pk) "
        f"FROM {STAGE_PREFIX}products").fetchone()
    if not n:
        raise RefreshRefused("products export is empty")
    if blank:
        raise RefreshRefused(f"{blank} products have no id_pk")
    if distinct != n:
        raise RefreshRefused(f"{n - distinct} duplicate id_pk values")
    if current_count and n < current_count * min_ratio:
        raise RefreshRefused(
            f"products fell from {current_count} to {n}; refusing a partial export")


def product_renames(cx):
    """[(id_pk, old_name, new_name)] between the live copy and the staged export.

    A rename is skipped when its old name is still carried by some product in the
    export: then the stored name still resolves and moving it would be a guess."""
    if "fmp_snap_products" not in _tables(cx):
        return []
    old = dict(cx.execute("SELECT id_pk, product_name FROM fmp_snap_products"))
    new = dict(cx.execute(f"SELECT id_pk, product_name FROM {STAGE_PREFIX}products"))
    still_named = {_norm(v) for v in new.values()}
    out = []
    for pk, old_name in old.items():
        new_name = new.get(pk)
        if new_name is None or _norm(old_name) == _norm(new_name):
            continue
        if not _clean(new_name) or _norm(old_name) in still_named:
            continue
        out.append((pk, _clean(old_name), _clean(new_name)))
    return sorted(out, key=lambda r: r[1].lower())


def _rewrite_stored_names(cx, renames):
    """Move stored remedy names onto the renamed product. Returns the changed rows."""
    by_old = {_norm(o): n for _, o, n in renames}
    have = _tables(cx)
    changed = []
    for table, col in STORED_NAME_COLUMNS:
        if table not in have or col not in _cols(cx, table):
            continue
        for rid, val in cx.execute(f'SELECT rowid, "{col}" FROM {table}').fetchall():
            key, suffix = _norm(val), ""
            if key not in by_old and key.endswith(_TR_SUFFIX):
                key = key[: -len(_TR_SUFFIX)].strip()
                suffix = " in Terrain Restore"
            new = by_old.get(key)
            if not new:
                continue
            new = new + suffix
            if val == val.lower():
                new = new.lower()        # coverage stores names lowercased; keep its form
            try:
                cx.execute(f'UPDATE {table} SET "{col}"=? WHERE rowid=?', (new, rid))
            except sqlite3.IntegrityError:
                # The same row already exists under the new name (a UNIQUE key), so the
                # old-name row is a duplicate of it.
                cx.execute(f"DELETE FROM {table} WHERE rowid=?", (rid,))
                new = "(duplicate removed)"
            changed.append((table, rid, val, new))
    return changed


def refresh_products(export_dir, db_path, *, min_ratio=0.95, apply=True):
    """Replace the fmp_snap_ product tables from a FileMaker export of PRODUCT_TABLES.

    The export is loaded into staging tables first and checked. Only if it passes are
    the live tables swapped, in one transaction with the stored-name rewrite. The
    previous copy is kept as fmp_prev_<table> so one bad refresh can be undone.
    With apply=False it reports what would change and leaves the live copy alone.
    Client tables (fmp_snap_client*, fmp_snap_clients) are never touched."""
    export_dir = Path(export_dir)
    for t in PRODUCT_TABLES:
        if not (export_dir / f"{t}.csv").exists():
            raise RefreshRefused(f"{t}.csv is not in {export_dir}")
    extra = sorted(p.stem for p in export_dir.glob("*.csv") if p.stem not in PRODUCT_TABLES)
    if extra:
        raise RefreshRefused(f"export holds non-product tables {extra}; refusing")
    counts = snapshot_csv_dir(export_dir, db_path, prefix=STAGE_PREFIX)
    with sqlite3.connect(db_path) as cx:
        try:
            current = (cx.execute("SELECT COUNT(*) FROM fmp_snap_products").fetchone()[0]
                       if "fmp_snap_products" in _tables(cx) else 0)
            _check_stage(cx, current, min_ratio)
            renames = product_renames(cx)
            old_ids = ({r[0] for r in cx.execute("SELECT id_pk FROM fmp_snap_products")}
                       if current else set())
            added = [_clean(r[1]) for r in cx.execute(
                f"SELECT id_pk, product_name FROM {STAGE_PREFIX}products") if r[0] not in old_ids]
            new_ids = {r[0] for r in cx.execute(f"SELECT id_pk FROM {STAGE_PREFIX}products")}
            removed = [_clean(r[1]) for r in cx.execute(
                "SELECT id_pk, product_name FROM fmp_snap_products")
                if r[0] not in new_ids] if current else []
            rewritten = []
            if apply:
                cx.execute("BEGIN")
                have = _tables(cx)
                for t in PRODUCT_TABLES:
                    live, prev, stage = "fmp_snap_" + t, PREV_PREFIX + t, STAGE_PREFIX + t
                    cx.execute(f"DROP TABLE IF EXISTS {prev}")
                    if live in have:
                        cx.execute(f"ALTER TABLE {live} RENAME TO {prev}")
                    cx.execute(f"ALTER TABLE {stage} RENAME TO {live}")
                rewritten = _rewrite_stored_names(cx, renames)
                cx.execute("COMMIT")
        except BaseException:
            if cx.in_transaction:
                cx.rollback()            # a half-swapped copy must never be committed
            raise
        finally:
            for t in PRODUCT_TABLES:
                cx.execute(f"DROP TABLE IF EXISTS {STAGE_PREFIX}{t}")
            cx.commit()
    return {"counts": counts, "previous_products": current, "renamed": renames,
            "added": added, "removed": removed, "rewritten": rewritten, "applied": apply}
