"""Generate customer-facing products.json ingredient panels FROM the DB formulation
recipes (single source of truth). One-way DB -> products.json. Completeness guard:
a recipe with a dosed-but-unnamed line is held for review, never overwrites a panel.
Dry-run default; --write. Supersedes scripts/refresh_ingredients_from_fmp.py."""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys

_PACKAGING_WORDS = re.compile(r'(plantcaps|capsule|pullulan|vegicap|gelcap|bottle)', re.I)
_PACKAGING_UNITS = {"ea.", "ea"}


def _dose_str(dose, unit):
    if dose is None:
        return ""
    # Under 1 mg reads as mcg: 0.4835 mg is "483.5 mcg". The store once showed it
    # as "4835 mg", and 15 other sub-milligram doses lost their "0." the same way.
    if (unit or "").strip().lower() == "mg" and 0 < float(dose) < 1:
        return f"{round(float(dose) * 1000, 3):g} mcg"
    d = int(dose) if float(dose) == int(dose) else dose
    return f"{d} {unit}".strip() if unit else f"{d}"


def build_panel(items):
    out = []
    for it in items:
        name = (it.get("ingredient_canonical") or it.get("ingredient_name") or "").strip()
        unit = (it.get("dose_unit") or "").strip().lower()
        has_dose = it.get("dose") is not None
        if name:
            if _PACKAGING_WORDS.search(name):
                continue                      # packaging component, not a customer ingredient
            out.append({"name": name, "dose": _dose_str(it.get("dose"), it.get("dose_unit"))})
            continue
        # blank name:
        if unit in _PACKAGING_UNITS:
            continue                          # packaging count (e.g. "1 ea." capsule/bottle) — skip
        if has_dose:
            return None, "incomplete: dosed line with no ingredient name"
        # nameless + doseless -> skip
    if not out:
        return None, "no named ingredients"
    return out, None


def build_assignments(formulations_with_items, fmp_to_slug):
    panels, review = {}, []
    for f in formulations_with_items:
        slug = fmp_to_slug.get(str(f["fmp_id"]).strip())
        if not slug:
            review.append({"fmp_id": f["fmp_id"], "name": f["name"], "reason": "no matched product"})
            continue
        panel, reason = build_panel(f["items"])
        if panel is None:
            review.append({"slug": slug, "name": f["name"], "reason": reason})
        else:
            panels[slug] = panel
    return {"panels": panels, "review": review}


def _products_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.environ.get("DATA_DIR")
    if d and os.path.exists(os.path.join(d, "products.json")):
        return os.path.join(d, "products.json")
    return os.path.join(here, "data", "products.json")


def _corrections_path(products_path):
    """The corrections file beside the catalogue being rebuilt, else the repository's."""
    beside = os.path.join(os.path.dirname(products_path), "products-manual-corrections.json")
    if os.path.exists(beside):
        return beside
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "data", "products-manual-corrections.json")


def _manual_slugs(products_path):
    """Slugs whose ingredients Glen pinned in products-manual-corrections.json. A rebuild
    from the database would put the FileMaker raw names back over them (Free & Easy store
    spec, 2026-10-27), so --write leaves them alone. A missing or unreadable file raises:
    no file is not the same as nothing pinned."""
    corr = json.load(open(_corrections_path(products_path)))
    return {k for k, v in corr.items()
            if not k.startswith("_") and isinstance(v, dict) and v.get("ingredients")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--db", default=None)
    ap.add_argument("--source-date", default="2026-06-24")  # stamp; pass run date
    args = ap.parse_args(argv)
    from dashboard.ingredient_catalog import _default_db_path
    from dashboard.formulations import search_formulations, list_items_for_formulation
    path = _products_path()
    with open(path) as f:
        doc = json.load(f)
    products = doc.get("products", {})
    fmp_to_slug = {str(p["fmp_id"]).strip(): slug for slug, p in products.items() if p.get("fmp_id")}
    db = args.db or _default_db_path()
    forms = search_formulations("", limit=100000, db_path=db)
    fwi = [{"fmp_id": f["fmp_id"], "name": f["name"],
            "items": list_items_for_formulation(f["id"], db_path=db)} for f in forms]
    m = build_assignments(fwi, fmp_to_slug)
    print(f"{len(m['panels'])} panels to write; {len(m['review'])} in review")
    for r in m["review"][:40]:
        print(f"  REVIEW {r.get('slug') or r.get('fmp_id')}: {r['name']!r} ({r['reason']})")
    manual = _manual_slugs(path)
    kept = sorted(set(m["panels"]) & manual)
    if kept:
        print(f"  SKIP {len(kept)} with pinned manual ingredients: {', '.join(kept)}")
    if args.write:
        for slug, panel in m["panels"].items():
            if slug in manual:
                continue
            products[slug]["ingredients"] = panel
            products[slug]["ingredients_source"] = f"db-formulations-{args.source_date}"
        with open(path, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        print(f"wrote {len(set(m['panels']) - manual)} panels")
    else:
        print("(dry run — pass --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
