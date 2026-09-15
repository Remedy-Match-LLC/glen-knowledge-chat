"""The public store: which products it lists, how search matches, and concern groups.

Pure, no Flask. The caller passes `get_product` (app._get_product), so retired products
follow their successor exactly as the product page and checkout do.

Listing is not promoting. Search may show a product on DO_NOT_RECOMMEND, because Glen ruled
Electrolyte Mineral Manna "OK to list". A concern group is a recommendation, so those products
never appear in one.
"""
from dashboard.order_destination import destination_for
from dashboard.related_products import DO_NOT_RECOMMEND

# [D3] What the store leaves out.
EXCLUDED_FLAGS = ("inactive", "info_only", "service", "competitor")


def listable(product):
    return bool(product) and not any(product.get(f) for f in EXCLUDED_FLAGS)


def card(product):
    slug = product["slug"]
    return {"slug": slug, "name": (product.get("name") or slug).strip(),
            "price_cents": int(product.get("price_cents") or 0),
            "image": product.get("image") or "", "url": destination_for(slug)}


def _ingredient_text(product):
    items = product.get("ingredients") or []
    if not isinstance(items, list):
        return str(items)
    return " ".join(str(i.get("name") if isinstance(i, dict) else i) for i in items)


def search(slugs, get_product, q="", limit=60):
    terms = [t for t in (q or "").lower().split() if t]
    seen, out = set(), []
    for slug in slugs:
        p = get_product(slug)
        if not listable(p) or p["slug"] in seen:
            continue
        hay = " ".join((p.get("name") or "", p.get("description") or "",
                        _ingredient_text(p))).lower()
        if terms and not all(t in hay for t in terms):
            continue
        seen.add(p["slug"])
        out.append(card(p))
    out.sort(key=lambda c: c["name"].lower())
    return out[:int(limit)]


def concern_groups(programs, get_product):
    groups = []
    for prog in programs or []:
        slugs = []
        for item in prog.get("items") or []:
            p = get_product(item.get("slug") if isinstance(item, dict) else item)
            if listable(p) and p["slug"] not in DO_NOT_RECOMMEND and p["slug"] not in slugs:
                slugs.append(p["slug"])
        if slugs:
            groups.append({"key": prog["condition_key"], "label": prog["label"], "slugs": slugs})
    return groups
