"""Crystalline Clarity: the app page matches batch CC-20261027-40 and its approved text.

Spec: production/05 Formulations/crystalline-clarity/2026-10-28/app-spec.md (sha256
43bf1ab1ace2a3db). Glen approved the store text and caution on 2026-10-28 (formulation tab),
and "yes" to writing it to the catalogue (platform tab, same day). The page had shown the
earlier formula: lutein, curcumin and tetrahydrocurcumin rows, AI copy naming selenium, empty
directions and no caution.
"""
import copy
import importlib
import json
import os
import shutil
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "crystalline-clarity"
DIRECTIONS = "Take 1 capsule daily with food, or as guided."
WARNING = (
    "If you are pregnant or breastfeeding, or giving it to a child, use it only on your "
    "practitioner's advice. Take it at least six hours apart from antibiotics. "
    "Take it four hours apart from thyroid medicine. Consult your practitioner "
    "before use if you take prescription medications, especially blood sugar or blood "
    "thinning medicines. Also consult first if you have liver disease or Wilson's disease, "
    "or are planning surgery. Each capsule supplies 25 mg of zinc, so adults should keep "
    "total zinc from all sources under 40 mg a day. Stop use and consult your practitioner "
    "if you notice yellowing of the skin or eyes, or dark urine.")
INTRO = ("Crystalline Clarity supplies a broad range of nutrients and botanical extracts to "
         "support and activate pathways related to crystalline lens health and clarity.")
DESCRIPTION = ("Our Crystalline Clarity formula is supplied in organic Pullulan vegetarian "
               "capsules.\n\nWe use no unwanted excipients in order to achieve optimum "
               "bioavailability and health effects.")
PANEL_NOTE = ("Other ingredients: certified organic pullulan vegicaps, Healing Energy, Love "
              "and Prayer. 30 vegicaps. Vegan. No added fillers or excipients.")
PINNED = ["intro", "description", "directions", "research", "ingredients"]
SOURCE = "FileMaker 314, batch CC-20261027-40, label 2026-10-25"
EIGHT = [
    ("Zinc (as zinc L-carnosine, 109 mg)", "25 mg", "zinc-carnosine"),
    ("Copper (as copper glycinate)", "2 mg", "copper-glycinate"),
    ("Chromium (as chromium polynicotinate)", "198 mcg", "chromium-polynicotinate"),
    ("Rosemary carnosic acid 90% (Salvia rosmarinus) (leaf)", "100 mg", "carnosic-acid"),
    ("Chinese skullcap baicalein 98% (Scutellaria baicalensis) (root)", "100 mg", "baicalein"),
    ("Scholar tree quercetin dihydrate (Sophora japonica) (flower)", "57.5 mg",
     "quercetin-dihydrate-sophora-japonica"),
    ("L-ergothioneine 98%", "25 mg", "ergothioneine"),
    ("R-lipoic acid (as stabilized sodium R-lipoate)", "10 mg", "r-lipoic-acid"),
]
FIELDS = {"directions": DIRECTIONS, "warning": WARNING, "chat_caution": True,
          "intro": INTRO, "description": DESCRIPTION, "panel_note": PANEL_NOTE,
          "copy_pinned": PINNED, "ingredients_source": SOURCE}
REMOVED = ["lutein", "curcumin", "selenium", "tetrahydrocurcumin"]


def _data(name):
    """The repo file itself, never via DATA_DIR, which other tests repoint."""
    return json.load(open(os.path.join(ROOT, "data", name)))


def _rows(entry):
    return [(i["name"], i["dose"], i["page_slug"]) for i in entry["ingredients"]]


def _check(entry):
    assert {k: entry.get(k) for k in FIELDS} == FIELDS
    assert _rows(entry) == EIGHT
    # A full UTC timestamp: a bare date keeps drafts from earlier that day (spec).
    since = entry["content_since"]
    assert len(since) == 25 and since.endswith("+00:00") and since[10] == "T", since


def test_the_catalogue_entry():
    _check(_data("products.json")["products"][SLUG])


def test_the_corrections_entry_carries_every_field():
    corr = _data("products-manual-corrections.json")[SLUG]
    _check(corr)
    assert corr["content_since"] == _data("products.json")["products"][SLUG]["content_since"]


