"""Estro-Clear: research-checked ingredient roles on the page and in the chat.

Spec: production/05 Formulations/_store-updates/2026-10-10-estro-clear-roles.md (approved
by Glen 2026-10-10, "yes", as written). The intro, how-it-works, learn text and benefits
are pinned verbatim, the page shows no AI image, and the chat's facts block carries each
ingredient's role beside its own label line. Black pepper has no role.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "estro-clear"
PEPPER = "Black pepper extract (Piper nigrum), 95% piperine"
ROLES = {
    "Calcium-D-glucarate": "Helps processed estrogen leave the body instead of being reabsorbed.",
    "DIM 98% (diindolylmethane)": "Supports a healthy balance of estrogen metabolites.",
    "Broccoli extract (Brassica oleracea), 17% glucoraphanin":
        "Supplies glucoraphanin, which supports the body's own detox enzymes.",
    "Vitamin C (L-ascorbic acid)": "Helps activate the enzyme that releases sulforaphane from broccoli.",
    "Vitamin D3 (cholecalciferol)": "Supports normal cell growth and division.",
    "Black mustard seed (Brassica nigra)":
        "Supplies myrosinase, the enzyme that activates broccoli glucoraphanin.",
    "Iodine (from potassium iodide)": "Supports normal thyroid hormone production.",
    "Selenium (as methylselenocysteine)": "Supports the body's own antioxidant enzymes.",
}
INTRO = ("Estro-Clear helps processed estrogen leave the body and supports a healthy balance of "
         "estrogen metabolites.")
HOW = (
    "Your body is always working to clear hormones that have already done their job. Here is "
    "what each ingredient in Estro-Clear does.\n\n"
    "Calcium-D-glucarate helps processed estrogen leave the body instead of being reabsorbed. "
    "DIM supports a healthy balance of estrogen metabolites. Broccoli extract supplies "
    "glucoraphanin, which supports the body's own detox enzymes. Black mustard seed supplies "
    "myrosinase, the enzyme that activates broccoli glucoraphanin. Vitamin C helps activate the "
    "enzyme that releases sulforaphane from broccoli. Iodine supports normal thyroid hormone "
    "production. Selenium supports the body's own antioxidant enzymes. Vitamin D3 supports "
    "normal cell growth and division.")
BENEFITS = [
    "Helps processed estrogen leave the body instead of being reabsorbed",
    "Supports a healthy balance of estrogen metabolites",
    "Supports the body's own detox and antioxidant enzymes",
    "Supports normal thyroid hormone production",
]
PINS = ["description", "ingredients", "intro", "research", "benefits", "roles"]
FIELDS = {"intro": INTRO, "how_it_works": HOW, "learn_markdown": HOW, "benefits": BENEFITS,
          "ingredient_roles": ROLES, "copy_pinned": PINS}


@pytest.fixture(scope="module")
def products():
    return json.load(open(os.path.join(ROOT, "data", "products.json")))["products"]


@pytest.fixture(scope="module")
def corrections():
    return json.load(open(os.path.join(ROOT, "data", "products-manual-corrections.json")))


def test_the_approved_texts_are_stored_verbatim(products):
    for field, value in FIELDS.items():
        assert products[SLUG].get(field) == value, field


def test_the_pins_are_exact(products):
    # No live read-back can see a dropped benefits pin, so the list is held here.
    assert products[SLUG]["copy_pinned"] == PINS


def test_every_role_names_a_label_line(products):
    # A renamed label line must fail here, not silently lose its role in the chat.
    names = {i["name"] for i in products[SLUG]["ingredients"]}
    assert set(products[SLUG]["ingredient_roles"]) <= names
    assert PEPPER in names and PEPPER not in products[SLUG]["ingredient_roles"]


def test_no_other_product_carries_roles(products):
    assert [s for s, p in products.items() if "ingredient_roles" in p] == [SLUG]


def test_the_corrections_file_carries_the_same_fields(products, corrections):
    for field in FIELDS:
        assert corrections[SLUG].get(field) == products[SLUG][field], field


def test_the_enrichment_replay_restores_every_new_field(products, corrections):
    import copy
    from scripts.apply_enrichment import apply_corrections
    damaged = copy.deepcopy(products)
    for field in FIELDS:
        damaged[SLUG].pop(field, None)
    apply_corrections(damaged, copy.deepcopy(corrections))
    for field, value in FIELDS.items():
        assert damaged[SLUG].get(field) == value, field


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALES_PAGES_ENABLED", "true")
    monkeypatch.setenv("SALES_PAGES_AI_COPY", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    from dashboard import db, sales_pages
    with db.connect(appmod.LOG_DB) as cx:
        sales_pages.init_table(cx)
    yield appmod
    monkeypatch.undo()
    importlib.reload(appmod)


def _line(block, name):
    lines = [ln for ln in block.splitlines() if ln.startswith(f"- {name}")]
    assert len(lines) == 1, name
    return lines[0]


def test_the_chat_carries_each_role_beside_its_own_ingredient(fresh_app):
    block = fresh_app._named_product_facts("What does each ingredient in Estro-Clear do?")
    for name, role in ROLES.items():
        assert _line(block, name).endswith(f". Role: {role}"), name
    assert "Role:" not in _line(block, PEPPER)
    assert block.count("Role: ") == 8
    assert "Ingredient roles for Estro-Clear:" in block


def test_without_the_roles_pin_the_chat_carries_no_roles(fresh_app, monkeypatch):
    p = dict(fresh_app._PRODUCTS["products"][SLUG])
    p["copy_pinned"] = [x for x in p["copy_pinned"] if x != "roles"]
    monkeypatch.setitem(fresh_app._PRODUCTS["products"], SLUG, p)
    block = fresh_app._named_product_facts("What does each ingredient in Estro-Clear do?")
    assert "Ingredients from the label:" in block
    assert "Role:" not in block and "Ingredient roles" not in block


def test_a_product_without_roles_gets_no_roles_rule(fresh_app):
    block = fresh_app._named_product_facts("What is in Fibrosolve?")
    assert "Ingredients from the label:" in block
    assert "Role:" not in block and "Ingredient roles" not in block


def test_the_page_serves_the_pinned_texts(fresh_app, monkeypatch):
    monkeypatch.setattr(fresh_app, "_product_how", lambda p: "AI HOW cofactor MARKER")
    # Live had a stored AI intro draft. A stored draft, dated after content_since so the
    # reset leaves it, must lose to the pin.
    import sqlite3
    from dashboard import sales_pages as sp
    with sqlite3.connect(fresh_app.LOG_DB) as cx:
        sp.init_table(cx)
        for sec in ("intro", "description", "research", "benefits"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT cofactor MARKER")
        cx.execute("UPDATE sales_pages SET generated_at='2099-01-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    c = fresh_app.app.test_client()
    pd = c.get(f"/begin/product-data/{SLUG}").get_json()
    assert pd["how_it_works"] == HOW
    assert pd["benefits"] == BENEFITS
    data = c.get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in data["sections"]}
    assert secs["intro"]["body"] == INTRO and "ai" not in secs["intro"]
    assert secs["research"]["body"]["how_it_works"] == HOW and "ai" not in secs["research"]
    assert "cofactor" not in json.dumps(data).lower()
    assert "cofactor" not in json.dumps(pd).lower()
    assert c.get(f"/begin/product-page-gen/{SLUG}/intro").status_code == 404
    assert c.get(f"/begin/learn-data/{SLUG}").get_json()["markdown"] == HOW


def test_no_ai_image_is_queued_or_served(fresh_app):
    import sqlite3
    from dashboard import sales_images as si
    with sqlite3.connect(fresh_app.LOG_DB) as cx:
        si.init_tables(cx)
        assert si.enqueue(cx, SLUG) is False
        # A row written before the opt-out is never drained.
        cx.execute("INSERT INTO sales_image_queue (product_slug, state, requested_at, updated_at) "
                   "VALUES (?, 'pending', '1', '1')", (SLUG,))
        assert si.enqueue(cx, "angiogenx") is True
        assert si.list_pending(cx) == ["angiogenx"]
    # The same file serves for a product that is not opted out, so the 404 is the opt-out.
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    for slug in (SLUG, "angiogenx"):
        d = fresh_app._SALES_IMG_DIR / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "mechanism-1.png").write_bytes(png)
    c = fresh_app.app.test_client()
    assert c.get("/begin/product-image/angiogenx/mechanism-1.png").status_code == 200
    assert c.get(f"/begin/product-image/{SLUG}/mechanism-1.png").status_code == 404
