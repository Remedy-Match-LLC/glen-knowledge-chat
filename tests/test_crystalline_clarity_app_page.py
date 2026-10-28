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

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "crystalline-clarity"
DIRECTIONS = "Take 1 capsule daily with food, or as guided."
WARNING = (
    "If you are pregnant or breastfeeding, or giving it to a child, use it only on your "
    "practitioner's advice. Take it at least two hours before or six hours after "
    "antibiotics. Take it four hours apart from thyroid medicine. Consult your practitioner "
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
    for _ in range(2):
        ae.main()
        out = json.load(open(tmp_path / "products.json"))["products"][SLUG]
        _check(out)
        assert "selenium" not in out["description"].lower()
        assert "vegitarian" not in out["description"]
    _check(_data("products.json")["products"][SLUG])


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
    # Research is pinned with no text: no generated how-it-works or research draft.
    assert secs["research"]["body"].get("how_it_works", "") == ""
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
    for name, dose, _ in EIGHT:
        assert f"- {name}: {dose}" in section
    for word in REMOVED:
        assert word not in section.lower().replace("does crystalline clarity contain", ""), word


@pytest.mark.parametrize("q", ["Can I take Crystalline Clarity while pregnant?",
                               "Is Crystalline Clarity safe with my thyroid medicine?"])
def test_a_safety_question_links_its_caution(q):
    import app
    assert SLUG in app._caution_link_slugs(q)


def test_the_learn_page_shows_the_pinned_description(appmod):
    r = appmod.app.test_client().get(f"/begin/learn-data/{SLUG}")
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert "We use no unwanted excipients" in text
    for word in REMOVED:
        assert word not in text.lower(), word
