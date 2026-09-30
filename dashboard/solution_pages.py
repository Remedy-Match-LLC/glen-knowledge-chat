"""Solution category pages: /solutions/ and /solutions/<slug>.

Pure, no Flask. One data file, data/solution_categories.json, names each category's
principle, products and learn pages. Product details are read from the catalogue at
render time through the caller's `get_product` (app._get_product), so a retired product
follows its successor exactly as the product page does, and an inactive one drops out.
"""
import json
import re
from pathlib import Path

from dashboard import shop_catalog as _sc
from dashboard.related_products import DO_NOT_RECOMMEND
from dashboard.topic_render import _document, _esc

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "solution_categories.json"

# Glen: never recommend the Living Water prill-bead bottle or its filter refill. Neither
# is in the catalogue today; this stops a re-added listing from entering a category. The
# "(Living Water)" plate ionizers are the primary recommendation, so the brand alone
# must never match.
EXCLUDED_NAME_PARTS = ("prill", "living water bottle", "living water filter")

# The same flags the store leaves out (dashboard.shop_catalog.EXCLUDED_FLAGS).
_INACTIVE_FLAGS = ("inactive", "info_only", "service")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load(path=None):
    with open(path or DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories")
    if not isinstance(cats, list):
        raise ValueError("solution_categories.json has no categories list")
    return cats


def is_excluded(slug, product):
    if slug in DO_NOT_RECOMMEND:
        return True
    name = ((product or {}).get("name") or "").lower()
    return any(part in name for part in EXCLUDED_NAME_PARTS)


def problems(categories, products, table_category_names):
    out, seen = [], set()
    for c in categories:
        slug = c.get("slug") or ""
        if not _SLUG_RE.match(slug):
            out.append(f"Category slug {slug!r} is not a lowercase-hyphen slug.")
        if slug in seen:
            out.append(f"Category slug {slug!r} appears twice.")
        seen.add(slug)
        for p in c.get("products") or []:
            rec = products.get(p)
            if rec is None:
                out.append(f"{slug}: product {p!r} is not in the catalogue.")
            elif any(rec.get(f) for f in _INACTIVE_FLAGS):
                out.append(f"{slug}: product {p!r} is not active.")
            if is_excluded(p, rec):
                out.append(f"{slug}: product {p!r} is on the do-not-recommend list.")
        if not c.get("products") and not c.get("learn"):
            out.append(f"{slug}: has no products and no learn page.")
    for name in table_category_names:
        n = sum(name in (c.get("table_names") or []) for c in categories)
        if n == 0:
            out.append(f"Remedy table name {name!r} is in no category.")
        elif n > 1:
            out.append(f"Remedy table name {name!r} is in {n} categories.")
    return out


def category_view(cat, get_product, learn_name=None):
    cards, seen = [], set()
    for slug in cat.get("products") or []:
        p = get_product(slug)
        if not _sc.listable(p) or p["slug"] in seen or is_excluded(p["slug"], p):
            continue
        seen.add(p["slug"])
        cards.append(_sc.card(p))
    learn = []
    for t in cat.get("learn") or []:
        name = learn_name(t) if learn_name else None
        if name:
            learn.append({"slug": t, "name": name})
    paras = [s.strip() for s in (cat.get("principle") or "").split("\n\n") if s.strip()]
    return {"slug": cat["slug"], "title": cat.get("title") or cat["slug"],
            "principle": paras, "choose_by_use": list(cat.get("choose_by_use") or []),
            "products": cards, "learn": learn}


def is_visible(view):
    return bool(view["products"] or view["learn"])


_HEAD = (
    "<style>"
    ".sol-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));"
    "gap:14px;margin-top:14px;}"
    ".sol-card{display:block;background:var(--surface);border:1px solid var(--border);"
    "border-radius:14px;padding:14px;color:var(--cream);}"
    ".sol-card:hover{border-color:var(--gold);text-decoration:none;}"
    ".sol-card img{width:100%;border-radius:10px;margin-bottom:8px;}"
    ".sol-price{color:var(--gold);font-size:15px;}"
    ".sol-use{margin:0 0 14px 20px;}"
    "</style>"
)


def _price(cents):
    return f"${int(cents or 0) / 100:,.2f}"


def _card_html(c):
    img = f'<img src="{_esc(c["image"])}" alt="" loading="lazy">' if c.get("image") else ""
    return (f'<a class="sol-card" href="{_esc(c["url"])}">{img}'
            f'<div>{_esc(c["name"])}</div>'
            f'<div class="sol-price">{_price(c["price_cents"])}</div></a>')


def render_category_html(view):
    body = [f'<p><a href="/solutions/">All solutions</a></p><h1>{_esc(view["title"])}</h1>']
    body += [f"<p>{_esc(p)}</p>" for p in view["principle"]]
    if view["choose_by_use"]:
        body.append('<h2>Choose by intended use</h2><ul class="sol-use">')
        body += [f"<li>{_esc(u)}</li>" for u in view["choose_by_use"]]
        body.append("</ul>")
    if view["products"]:
        body.append('<div class="sol-grid">' + "".join(_card_html(c) for c in view["products"])
                    + "</div>")
    if view["learn"]:
        body.append('<section class="related"><h2>Learn more</h2><ul>')
        body += [f'<li><a href="/learn/{_esc(t["slug"])}">{_esc(t["name"])}</a></li>'
                 for t in view["learn"]]
        body.append("</ul></section>")
    lead = view["principle"][0] if view["principle"] else view["title"]
    return _document(f'{view["title"]} · Dr. Glen Swartwout', lead, _HEAD, "".join(body))


def render_hub_html(views):
    cards = "".join(
        f'<a class="sol-card" href="/solutions/{_esc(v["slug"])}">'
        f'<div><strong>{_esc(v["title"])}</strong></div>'
        f'<div>{_esc(v["principle"][0] if v["principle"] else "")}</div></a>'
        for v in views)
    body = ("<h1>Solutions</h1><p>Healing tools and resources, grouped by what they do.</p>"
            f'<div class="sol-grid">{cards}</div>')
    return _document("Solutions · Dr. Glen Swartwout",
                     "Healing tools and resources, grouped by what they do.", _HEAD, body)
