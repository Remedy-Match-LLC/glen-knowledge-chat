# Solution Category Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/solutions/`, a hub of ten solution categories, and `/solutions/<slug>`, one page per category, on illtowell.com.

**Architecture:** One data file, `data/solution_categories.json`, names each category's principle, products and learn pages. A pure module, `dashboard/solution_pages.py`, loads and checks that file, joins it to the catalogue at render time, and renders server-side HTML in the `/learn/` pages' style. Two thin Flask routes in `app.py` call it.

**Tech Stack:** Python 3, Flask, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-30-solution-category-pages-design.md`

## Global Constraints

- One data file is the single source. Product details are never copied into it. They are read from `data/products.json` when the page renders.
- Each product card shows name, image and price from the catalogue, and links to `/begin/product/<slug>`.
- `/solutions/<unknown>` returns 404, not a placeholder page.
- Every product slug exists in the catalogue and is active.
- Every category has at least one product, or names at least one `/learn/` page.
- No do-not-recommend product appears. The Living Water prill-bead bottle and its filter refill are excluded. The Living Water plate ionizers are included.
- Every remedy table name that is a category appears in exactly one category's `table_names`.
- Copy describes what a tool supports, never what it treats. No em dashes. No ALL CAPS.
- Copy gets a copy round, three review rounds, and Glen's approval before publishing.
- Never `git add -A`. Stage named files.
- A PR opened in this work is merged by Glen in the platform tab.

## Review Focus

1. A category whose products are all inactive, with no approved learn page (Fasting today), returns 404 and is absent from the hub. It never renders an empty page.
2. A learn slug whose topic page is still pending is not linked. `/learn/fasting` answers 200 today with a noindex "preparing" page.
3. A product name or title holding `&`, quotes or `”` (for example `EMF Shielding Pouch 7” x 4” Cell Phone`) is escaped, never raw HTML.
4. Two listed slugs that resolve to the same surviving product (one superseded by the other) show one card, not two.
5. A missing or unreadable data file at runtime gives a 404 on both routes and a log line, not a 500.

Each of these has a test in the task that owns the code: 1 and 2 in Task 3, 3 and 4 in Task 2, 5 in Task 3.

## Assumptions to confirm with Glen before Task 4

These shape the data, not the code, so Tasks 1 to 3 can proceed first.

- Fasting has no product and no approved learn page. Under the rules above it stays hidden until one exists.
- Most catalogue products have no `image` field (3 of 1,092 today). Cards without one show name and price only, as the store does.
- A product may sit in two categories, as the old store did (Harmony Laser in Light and in Frequency).

---

### Task 1: Load and check the category data

**Files:**
- Create: `dashboard/solution_pages.py`
- Test: `tests/test_solution_pages.py`

**Interfaces:**
- Consumes: `dashboard.related_products.DO_NOT_RECOMMEND` (frozenset of slugs).
- Produces:
  - `DATA_PATH: pathlib.Path`
  - `load(path=None) -> list[dict]`: the `categories` list. Raises `OSError` or `ValueError` on a missing or malformed file.
  - `is_excluded(slug: str, product: dict | None) -> bool`
  - `problems(categories: list[dict], products: dict, table_category_names: list[str]) -> list[str]`: one plain sentence per broken rule, empty when all hold.

- [ ] **Step 1: Write the failing tests**

```python
"""Solution category pages: data loading, rule checks, view and HTML."""
import json

import pytest

from dashboard import solution_pages as sp

PRODUCTS = {
    "kloud-pemf-mini": {"name": "Kloud PEMF Mat (Mini)", "price_cents": 99900},
    "old-mat": {"name": "Old Mat", "inactive": True},
    "info-page": {"name": "Info", "info_only": True},
    "fungifuge": {"name": "Fungifuge"},
    "prill-bottle": {"name": "Living Water Bottle (prill beads)"},
    "water-ionizer-5plate": {"name": "5-Plate Water Ionizer (Living Water)"},
}


def cat(**kw):
    base = {"slug": "pemf", "title": "PEMF", "principle": "p", "choose_by_use": [],
            "products": ["kloud-pemf-mini"], "learn": [], "table_names": []}
    base.update(kw)
    return base


