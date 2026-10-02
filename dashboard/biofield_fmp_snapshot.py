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
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
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
# Where a JSON list of (lowercased) product names is stored.
STORED_NAME_LISTS = (("biofield_auth_remedy_set", "remedies_json"),
                     ("biofield_remedy_pattern", "remedies_json"))
_TR_SUFFIX = " in terrain restore"
STAGE_PREFIX = "fmp_stage_"     # each run appends its own token, so two runs never share
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


def _check_stage(cx, stage, min_ratio, allow_removed):
    have = _tables(cx)
    missing = [t for t in PRODUCT_TABLES if stage + t not in have]
    if missing:
        raise RefreshRefused(f"export is missing {missing}")
    for t in PRODUCT_TABLES:
        live = "fmp_snap_" + t
        if live in have:
            lost = [c for c in _cols(cx, live) if c not in set(_cols(cx, stage + t))]
            if lost:
                raise RefreshRefused(f"{t} export lacks columns {lost} that the copy has")
        before = cx.execute(f"SELECT COUNT(*) FROM {live}").fetchone()[0] if live in have else 0
        after = cx.execute(f"SELECT COUNT(*) FROM {stage}{t}").fetchone()[0]
        if before and after < before * min_ratio:
            raise RefreshRefused(f"{t} fell from {before} to {after}; refusing a partial export")
    if "id_pk" in _cols(cx, stage + "products_items"):
        n, distinct = cx.execute(f"SELECT COUNT(*), COUNT(DISTINCT id_pk) "
                                 f"FROM {stage}products_items").fetchone()
        if distinct != n:
            raise RefreshRefused(f"products_items has {n - distinct} duplicate id_pk values")
    if "fmp_snap_products" in have and not allow_removed:
        gone = cx.execute(f"SELECT COUNT(*) FROM fmp_snap_products WHERE id_pk NOT IN "
                          f"(SELECT id_pk FROM {stage}products)").fetchone()[0]
        if gone:
            raise RefreshRefused(f"{gone} products are no longer in FileMaker; "
                                 "rerun with allow_removed once that is confirmed")
    cols = set(_cols(cx, stage + "products"))
    lacking = [c for c in REQUIRED_PRODUCT_COLS if c not in cols]
    if lacking:
        raise RefreshRefused(f"products export lacks columns {lacking}")
    n, blank, distinct = cx.execute(
        f"SELECT COUNT(*), SUM(TRIM(COALESCE(id_pk,''))=''), COUNT(DISTINCT id_pk) "
        f"FROM {stage}products").fetchone()
    if not n:
        raise RefreshRefused("products export is empty")
    if blank:
        raise RefreshRefused(f"{blank} products have no id_pk")
    if distinct != n:
        raise RefreshRefused(f"{n - distinct} duplicate id_pk values")


