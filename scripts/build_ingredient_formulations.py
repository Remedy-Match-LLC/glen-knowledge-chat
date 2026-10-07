"""Build data/ingredient_formulations.json from the formula data in ingredients.db.

The ingredient page lists "In these formulations". By default it matches the page's
ingredient name exactly against each product's ingredient list, which misses products
that spell the ingredient differently ("Serrapeptase" vs "Serrapeptase 400,000 u/g ...").
For an ingredient listed in GROUPS below, the page lists every product whose formula
contains that FileMaker ingredient id instead, largest amount first.

Run locally whenever the formulas change; commit the output. Read-only on the database.
    python3 scripts/build_ingredient_formulations.py
"""
import json
import os
import sqlite3

DB = os.path.expanduser("~/AI-Training/ingredients.db")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PRODUCTS = os.path.join(ROOT, "data", "products.json")
OUT = os.path.join(ROOT, "data", "ingredient_formulations.json")

# One entry per ingredient Glen has asked to link by formula data.
#   pages:   the ingredient-page slugs that show this list (slugify of each spelling).
#   primary: the product shown first, with its note (Glen's words).
GROUPS = {
    "serrapeptase": {
        "fmp_ingredient_id": 272,
        # The OcuHeal pages ("Serrapeptase (Serratia marcescens)", a 5C potency) are left
        # on the default match: Glen, 2026-10-07, linked the capsule formulas only.
        "pages": ["serrapeptase", "serrapeptase-400-000-u-g-serratiopeptida",
                  "serrapeptase-bacterium-in-gut-of-bombyx-"],
        "primary": "clear-the-way",
        "primary_note": "Serrapeptase in an enteric capsule, so it releases in the small intestine.",
        "ruled": "Glen, 2026-10-07",
    },
}


def build():
    prods = json.load(open(PRODUCTS))["products"]
    by_fmp, retired_fmp = {}, set()
    for slug, p in prods.items():
        if p.get("fmp_id") is None:
            continue
        if p.get("inactive"):
            retired_fmp.add(str(p["fmp_id"]))
        else:
            by_fmp.setdefault(str(p["fmp_id"]), []).append(slug)
    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {"_source": "scripts/build_ingredient_formulations.py from ingredients.db product_ingredients",
           "groups": {}}
    for key, g in GROUPS.items():
        rows = cx.execute(
            "SELECT product_id, product_name, MAX(COALESCE(mg, 0)) FROM product_ingredients "
            "WHERE ingredient_id=? GROUP BY product_id", (g["fmp_ingredient_id"],)).fetchall()
        items, unmatched = [], []
        for pid, pname, mg in rows:
            slugs = by_fmp.get(str(pid), [])
            if not slugs and str(pid) in retired_fmp:
                print(f"  skipped, off sale: {pname} (FileMaker {pid})")
                continue
            if len(slugs) != 1:
                unmatched.append((pid, pname, slugs))
                continue
            items.append({"slug": slugs[0], "mg": mg})
        if unmatched:
            raise SystemExit(f"{key}: formula rows with no single live product: {unmatched}")
        items.sort(key=lambda i: (i["slug"] != g["primary"], -(i["mg"] or 0), i["slug"]))
        if not items or items[0]["slug"] != g["primary"]:
            raise SystemExit(f"{key}: primary {g['primary']} is not in the formula data")
        items[0]["note"] = g["primary_note"]
        out["groups"][key] = {"fmp_ingredient_id": g["fmp_ingredient_id"], "pages": g["pages"],
                              "ruled": g["ruled"], "products": items}
        print(f"{key}: {[(i['slug'], i['mg']) for i in items]}")
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, OUT)   # atomic: a reader never sees a half-written file


if __name__ == "__main__":
    build()