def test_a_full_enrichment_run_twice_on_a_copy_keeps_every_field(tmp_path, monkeypatch):
    """Spec read-back 5: main(), not only apply_corrections(). The clean store file still
    holds the old scraped description, which names selenium."""
    import scripts.apply_enrichment as ae
    for f in ("products-enrich-clean.json", "products.json", "products-manual-corrections.json"):
        src = os.path.join(ROOT, "data", f)
        if os.path.exists(src):
            shutil.copy(src, tmp_path / f)
    monkeypatch.setattr(ae, "CLEAN", str(tmp_path / "products-enrich-clean.json"))
    monkeypatch.setattr(ae, "PRODUCTS", str(tmp_path / "products.json"))
    monkeypatch.setattr(ae, "CORRECTIONS", str(tmp_path / "products-manual-corrections.json"))
    repo_bytes = open(os.path.join(ROOT, "data", "products.json"), "rb").read()
    # Strip the fields from the copy, so only main() can put them back (review round 3).
    cat = json.load(open(tmp_path / "products.json"))
    for k in ("directions", "warning", "intro", "panel_note", "copy_pinned", "chat_caution"):
        cat["products"][SLUG].pop(k)
    cat["products"][SLUG]["description"] = "Crystalline Clarity . selenium vegitarian"
    json.dump(cat, open(tmp_path / "products.json", "w"))
    for _ in range(2):
        ae.main()
        out = json.load(open(tmp_path / "products.json"))["products"][SLUG]
        _check(out)
        assert "selenium" not in out["description"].lower()
        assert "vegitarian" not in out["description"]
    assert open(os.path.join(ROOT, "data", "products.json"), "rb").read() == repo_bytes