def test_load_reads_the_categories_list(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"categories": [cat()]}))
    assert [c["slug"] for c in sp.load(f)] == ["pemf"]


def test_load_raises_on_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        sp.load(tmp_path / "nope.json")


def test_excluded_covers_do_not_recommend_and_the_prill_bottle():
    assert sp.is_excluded("fungifuge", PRODUCTS["fungifuge"])
    assert sp.is_excluded("prill-bottle", PRODUCTS["prill-bottle"])
    # The plate ionizers are the primary recommendation. The shared brand must not hide them.
    assert not sp.is_excluded("water-ionizer-5plate", PRODUCTS["water-ionizer-5plate"])


def test_problems_is_empty_for_good_data():
    assert sp.problems([cat(table_names=["PEMF"])], PRODUCTS, ["PEMF"]) == []


@pytest.mark.parametrize("bad, fragment", [
    (cat(products=["nope"]), "not in the catalogue"),
    (cat(products=["old-mat"]), "not active"),
    (cat(products=["info-page"]), "not active"),
    (cat(products=["fungifuge"]), "do-not-recommend"),
    (cat(products=[], learn=[]), "no products and no learn page"),
    (cat(slug="Bad Slug"), "slug"),
])
def test_problems_names_each_broken_rule(bad, fragment):
    out = sp.problems([bad], PRODUCTS, [])
    assert any(fragment in p for p in out), out


def test_problems_requires_each_table_name_exactly_once():
    two = [cat(table_names=["172 Hz"]), cat(slug="other", table_names=["172 Hz"])]
    assert any("172 Hz" in p and "2 categories" in p for p in sp.problems(two, PRODUCTS, ["172 Hz"]))
    assert any("172 Hz" in p and "no category" in p for p in sp.problems([cat()], PRODUCTS, ["172 Hz"]))


def test_problems_flags_a_duplicate_category_slug():
    assert any("twice" in p for p in sp.problems([cat(), cat()], PRODUCTS, []))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `bash ci/run-tests.sh tests/test_solution_pages.py -q`
Expected: FAIL with `ImportError: cannot import name 'solution_pages'`.

If `ci/run-tests.sh` does not pass arguments through, run `OPENAI_API_KEY=sk-fake PINECONE_API_KEY=pcsk_fake python3 -m pytest tests/test_solution_pages.py -q` instead, and use that form for every run in this plan.

- [ ] **Step 3: Write the minimal implementation**

```python
"""Solution category pages: /solutions/ and /solutions/<slug>.

Pure, no Flask. One data file, data/solution_categories.json, names each category's
principle, products and learn pages. Product details are read from the catalogue at
render time through the caller's `get_product` (app._get_product), so a retired product
follows its successor exactly as the product page does, and an inactive one drops out.
"""
import json
import re
from pathlib import Path

from dashboard.related_products import DO_NOT_RECOMMEND

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash ci/run-tests.sh tests/test_solution_pages.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add dashboard/solution_pages.py tests/test_solution_pages.py
git commit -m "feat(solutions): load and check the solution category data"
```

---

### Task 2: Build the page view and render the HTML

**Files:**
- Modify: `dashboard/solution_pages.py` (append)
- Test: `tests/test_solution_pages.py` (append)

