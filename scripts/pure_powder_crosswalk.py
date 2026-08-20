#!/usr/bin/env python3
"""Build a review-gated crosswalk from FMP powders to the sellable catalog.

This script is deliberately read-only.  It distinguishes established Pure Powder
products from raw production ingredients; a raw ingredient is never made sellable
merely because inventory exists.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path


PURE_TYPES = {"pure powder", "pure powders"}
KNOWN_CATALOG_ALIASES = {
    "gingerols 20% (zingiber officinalis)": "gingerol",
    "sumac bran 50:1": "sumac-bran-501-pure-powder",
}

TINY_DOSE_RE = re.compile(
    r"(?:\b5-mthf\b|methylcobalamin|cobalamin|\bbiotin\b|methylselenocysteine|"
    r"\bmk-4\b|\bmk-7\b|cholecalciferol|huperzine|melatonin|pqq\b)", re.I
)
RESTRICTED_RE = re.compile(
    r"(?:dhea|pregnenolone|progesterone|cannabidiol|\bcbd\b|kratom|monkshood|"
    r"methylene blue|edta|sodium chlorite|4-methylumbelliferone|noopept|racetam|"
    r"l-dopa|artemisinin)", re.I
)
NON_ORAL_RE = re.compile(
    r"(?:eye drops?|preservative|bentonite|shungite|diatomaceous|xanthan gum|"
    r"sodium benzoate|tripolyphosphate)", re.I
)


def normalize_name(value: str) -> str:
    value = (value or "").strip().lstrip("*").strip()
    return re.sub(r"\s+", " ", value).casefold()


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text())["products"]


def load_pure_products(db_path: Path) -> list[dict]:
    with sqlite3.connect(db_path) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute(
            "SELECT product_name,type,sold_size,sold_measurement,sold_price,active,id_pk "
            "FROM fmp_products WHERE lower(trim(type)) IN ('pure powder','pure powders') "
            "ORDER BY product_name"
        ).fetchall()
    return [dict(row) for row in rows]


def _number(value: str) -> float:
    try:
        return float((value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def load_inventory_powders(path: Path) -> list[dict]:
    csv.field_size_limit(sys.maxsize)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        row for row in rows
        if normalize_name(row.get("active")) == "yes"
        and "powder" in normalize_name(row.get("deliver_method"))
        and _number(row.get("zc_inventory")) > 0
    ]


def catalog_match(name: str, catalog: dict) -> tuple[str, dict] | tuple[None, None]:
    target = normalize_name(name)
    for slug, item in catalog.items():
        if normalize_name(item.get("name")) == target:
            return slug, item
    alias = KNOWN_CATALOG_ALIASES.get(target)
    if alias and alias in catalog:
        return alias, catalog[alias]
    return None, None


def review_gate(name: str) -> tuple[str, str]:
    if RESTRICTED_RE.search(name):
        return "exclude", "restricted/high-risk raw material"
    if NON_ORAL_RE.search(name):
        return "exclude", "non-oral or production-use material"
    if TINY_DOSE_RE.search(name):
        return "formula_only", "tiny-dose active; route to containing formulations"
    return "manual_review", "needs serving size, packaging, price, and safety approval"


def build_crosswalk(pure_products: list[dict], powders: list[dict], catalog: dict) -> list[dict]:
    result = []
    established_names = {normalize_name(row["product_name"]) for row in pure_products}
    for row in pure_products:
        slug, item = catalog_match(row["product_name"], catalog)
        result.append({
            "source": "established_pure_powder_sku",
            "fmp_id": row.get("id_pk", ""),
            "name": row["product_name"],
            "inventory": "",
            "sold_size": f"{row.get('sold_size') or ''} {row.get('sold_measurement') or ''}".strip(),
            "fmp_price": row.get("sold_price") or "",
            "catalog_slug": slug or "",
            "catalog_name": (item or {}).get("name", ""),
            "catalog_price": (item or {}).get("price_cents", ""),
            "status": "live_catalog_record" if slug else "missing_catalog_record",
            "decision": "publish_or_reconcile",
            "reason": "established active Pure Powder product",
        })

    for row in powders:
        name = (row.get("name_raw") or row.get("zc_name_compound") or "").strip()
        if not name or normalize_name(name) in established_names:
            continue
        slug, item = catalog_match(name, catalog)
        decision, reason = review_gate(name)
        status = "raw_material_review"
        if slug:
            status = "specification_mismatch" if normalize_name(name) != normalize_name(item["name"]) else "live_catalog_record"
            decision = "rename_or_split_catalog_record" if status == "specification_mismatch" else "verify_retail_configuration"
            reason = "catalog record exists; verify the exact material specification"
        result.append({
            "source": "raw_ingredient_inventory",
            "fmp_id": row.get("id_pk", ""),
            "name": name,
            "inventory": row.get("zc_inventory") or "",
            "sold_size": "",
            "fmp_price": "",
            "catalog_slug": slug or "",
            "catalog_name": (item or {}).get("name", ""),
            "catalog_price": (item or {}).get("price_cents", ""),
            "status": status,
            "decision": decision,
            "reason": reason,
        })
    return result


def write_outputs(rows: list[dict], output_csv: Path, output_md: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    established = [r for r in rows if r["source"] == "established_pure_powder_sku"]
    priority = [r for r in rows if r["status"] in {"missing_catalog_record", "specification_mismatch"}]
    decisions = {}
    for row in rows:
        decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
    lines = [
        "# Pure Powder Storefront Crosswalk",
        "",
        f"- Established FMP Pure Powder SKUs: **{len(established)}**",
        f"- Active powdered raw materials with recorded inventory: **{len(rows) - len(established)}**",
        f"- Missing or specification-mismatched catalog records: **{len(priority)}**",
        "",
        "## Decision gates",
        "",
    ]
    lines.extend(f"- {key}: **{value}**" for key, value in sorted(decisions.items()))
    lines += ["", "## Priority reconciliation", "", "| Ingredient | Status | Current catalog record | Action |", "|---|---|---|---|"]
    lines.extend(
        f"| {r['name']} | {r['status']} | {r['catalog_name'] or '—'} | {r['decision']} |"
        for r in priority
    )
    lines += [
        "",
        "## Publishing rule",
        "",
        "Inventory alone never authorizes a retail listing. Every raw material needs an approved serving size, package size, price, labeling name, and safety disposition. Tiny-dose ingredients remain formula-only.",
        "",
        f"Full row-level crosswalk: `{output_csv.name}`",
    ]
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingredients-db", type=Path, required=True)
    parser.add_argument("--ingredients-csv", type=Path, required=True)
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    rows = build_crosswalk(
        load_pure_products(args.ingredients_db),
        load_inventory_powders(args.ingredients_csv),
        load_catalog(args.products),
    )
    write_outputs(rows, args.output_csv, args.output_md)


if __name__ == "__main__":
    main()