def test_a_damaged_record_is_restored_by_the_replay():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(_data("products.json")["products"])
    for k in list(FIELDS) + ["ingredients", "content_since"]:
        products[SLUG].pop(k, None)
    products[SLUG]["description"] = "Crystalline Clarity . selenium"
    apply_corrections(products, copy.deepcopy(_data("products-manual-corrections.json")))
    _check(products[SLUG])


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    # Old-formula text from every AI source, so only the pins can keep it off the page
    # (review rounds 2 and 3).
    monkeypatch.setattr(a, "_product_card", lambda p: {
        "description": "AI CARD selenium", "benefits": [],
        "ingredients": [{"name": "Lutein", "dose": "10 mg"}]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW lutein curcumin")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    from dashboard import sales_pages as sp
    with sqlite3.connect(a.LOG_DB) as cx:
        sp.init_table(cx)
        for sec in ("intro", "description", "research"):
            sp.upsert_section(cx, SLUG, sec, "AI DRAFT selenium lutein")
        # Dated after content_since, so the reset cannot remove them: only the pins can.
        cx.execute("UPDATE sales_pages SET generated_at='2099-01-01T00:00:00' "
                   "WHERE product_slug=?", (SLUG,))
        cx.commit()
    return a


def test_the_page_shows_the_approved_text(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO and not secs["intro"].get("ai")
    assert secs["description"]["body"] == DESCRIPTION and not secs["description"].get("ai")
    body = secs["ingredients"]["body"]
    assert body["directions"] == DIRECTIONS
    assert [(i["name"], i["dose"], i["slug"]) for i in body["ingredients"]] == EIGHT
    assert PANEL_NOTE in json.dumps(body)
    assert secs["caution"]["body"] == WARNING
    assert "AI " not in json.dumps(d)
    # Research is pinned with no text: only the link to the learn page.
    assert secs["research"]["body"] == {"how_it_works": "",
                                        "learn_url": f"/begin/learn/{SLUG}"}
    for sid in ("intro", "description", "ingredients", "research", "caution"):
        text = json.dumps(secs[sid]["body"]).lower()
        for word in REMOVED:
            assert word not in text, (sid, word)


@pytest.mark.parametrize("q", ["How do I take Crystalline Clarity?",
                               "What is in Crystalline Clarity?",
                               "Does Crystalline Clarity contain selenium?"])
def test_the_chat_block_holds_the_eight_rows_and_the_directions(q):
    import app
    block = app.named_product_facts_block(q)
    assert "### Crystalline Clarity" in block, q
    assert "### Clarity\n" not in block
    section = block[block.index("### Crystalline Clarity"):].split("\n### ")[0]
    assert f"Directions: {DIRECTIONS}" in section
    assert "The Directions above are the only dose for Crystalline Clarity." in section
    assert DESCRIPTION in section
    for name, dose, _ in EIGHT:
        assert f"- {name}: {dose}" in section
    for word in REMOVED:
        assert word not in section.lower().replace("does crystalline clarity contain", ""), word


@pytest.mark.parametrize("q", ["Can I take Crystalline Clarity while pregnant?",
                               "Is Crystalline Clarity safe with my thyroid medicine?",
                               "Is Crystalline Clarity safe?",
                               "Are there side effects with Crystalline Clarity?",
                               "Is Crystalline-Clarity safe in pregnancy?"])
def test_a_safety_question_links_its_caution(q):
    import app
    assert SLUG in app._caution_link_slugs(q)


def test_the_learn_page_shows_the_pinned_description(appmod):
    r = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}")
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert json.loads(text)["markdown"] == DESCRIPTION
    for word in REMOVED:
        assert word not in text.lower(), word


def test_a_plain_dose_question_gets_the_caution_written():
    import app
    block = app.named_product_facts_block("How do I take Crystalline Clarity?")
    section = block[block.index("### Crystalline Clarity"):].split("\n### ")[0]
    assert f"Caution: {WARNING}" in section


@pytest.mark.parametrize("q,want", [
    ("Is Crystalline-Clarity safe in pregnancy?", {SLUG}),
    ("Crystalline/Clarity dose?", {SLUG}),
    ("Is Crystalline\u2013Clarity safe?", {SLUG}),
    ("Is Crystalline\u2014Clarity safe?", {SLUG}),
    ("Crystalline -- Clarity dose?", {SLUG}),
    ("Crystalline - Clarity dose?", {SLUG}),
    ("Crystalline_Clarity dose?", {SLUG}),
    # A misspelling names neither product, rather than Clarity and its caution.
    ("Crystaline Clarity dose?", set()),
    ("Crystaline-Clarity dose?", set()),
    ("Crystaline/Clarity dose?", set()),
    ("Crystaline - Clarity dose?", set()),
    ("Crystaline\u2013Clarity dose?", set()),
    ("Crystallinne Clarity dose?", set()),
    ("Crystline Clarity dose?", set()),
    ("Crystal Clarity dose?", set()),
    ("Crystaline Clarity dose? I have cataracts in both of my eyes.", set()),
    ("Crystallene Clarity dose?", set()),
    ("Crystallne-Clarity safe in pregnancy?", set()),
    ("Crystalyne Clarity dose?", set()),
    ("Crystallin Clarity dose?", set()),
    ("Crystaalline Clarity dose?", set()),
    ("Crystallized Clarity dose?", set()),
    ("Crystalsine Clarity dose?", set()),
    ("Crystallinnnnnnnnnnnnnnnnnnne Clarity dose?", set()),
    ("Monocrystalline Clarity dose?", {"clarity"}),
    ("Crystaline\tClarity dose?", set()),
    ("Crystaline _ Clarity dose?", set()),
    ("Crystalline  Clarity dose?", {SLUG}),
    ("Crystalline  -  Clarity dose?", {SLUG}),
    ("Crystalline---Clarity dose?", {SLUG}),
    ("Crystalline_Clarity / Clarity dose?", {SLUG, "clarity"}),
    ("Vitreous_Vitality / Vitality dose?", {"vitreous-vitality", "vitality"}),
    # Clarity itself, beside other crystal words (review round 2).
    ("How much Clarity should I take?", {"clarity"}),
    ("crystal clear. Clarity dose?", {"clarity"}),
    ("I love crystal. Clarity dose?", {"clarity"}),
    ("Can I take vitamin C crystals / Clarity during pregnancy?", {"clarity"}),
    ("I like crystals. Clarity dose?", {"clarity"}),
    ("I bought crystals and Clarity, dose?", {"clarity"}),
    ("Crystal clear vision, Clarity dose?", {"clarity"}),
    ("Crystalline Clarity and Clarity dose?", {SLUG, "clarity"}),
    # The joiner stays with the joined names (round 3).
    ("Is Vitreous\u2014Vitality safe?", {"vitreous-vitality"}),
    ("Vitrious/Vitality dose?", set()),
    ("Neuro-Magnesium dose?", set())])
def test_a_joined_or_misspelt_name_never_brings_clarity(q, want):
    """Clarity is a different product with its own caution (review round 1)."""
    import app
    assert set(app._named_product_spans(q, app._PRODUCTS["products"])) == want


@pytest.mark.parametrize("dash", ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015"])
def test_every_dash_joins_and_yields(dash):
    import app
    P = app._PRODUCTS["products"]
    assert set(app._named_product_spans(f"Crystalline{dash}Clarity dose?", P)) == {SLUG}
    assert set(app._named_product_spans(f"Crystaline{dash}Clarity dose?", P)) == set()
    assert set(app._named_product_spans(f"Vitreous{dash}Vitality dose?", P)) == \
        {"vitreous-vitality"}
    assert set(app._named_product_spans(f"Vitreus{dash}Vitality dose?", P)) == set()


def test_a_safety_question_never_links_clarity_for_crystalline_clarity():
    import app
    for q in ["Is Crystalline-Clarity safe in pregnancy?", "Is Crystalline\u2014Clarity safe?",
              "Can I take Crystalline Clarity while pregnant?"]:
        links = app._caution_link_slugs(q)
        assert "clarity" not in links and SLUG in links, (q, links)