**Interfaces:**
- Consumes: Task 1's `is_excluded`. `dashboard.shop_catalog.listable(product) -> bool` and `dashboard.shop_catalog.card(product) -> {"slug","name","price_cents","image","url"}`. `dashboard.topic_render._document(title, meta_desc, head_extra, body_inner, *, noindex=False) -> str` and `dashboard.topic_render._esc(s) -> str`.
- Produces:
  - `category_view(cat: dict, get_product, learn_name=None) -> dict` with keys `slug`, `title`, `principle` (list of paragraph strings), `choose_by_use` (list of str), `products` (list of card dicts), `learn` (list of `{"slug","name"}`).
    - `get_product(slug) -> dict | None`: returns the live product with its `"slug"` set, following `superseded_by`. This is `app._get_product`.
    - `learn_name(slug) -> str | None`: the approved topic's display name, or None when it is not public.
  - `is_visible(view: dict) -> bool`: True when the view has a product or a learn link.
  - `render_category_html(view: dict) -> str`
  - `render_hub_html(views: list[dict]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def _get(products):
    def get_product(slug):
        p = products.get(slug)
        if p and p.get("superseded_by"):
            return get_product(p["superseded_by"])
        return dict(p, slug=slug) if p else None
    return get_product


VIEW_PRODUCTS = {
    "pouch": {"name": "EMF Shielding Pouch 7” x 4” Cell Phone", "price_cents": 3500},
    "old-pouch": {"name": "Old Pouch", "inactive": True, "superseded_by": "pouch"},
    "gone": {"name": "Gone", "inactive": True},
    "fungifuge": {"name": "Fungifuge", "price_cents": 4000},
}


def test_view_drops_inactive_and_excluded_and_dedupes_a_superseded_twin():
    c = cat(products=["pouch", "old-pouch", "gone", "fungifuge"], learn=["emf-sensitivity"],
            principle="First.\n\nSecond.")
    v = sp.category_view(c, _get(VIEW_PRODUCTS), learn_name=lambda s: "EMF Sensitivity")
    assert [p["slug"] for p in v["products"]] == ["pouch"]
    assert v["products"][0]["url"] == "/begin/product/pouch"
    assert v["principle"] == ["First.", "Second."]
    assert v["learn"] == [{"slug": "emf-sensitivity", "name": "EMF Sensitivity"}]


def test_view_drops_a_learn_page_that_is_not_public():
    v = sp.category_view(cat(learn=["fasting"]), _get(VIEW_PRODUCTS), learn_name=lambda s: None)
    assert v["learn"] == []


def test_a_view_with_nothing_to_show_is_not_visible():
    v = sp.category_view(cat(products=["gone"], learn=["fasting"]), _get(VIEW_PRODUCTS),
                         learn_name=lambda s: None)
    assert not sp.is_visible(v)


def test_category_html_escapes_names_and_links_each_card():
    c = cat(title="Water & Hydrogen", products=["pouch"], choose_by_use=["Travel <light>."])
    html = sp.render_category_html(sp.category_view(c, _get(VIEW_PRODUCTS)))
    assert "Water &amp; Hydrogen" in html
    assert "Travel &lt;light&gt;." in html
    assert 'href="/begin/product/pouch"' in html
    assert "7” x 4”" in html and "$35.00" in html
    assert "<script" not in html


def test_hub_html_links_every_visible_category():
    views = [sp.category_view(cat(slug="pemf", title="PEMF", principle="Pulse."), _get(PRODUCTS))]
    html = sp.render_hub_html(views)
    assert 'href="/solutions/pemf"' in html and "Pulse." in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `bash ci/run-tests.sh tests/test_solution_pages.py -q`
Expected: FAIL with `AttributeError: module 'dashboard.solution_pages' has no attribute 'category_view'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `dashboard/solution_pages.py`, and add the two imports at the top beside the existing one.

```python
from dashboard import shop_catalog as _sc
from dashboard.topic_render import _document, _esc
```

```python
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
        body.append("<h2>Choose by intended use</h2><ul class=\"sol-use\">")
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
```

The hub card shows the first principle paragraph. Task 4 keeps that paragraph to one or two sentences so it reads as the spec's "one-line principle".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash ci/run-tests.sh tests/test_solution_pages.py -q`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add dashboard/solution_pages.py tests/test_solution_pages.py
git commit -m "feat(solutions): build the category view and render both pages"
```

---

### Task 3: Serve the two routes

**Files:**
- Modify: `app.py`. Insert the new routes directly above `@app.route("/learn/patterns")` (about line 9876).
- Test: `tests/test_solution_routes.py`

**Interfaces:**
- Consumes: Task 1's `load`, Task 2's `category_view`, `is_visible`, `render_category_html` and `render_hub_html`. `app._get_product(slug)`. `dashboard.topic_pages.get_page(cx, slug) -> dict | None` and `dashboard.topic_render.is_public(page) -> bool`.
- Produces: `GET /solutions/` (a bare `/solutions` redirects to it) and `GET /solutions/<slug>`. Both return HTML with status 200, or 404.

- [ ] **Step 1: Write the failing tests**

```python
"""Solution category routes: /solutions/ and /solutions/<slug>."""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import json