def product_renames(cx, stage=STAGE_PREFIX):
    """[(id_pk, old_name, new_name)] between the live copy and the staged export.

    A stored row holds only a name, so a rename can be carried only when that name
    pointed at one product. Two cases are skipped, because the stored name still
    resolves to the product it always did: the old name is blank, or another product
    that already carried it still does. Two cases are refused, because moving a row
    would be a guess: a DIFFERENT product has taken the old name, or two products
    that shared the old name were renamed apart."""
    if "fmp_snap_products" not in _tables(cx):
        return []
    old = dict(cx.execute("SELECT id_pk, product_name FROM fmp_snap_products"))
    new = dict(cx.execute(f"SELECT id_pk, product_name FROM {stage}products"))
    holders_before, holders_after = {}, {}
    for pk, v in old.items():
        holders_before.setdefault(_norm(v), set()).add(pk)
    for pk, v in new.items():
        holders_after.setdefault(_norm(v), set()).add(pk)
    out, plans = [], {}
    for pk, old_name in old.items():
        new_name = new.get(pk)
        key = _norm(old_name)
        if new_name is None or not key or key == _norm(new_name) or not _clean(new_name):
            continue
        takers = holders_after.get(key, set()) - holders_before.get(key, set())
        if takers:
            raise RefreshRefused(
                f"'{_clean(old_name)}' was renamed and is now carried by another product "
                f"(id {', '.join(sorted(takers))}); stored rows cannot be moved safely")
        if holders_after.get(key):
            continue                     # a product that always carried it still does
        others = holders_after.get(_norm(new_name), set()) - {pk}
        if others:
            raise RefreshRefused(
                f"'{_clean(old_name)}' was renamed to '{_clean(new_name)}', a name another "
                f"product (id {', '.join(sorted(others))}) also carries; stored rows would merge")
        plans.setdefault(key, set()).add(_norm(new_name))
        out.append((pk, _clean(old_name), _clean(new_name)))
    split = sorted(k for k, v in plans.items() if len(v) > 1)
    if split:
        raise RefreshRefused(f"products sharing the name {split} were renamed apart; "
                             "stored rows cannot be moved safely")
    return sorted(out, key=lambda r: r[1].lower())


def _renamed(by_old, val):
    """The new stored form of `val`, or None when it names no renamed product."""
    key, suffix = _norm(val), ""
    if key not in by_old and key.endswith(_TR_SUFFIX):
        key = key[: -len(_TR_SUFFIX)].strip()
        suffix = " in Terrain Restore"
    new = by_old.get(key)
    if not new:
        return None
    new = new + suffix
    return new.lower() if val == val.lower() else new


def _rewrite_name_lists(cx, by_old, have):
    changed = []
    for table, col in STORED_NAME_LISTS:
        if table not in have or col not in _cols(cx, table):
            continue
        for rid, raw in cx.execute(f'SELECT rowid, "{col}" FROM {table}').fetchall():
            try:
                names = json.loads(raw or "[]")
            except ValueError:
                continue
            if not isinstance(names, list):
                continue
            out = []
            for v in names:
                nv = _renamed(by_old, v) if isinstance(v, str) else None
                nv = nv or v
                if nv not in out:
                    out.append(nv)
            if out != names:
                cx.execute(f'UPDATE {table} SET "{col}"=? WHERE rowid=?', (json.dumps(out), rid))
                changed.append((table, rid, raw, json.dumps(out)))
    return changed


def _rewrite_clinical_catalog(cx, by_old, have):
    """Glen's condition checklist: (item_key, remedy_key) is its key, so both move."""
    table = "biofield_clinical_catalog"
    if table not in have:
        return []
    from dashboard.biofield_clinical_checklist import _norm as checklist_key
    changed = []
    for rid, item, remedy, hidden in cx.execute(
            f"SELECT rowid, item_key, remedy, hidden FROM {table}").fetchall():
        new = _renamed(by_old, remedy)
        if not new:
            continue
        key = checklist_key(new)
        twin = cx.execute(f"SELECT hidden FROM {table} WHERE item_key=? AND remedy_key=? "
                          "AND rowid<>?", (item, key, rid)).fetchone()
        if twin is None:
            cx.execute(f"UPDATE {table} SET remedy=?, remedy_key=? WHERE rowid=?",
                       (new, key, rid))
        elif twin[0] == hidden:
            cx.execute(f"DELETE FROM {table} WHERE rowid=?", (rid,))   # same entry twice
            new = "(duplicate removed)"
        else:
            raise RefreshRefused(f"checklist '{item}' lists both '{remedy}' and '{new}', "
                                 "one hidden and one shown; Glen decides which stays")
        changed.append((table, rid, remedy, new))
    return changed


def _rewrite_stored_names(cx, renames):
    """Move stored remedy names onto the renamed product. Returns the changed rows."""
    by_old = {_norm(o): n for _, o, n in renames}
    have = _tables(cx)
    changed = _rewrite_name_lists(cx, by_old, have) + _rewrite_clinical_catalog(cx, by_old, have)
    for table, col in STORED_NAME_COLUMNS:
        if table not in have or col not in _cols(cx, table):
            continue
        for rid, val in cx.execute(f'SELECT rowid, "{col}" FROM {table}').fetchall():
            # coverage stores names lowercased, and _renamed keeps that form
            new = _renamed(by_old, val or "")
            if not new:
                continue
            try:
                cx.execute(f'UPDATE {table} SET "{col}"=? WHERE rowid=?', (new, rid))
            except sqlite3.IntegrityError:
                # Coverage is (test_id, remedy, code) and nothing else. When that exact
                # row already exists under the new name, the old-name row says the same
                # thing, so it goes. Anything else is not a duplicate and stops the run.
                if table != "biofield_auth_remedy_coverage":
                    raise
                t_id, code = cx.execute(f"SELECT test_id, code FROM {table} WHERE rowid=?",
                                        (rid,)).fetchone()
                if not cx.execute(f"SELECT 1 FROM {table} WHERE test_id=? AND remedy=? "
                                  "AND code=? AND rowid<>?", (t_id, new, code, rid)).fetchone():
                    raise
                cx.execute(f"DELETE FROM {table} WHERE rowid=?", (rid,))
                new = "(duplicate removed)"
            changed.append((table, rid, val, new))
    return changed


def refresh_products(export_dir, db_path, *, min_ratio=0.95, apply=True,
                     allow_removed=False):
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
    stage = f"{STAGE_PREFIX}{os.getpid()}_{secrets.token_hex(3)}_"
    with tempfile.TemporaryDirectory() as only:
        # snapshot_csv_dir loads every CSV in a folder, so hand it the product ones only
        for t in PRODUCT_TABLES:
            shutil.copy(export_dir / f"{t}.csv", Path(only) / f"{t}.csv")
        counts = snapshot_csv_dir(only, db_path, prefix=stage)
    with sqlite3.connect(db_path) as cx:
        try:
            current = (cx.execute("SELECT COUNT(*) FROM fmp_snap_products").fetchone()[0]
                       if "fmp_snap_products" in _tables(cx) else 0)
            _check_stage(cx, stage, min_ratio, allow_removed)
            renames = product_renames(cx, stage)
            old_ids = ({r[0] for r in cx.execute("SELECT id_pk FROM fmp_snap_products")}
                       if current else set())
            added = [_clean(r[1]) for r in cx.execute(
                f"SELECT id_pk, product_name FROM {stage}products") if r[0] not in old_ids]
            new_ids = {r[0] for r in cx.execute(f"SELECT id_pk FROM {stage}products")}
            removed = [_clean(r[1]) for r in cx.execute(
                "SELECT id_pk, product_name FROM fmp_snap_products")
                if r[0] not in new_ids] if current else []
            rewritten = []
            if apply:
                cx.execute("BEGIN")
                have = _tables(cx)
                for t in PRODUCT_TABLES:
                    live, prev = "fmp_snap_" + t, PREV_PREFIX + t
                    cx.execute(f"DROP TABLE IF EXISTS {prev}")
                    if live in have:
                        cx.execute(f"ALTER TABLE {live} RENAME TO {prev}")
                    cx.execute(f"ALTER TABLE {stage}{t} RENAME TO {live}")
                rewritten = _rewrite_stored_names(cx, renames)
                cx.execute("COMMIT")
        except BaseException:
            if cx.in_transaction:
                cx.rollback()            # a half-swapped copy must never be committed
            raise
        finally:
            for t in PRODUCT_TABLES:
                cx.execute(f"DROP TABLE IF EXISTS {stage}{t}")
            cx.commit()
    return {"counts": counts, "previous_products": current, "renamed": renames,
            "added": added, "removed": removed, "rewritten": rewritten, "applied": apply}