import pytest

import app
from dashboard import solution_pages as sp

CAT = {
    "kloud-pemf-mini": {"name": "Kloud PEMF Mat (Mini)", "price_cents": 99900},
    "old-mat": {"name": "Old Mat", "inactive": True},
}
CATS = [
    {"slug": "pemf", "title": "PEMF", "principle": "Pulsed fields.", "choose_by_use": [],
     "products": ["kloud-pemf-mini", "old-mat"], "learn": ["cellular-energy"], "table_names": []},
    {"slug": "fasting", "title": "Fasting", "principle": "Rest.", "choose_by_use": [],
     "products": [], "learn": ["fasting"], "table_names": ["Fasting resources"]},
]
PUBLIC = {"cellular-energy": "Cellular Energy"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    f = tmp_path / "solution_categories.json"
    f.write_text(json.dumps({"categories": CATS}))
    monkeypatch.setattr(sp, "DATA_PATH", f)
    monkeypatch.setattr(app, "_get_product",
                        lambda s: dict(CAT[s], slug=s) if s in CAT else None)
    monkeypatch.setattr(app, "_solution_learn_name", lambda s: PUBLIC.get(s))
    return app.app.test_client()


def test_hub_lists_visible_categories_only(client):
    r = client.get("/solutions/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/solutions/pemf"' in html
    # Fasting has no product and its learn page is still pending, so it stays hidden.
    assert "/solutions/fasting" not in html


def test_bare_solutions_redirects_to_the_hub(client):
    r = client.get("/solutions")
    assert r.status_code in (301, 308)
    assert r.headers["Location"].endswith("/solutions/")


def test_category_page_shows_cards_and_learn_link_and_drops_inactive(client):
    r = client.get("/solutions/pemf")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/begin/product/kloud-pemf-mini"' in html
    assert "/begin/product/old-mat" not in html
    assert 'href="/learn/cellular-energy"' in html


def test_unknown_slug_is_404(client):
    assert client.get("/solutions/no-such-thing").status_code == 404


def test_a_category_with_nothing_to_show_is_404(client):
    assert client.get("/solutions/fasting").status_code == 404


def test_a_missing_data_file_is_404_not_500(client, monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "DATA_PATH", tmp_path / "gone.json")
    assert client.get("/solutions/").status_code == 404
    assert client.get("/solutions/pemf").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `bash ci/run-tests.sh tests/test_solution_routes.py -q`
Expected: FAIL. The fixture raises `AttributeError: module 'app' has no attribute '_solution_learn_name'`.

- [ ] **Step 3: Write the minimal implementation**

Insert above `@app.route("/learn/patterns")` in `app.py`:

```python
def _solution_learn_name(slug):
    """The display name of an approved /learn/ topic, or None. A pending topic answers 200
    with a noindex 'preparing' page, so linking it would send a visitor to a stub."""
    from dashboard import topic_pages as _tp, topic_render as _tr
    try:
        with _db_lock, db.connect(LOG_DB) as cx:
            page = _tp.get_page(cx, slug)
    except Exception:
        return None
    if not _tr.is_public(page):
        return None
    return (page or {}).get("name") or slug.replace("-", " ").title()


def _solution_views():
    """Every category joined to the live catalogue. None when the data file is unreadable."""
    from dashboard import solution_pages as _sp
    try:
        cats = _sp.load()
    except (OSError, ValueError) as exc:
        print(f"[solutions] data file unreadable: {exc!r}", flush=True)
        return None
    return [_sp.category_view(c, _get_product, learn_name=_solution_learn_name) for c in cats]


@app.route("/solutions/")
def solutions_hub():
    from dashboard import solution_pages as _sp
    views = [v for v in (_solution_views() or []) if _sp.is_visible(v)]
    if not views:
        return ("Not found", 404)
    return Response(_sp.render_hub_html(views), mimetype="text/html")


@app.route("/solutions/<slug>")
def solutions_category(slug):
    from dashboard import solution_pages as _sp
    view = next((v for v in (_solution_views() or []) if v["slug"] == slug), None)
    if view is None or not _sp.is_visible(view):
        return ("Not found", 404)
    return Response(_sp.render_category_html(view), mimetype="text/html")
```

Flask redirects a bare `/solutions` to `/solutions/` on its own, because the rule ends in a slash.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash ci/run-tests.sh tests/test_solution_routes.py tests/test_solution_pages.py -q`
Expected: PASS, 23 tests.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_solution_routes.py
git commit -m "feat(solutions): serve /solutions/ and /solutions/<slug>"
```

---

### Task 4: Write the category data and its rule test

Clinical owns this task. Glen approves the membership lists before Task 5 starts.

**Files:**
- Create: `data/solution_categories.json`
- Test: `tests/test_solution_categories_data.py`

**Interfaces:**
- Consumes: Task 1's `load`, `problems` and `DATA_PATH`.
- Produces: the data file Task 3's routes serve.

- [ ] **Step 1: Write the failing data test**

```python
"""The real solution category file obeys the spec's rules against the real catalogue."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from dashboard import solution_pages as sp

ROOT = Path(__file__).resolve().parent.parent

# Remedy table names that are categories, from e4l.db formulations.category on 2026-09-30
# ('Solution category', 'Tools', 'Tools (laser, tuning fork, helmet)'). EVOX Session is a
# service, so it is out of scope.
TABLE_CATEGORY_NAMES = ["EMF reduction resources", "Fasting resources", "Photobiomodulation",
                        "Infrared & Red Light Photobiomodulation", "Infrared", "172 Hz"]


def _products():
    return json.load(open(ROOT / "data" / "products.json"))["products"]


def test_the_category_file_obeys_every_rule():
    assert sp.problems(sp.load(), _products(), TABLE_CATEGORY_NAMES) == []


def test_there_are_the_ten_approved_categories():
    assert [c["slug"] for c in sp.load()] == [
        "emf-protection", "water-hydrogen", "air", "light-photobiomodulation", "pemf",
        "microcurrent", "frequency-sound", "fasting", "stones-wearables", "books"]


def test_every_category_has_its_copy():
    empty = [c["slug"] for c in sp.load() if not (c.get("principle") or "").strip()]
    assert empty == [], f"no principle written for {empty}"


def test_the_plate_ionizers_are_listed():
    water = next(c for c in sp.load() if c["slug"] == "water-hydrogen")
    assert {"water-ionizer-5plate", "water-ionizer-9plate", "water-ionizer-15plate"} <= set(water["products"])


@pytest.mark.skipif(not os.environ.get("E4L_DB"), reason="set E4L_DB to check against e4l.db")
def test_table_names_match_e4l_db():
    cx = sqlite3.connect(os.environ["E4L_DB"])
    names = {r[0] for r in cx.execute(
        "SELECT name FROM formulations WHERE category IN "
        "('Solution category','Tools','Tools (laser, tuning fork, helmet)')")}
    assert names == set(TABLE_CATEGORY_NAMES)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash ci/run-tests.sh tests/test_solution_categories_data.py -q`
Expected: FAIL with `FileNotFoundError` for `data/solution_categories.json`.

- [ ] **Step 3: Write the data file**

The membership below was checked against `data/products.json` on 2026-09-30. Every slug exists and is active. It starts from the old store's hidden category pages.

Draft the `principle` and `choose_by_use` text in this step, in Glen's voice. Start from his clinicalpraxis.com pages where one exists: `/pemf` and `/photobiomodulation` ("Principle before product. Choose by intended use."). Each principle opens with one or two sentences that stand alone on the hub card. Describe what a tool supports, never what it treats. Use no em dashes and no ALL CAPS.

The `learn` slugs below are approved topics on the live site today. They are clinical's picks, for Glen to confirm.

```json
{
  "_comment": "Solution category pages. Spec: docs/superpowers/specs/2026-09-30-solution-category-pages-design.md. Product details come from data/products.json at render time; never copy them here.",
  "categories": [
    {"slug": "emf-protection", "title": "EMF Protection",
     "principle": "", "choose_by_use": [],
     "products": ["emf-shielding-pouch-7-x-4-cell-phone", "emf-shielding-pouch-7-x-9-tablet",
                  "emf-shielding-pouch-13-x-14-notebook", "emf-free-headset", "neutralizer-3-pack",
                  "car-neutralizer-usb", "whole-house-neutralizer", "aulterra-energy-pendant-silver-gold",
                  "circular-mithreal-silver-sleep-canopy", "square-mithreal-silver-sleep-canopy",
                  "large-mithreal-silver-lined-blanket", "small-mithreal-silver-lined-blanket",
                  "mithreal-silver-head-face-shield", "mithreal-knit-beanie-with-44-silver",
                  "mithreal-silver-hoodie-hat-with-42-silver", "mithreal-silver-baseball-cap",
                  "mithreal-silver-ivy-hat", "mithreal-silver-blue-zipper-hoodie",
                  "mithreal-silver-long-sleeve-shirt", "mithreal-silver-t-shirt",
                  "mithreal-t-shirt-with-70-silver", "boxers-with-mithreal-silver",
                  "mithreal-silver-socks", "book-emf-pollution-solutions"],
     "learn": ["emf-sensitivity"], "table_names": ["EMF reduction resources"]},
    {"slug": "water-hydrogen", "title": "Water & Hydrogen",
     "principle": "", "choose_by_use": [],
     "products": ["water-ionizer-5plate", "water-ionizer-9plate", "water-ionizer-15plate",
                  "molecular-hydrogen-bottle", "miracule-water-system",
                  "cds-water-purifier--activator", "shower-filter"],
     "learn": ["ph-balance", "oxidative-stress-support"], "table_names": []},
    {"slug": "air", "title": "Air",
     "principle": "", "choose_by_use": [],
     "products": ["freshair-mobile-purifier", "air-surface-pro-plus", "wearable-ionizer", "car-ionizer"],
     "learn": ["oxygenation"], "table_names": []},
    {"slug": "light-photobiomodulation", "title": "Light & Photobiomodulation",
     "principle": "", "choose_by_use": [],
     "products": ["harmony-laser", "acupuncture-point-cold-laser", "nir-brain-frequency-helmet",
                  "hair-growth-helmet", "photobiomodulation-package", "nir-nasal-clip",
                  "infrared-therapy-flashlight", "infrared-bamboo-bath-towel",
                  "blue-blocking-photochromic-sunglasses"],
     "learn": ["mitochondrial-energy", "circadian-rhythm-support"],
     "table_names": ["Photobiomodulation", "Infrared & Red Light Photobiomodulation", "Infrared"]},
    {"slug": "pemf", "title": "PEMF",
     "principle": "", "choose_by_use": [],
     "products": ["kloud-pemf-mini", "kloud-pemf-maxi", "nes-mihealth"],
     "learn": ["cellular-energy"], "table_names": []},
    {"slug": "microcurrent", "title": "Microcurrent",
     "principle": "", "choose_by_use": [],
     "products": ["denas-scenar", "denas-microcurrent-eye-system", "denas-eyeglasses-electrode",
                  "microgen-microcurrent-generator", "nes-mihealth", "vagus-nerve-stimulation-kit"],
     "learn": ["tissue-repair"], "table_names": []},
    {"slug": "frequency-sound", "title": "Frequency & Sound",
     "principle": "", "choose_by_use": [],
     "products": ["spirit-tuning-fork-172hz", "frosted-quartz-tuning-fork-172hz",
                  "tibetan-singing-bowl-172hz", "breath-tuning-fork-1283hz",
                  "mind-tuning-fork-5000hz", "harmony-laser", "nir-brain-frequency-helmet",
                  "bioenergetic-wellness-scanner"],
     "learn": ["biofield-coherence"], "table_names": ["172 Hz"]},
    {"slug": "fasting", "title": "Fasting",
     "principle": "", "choose_by_use": [],
     "products": [], "learn": ["fasting"], "table_names": ["Fasting resources"]},
    {"slug": "stones-wearables", "title": "Stones & Wearables",
     "principle": "", "choose_by_use": [],
     "products": ["shungite-stick-plate", "smokey-quartz-healing-tool", "aulterra-energy-pendant-silver-gold"],
     "learn": [], "table_names": []},
    {"slug": "books", "title": "Books",
     "principle": "", "choose_by_use": [],
     "products": ["book-refreshing-vision", "book-refreshing-vision-ebook", "book-cataract-solutions",
                  "book-cataract-solutions-ebook", "book-healing-glaucoma", "book-healing-glaucoma-ebook",
                  "book-macular-regeneration", "book-macular-regeneration-ebook", "book-dry-eye-relief",
                  "book-dry-eye-relief-ebook", "book-emf-pollution-solutions",
                  "book-emf-pollution-solutions-ebook", "book-natural-eye-care", "book-alternative-medicine",
                  "book-materia-medica", "book-materia-medica-ebook", "book-anima-medica",
                  "book-anima-medica-ebook", "book-nous-energy", "book-the-shire", "book-the-shire-ebook",
                  "book-living-universe", "book-living-universe-ebook", "book-as-above-so-below",
                  "book-transforming-your-life-iv", "book-achievement-of-excellence",
                  "book-achievement-of-excellence-audiobook"],
     "learn": [], "table_names": []}
  ]
}
```

Fill every empty `"principle"` and `"choose_by_use"` before the next step. An empty principle renders a page with a title and cards only.

Three catalogue items have no category: `emf-infoceutical`, `nes-scanner` and `hand-cradle`. List them for Glen with the membership. Do not add them.

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash ci/run-tests.sh tests/test_solution_categories_data.py -q`
Expected: PASS, 4 tests and 1 skipped.

Then run the e4l.db check once by hand:
`E4L_DB="$HOME/AI-Training/e4l.db" python3 -m pytest tests/test_solution_categories_data.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Mutate the guard and watch it fail**

Add `"fungifuge"` to the `air` products, and run Step 4 again. Expected: FAIL naming `air: product 'fungifuge' is on the do-not-recommend list.` Then remove `"water-ionizer-5plate"` from `water-hydrogen` and run again. Expected: FAIL in `test_the_plate_ionizers_are_listed`. Restore the file from a copy taken before the edits, never with `git checkout`, and run once more to see it pass.

- [ ] **Step 6: Commit**

```bash
git add data/solution_categories.json tests/test_solution_categories_data.py
git commit -m "feat(solutions): the ten solution categories and their rule test"
```

---

### Task 5: Copy review, render check and ship

**Files:**
- Modify: `data/solution_categories.json` (copy edits only)

- [ ] **Step 1: Copy round and three blind review rounds**

Put the ten principles and choose-by-use notes into one review page in the vault at `clinical/solutions-copy-review/solutions-copy.md`. Render it with `python3 "00 System/render-md.py" <md>`. Run the copy round. Then run three blind adversarial rounds, with round 2 through `00 System/scripts/review_round2.py`. Each round checks for treatment claims, invented specifics, em dashes and ALL CAPS. Apply the fixes to the data file.

- [ ] **Step 2: Glen approves the copy and the membership**

Give Glen the rendered page path. Wait for his approval before Step 3.

- [ ] **Step 3: Render check in a browser**

Run the app locally from this worktree, then open `/solutions/` and `/solutions/emf-protection` in Chrome. Confirm each card shows its name and price and opens `/begin/product/<slug>`. Confirm the "Learn more" links open approved topic pages. Confirm `/solutions/fasting` returns 404. Check the page at 390 px wide for horizontal scroll.

- [ ] **Step 4: Run the full suite**

Run: `bash ci/run-tests.sh`
Expected: no failure beyond `tests/known_failures.txt`.

- [ ] **Step 5: Commit, push and open the PR**

```bash
git add data/solution_categories.json
git commit -m "copy(solutions): Glen-approved principles and choose-by-use notes"
git push -u origin HEAD
gh pr create --title "Solution category pages: /solutions/ and /solutions/<slug>" --body-file <body file in the vault>
```

Ask Glen to merge it in the platform tab.

- [ ] **Step 6: Verify live**

After the deploy, fetch `https://illtowell.com/solutions/` and `https://illtowell.com/solutions/pemf`. Both must return 200 with product cards. `https://illtowell.com/solutions/fasting` must return 404. Then tell the marketing tab, since this is part of its Website Master Plan phase 2.
