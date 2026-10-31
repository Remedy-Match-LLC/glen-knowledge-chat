"""Brain Boost: two app records become one, with the July 2026 label's rows.

Glen, 30 October 2026: Brain Boost and Brain Boost Nootropic are "one product"; the shelf
bottle lists "citicoline, L-theanine, PS" ("July label"); no older Brain Boost bottle is
still for sale ("no"), so the vinpocetine line comes off; "pin". PS is from soy (Glen,
2026-09-21). Directions per Glen, 31 October (production tab), "yes" for the app and store
(formulation tab). Rows stay per 2-capsule serving, as on the bottle.
Spec: production/05 Formulations/_store-updates/2026-10-30-brain-boost-one-record.md.
"""
import importlib
import json
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = "brain-boost"
OLD = "brain-boost-nootropic"
ROWS = [("Citicoline (CDP-Choline)", "500 mg"),
        ("L-Theanine", "200 mg"),
        ("Phosphatidylserine (from soy)", "100 mg")]
DIRECTIONS = "1 to 2 capsules daily with breakfast and/or lunch, or as guided."
DESCRIPTION = ("Each serving of Brain Boost, 2 vegicaps, combines citicoline 500 mg, "
               "L-theanine 200 mg and phosphatidylserine 100 mg.")
PANEL = "Contains: Soy."
PINNED = ["ingredients", "directions", "description"]
LEGACY = "https://remedymatch.com/remedies/syntropy/14-brain-boost"
VINPOCETINE = "contains vinpocetine"
# Words from the old formula and its old description.
GONE = ("vinpocetine", "coluracetam", "centrophenoxine", "ginkgo", "bacopa", "bacosides",
        "color vision", "cynarin", "moringa", "fulvic", "spirit minerals", "choline complex")


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


def _assert_merged(products):
    old = products[OLD]
    assert old["inactive"] is True and old["superseded_by"] == KEEP
    assert "fmp_id" not in old
    s = products[KEEP]
    assert not s.get("inactive") and not s.get("superseded_by")
    assert (s["name"], s["pinecone_title"]) == ("Brain Boost", "Brain Boost")
    assert s["fmp_id"] == "365"
    assert [(r["name"], r["dose"]) for r in s["ingredients"]] == ROWS
    assert s["ingredients_source"] == "label-read-2026-10-30"
    assert (s["directions"], s["description"], s["panel_note"]) == (DIRECTIONS, DESCRIPTION, PANEL)
    assert s["copy_pinned"] == PINNED
    assert s["regular_cents"] == 8000
    assert s["legacy_store_url"] == LEGACY
    assert s["chat_caution"] is False
    assert "warning" not in s
    assert s["content_since"].endswith("+00:00")
    assert "coluracetam" not in s["enrichment_note"].lower()
    assert products["brain-program"]["caution_components"] == ["brain-cleanse"]


# ---- the stored records ------------------------------------------------------------

def test_the_catalog_holds_one_live_brain_boost():
    _assert_merged(_load("products.json"))


def test_only_the_survivor_has_a_corrections_entry():
    c, p = _load("products-manual-corrections.json"), _load("products.json")
    assert OLD not in c
    e = c[KEEP]
    assert [(r["name"], r["dose"]) for r in e["ingredients"]] == ROWS
    assert e["ingredients_source"] == "label-read-2026-10-30"
    assert (e["directions"], e["description"], e["panel_note"]) == (DIRECTIONS, DESCRIPTION, PANEL)
    assert e["copy_pinned"] == PINNED
    assert e["content_since"] == p[KEEP]["content_since"]
    # An explicit false, replayed by key presence, keeps the bot opt-in withdrawn.
    assert e["chat_caution"] is False and "warning" not in e
    assert e["note"] == p[KEEP]["enrichment_note"]
    assert c["brain-program"]["caution_components"] == ["brain-cleanse"]


def test_no_string_field_names_the_old_formula():
    s = _load("products.json")[KEEP]
    text = " ".join(str(v) for k, v in s.items() if k != "enrichment_note").lower()
    text += json.dumps(s["ingredients"]).lower()
    for word in GONE:
        assert word not in text, word


def test_the_store_id_14_reaches_the_survivor():
    from dashboard import legacy_store_links as lsl
    products = _load("products.json")
    assert lsl.build_map(products)["14"] == lsl._page_for(KEEP, products)


def test_the_other_files_name_the_survivor():
    assert json.load(open(os.path.join(ROOT, "data", "fmp_slug_map.json")))["resolved"]["365"] == KEEP
    for name in os.listdir(os.path.join(ROOT, "data")):
        if not name.endswith(".json") or name in ("products.json", "atlas-concepts.json",
                                                   "atlas-seed-input.json"):
            continue
        text = open(os.path.join(ROOT, "data", name), encoding="utf-8").read()
        assert f'"{OLD}"' not in text, name


def test_the_worklist_no_longer_appends_to_it():
    from scripts import apply_worklist as w
    assert KEEP not in w.SECTION_A


# ---- the replay ----------------------------------------------------------------------

def _copy_data(where, monkeypatch, ae):
    where.mkdir()
    for name, attr in (("products.json", "PRODUCTS"), ("products-enrich-clean.json", "CLEAN"),
                       ("products-manual-corrections.json", "CORRECTIONS")):
        shutil.copy(os.path.join(ROOT, "data", name), where / name)
        monkeypatch.setattr(ae, attr, str(where / name))


def test_a_rebuild_twice_keeps_the_merge(tmp_path, monkeypatch):
    """The spec's replay test: main() twice on a copy keeps steps 2 to 10."""
    from scripts import apply_enrichment as ae
    repo_bytes = open(os.path.join(ROOT, "data", "products.json"), "rb").read()
    _copy_data(tmp_path / "d", monkeypatch, ae)
    for _ in range(2):
        ae.main()
        _assert_merged(json.load(open(tmp_path / "d" / "products.json"))["products"])
    assert open(os.path.join(ROOT, "data", "products.json"), "rb").read() == repo_bytes


def test_a_rebuild_from_the_old_record_restores_the_replayed_fields(tmp_path, monkeypatch):
    """Review round 2: test the replay from an old catalogue, not only an already-migrated one.
    The fields the corrections entry carries come back over the old ones; the fields the
    replay never writes (name, fmp_id, retirement, price, store link) are hand edits."""
    from scripts import apply_enrichment as ae
    _copy_data(tmp_path / "d", monkeypatch, ae)
    path = tmp_path / "d" / "products.json"
    doc = json.load(open(path))
    s = doc["products"][KEEP]
    # Review round 2: the old vinpocetine warning goes in too. A rebuild merges onto the
    # existing record and cannot delete a warning, so removing it is a hand edit to
    # products.json. This records that limit; test_both_files... checks the hand edit.
    s.update(chat_caution=True, description="Brain Boost ... includes Coluracetam",
             warning="This formula contains vinpocetine, so do not use it if you are pregnant.",
             ingredients=[{"name": "Vinpocetine (Vinca minor)", "dose": "1 mg"}])
    for k in ("directions", "panel_note", "copy_pinned", "content_since"):
        s.pop(k)
    doc["products"]["brain-program"]["caution_components"] = ["brain-cleanse", KEEP]
    json.dump(doc, open(path, "w"))
    ae.main()
    ae.main()
    products = json.load(open(path))["products"]
    assert "vinpocetine" in products[KEEP].pop("warning")
    _assert_merged(products)


def test_a_rebuild_does_not_flag_the_store_page_as_stale(tmp_path, monkeypatch):
    """Spec step 10: the store page 14-brain-boost carries the label (read 31 October
    18:58 UTC), so the gk_stale flag was removed from products-enrich-clean.json."""
    from scripts import apply_enrichment as ae
    _copy_data(tmp_path / "d", monkeypatch, ae)
    ae.main()
    s = json.load(open(tmp_path / "d" / "products.json"))["products"][KEEP]
    assert "gk_stale" not in s and "stale_reason" not in s


# ---- the atlas -----------------------------------------------------------------------

def test_the_atlas_concept_describes_the_label():
    for path, full in (("data/atlas-concepts.json", True), ("data/atlas-seed-input.json", False)):
        data = json.load(open(os.path.join(ROOT, path)))
        concepts = data["concepts"] if isinstance(data, dict) else data
        by = {c["id"]: c for c in concepts}
        c = by[OLD]
        assert c["label"] == "Brain Boost", path
        assert c["summary"] == DESCRIPTION, path
        assert [(l["type"], l["title"], l["url"]) for l in c["links"]] == [
            ("product", "Brain Boost", "https://myhealingoasis.com/begin/product/brain-boost")], path
        assert "coluracetam" not in json.dumps(c).lower(), path
        if full:
            assert c["neighbors"] == ["brain-program", "brain-cleanse"]
            for other in ("coluracetam", "coluracetam-bci-540-mkc-231"):
                assert OLD not in by[other]["neighbors"]


# ---- the chat ------------------------------------------------------------------------

@pytest.fixture
def appmod():
    import app
    return app


def _named(appmod, q):
    return set(appmod._named_product_spans(q, appmod._PRODUCTS["products"]))


def test_the_names_lists(appmod):
    assert KEEP in appmod._CUE_NAMES
    assert KEEP not in appmod._YIELDS_TO_LONGER_NAMES


@pytest.mark.parametrize("q", [
    "What is in Brain Boost?",
    "what is in brain boost?",
    "Can I take Brain Boost while pregnant?",
    "is brain boost safe while pregnant?",
    "What is in Brain Boost Nootropic?",
])
def test_a_question_brings_the_label_facts_and_no_caution(appmod, q):
    assert _named(appmod, q) == {KEEP}, q
    assert KEEP not in appmod._caution_link_slugs(q)
    b = appmod.named_product_facts_block(q)
    assert "### Brain Boost\n" in b
    for name, dose in ROWS:
        assert f"- {name}: {dose}" in b
    assert f"Directions: {DIRECTIONS}" in b
    assert appmod._NO_CAUTION.format(name="Brain Boost")[:60] in b
    low = b.lower()
    for word in GONE:
        assert word not in low, (q, word)


def test_an_ordinary_phrase_still_names_nothing(appmod):
    assert KEEP not in _named(appmod, "how do i boost my brain power?")


def test_the_brain_program_lists_only_brain_cleanse(appmod):
    b = appmod.named_product_facts_block("What should I know before taking the Brain Program?")
    assert VINPOCETINE not in b and "Caution for Brain Cleanse" in b


# ---- the pages -----------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


def test_the_old_page_lands_on_the_survivor(client):
    r = client.app.test_client().get(f"/begin/product/{OLD}?utm_source=x")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/begin/product/{KEEP}?utm_source=x")


def test_the_page_shows_the_label_rows_and_no_caution(client, monkeypatch):
    monkeypatch.setattr(client, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(client, "_product_how", lambda p: "AI HOW MARKER")
    d = client.app.test_client().get(f"/begin/product-page-data/{KEEP}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    body = secs["ingredients"]["body"]
    assert [(r["name"], r["dose"]) for r in body["ingredients"]] == ROWS
    assert body["directions"] == DIRECTIONS
    assert body["note"] == PANEL
    assert not any(i.startswith("caution") for i in secs)
    page = json.dumps(d).lower()
    for word in GONE:
        assert word not in page, word


def test_the_brain_program_page_shows_only_brain_cleanse(client):
    d = client.app.test_client().get("/begin/product-page-data/brain-program").get_json()
    ids = [s["id"] for s in d["sections"] if s["id"].startswith("caution")]
    assert ids == ["caution-brain-cleanse"]


OLD_DRAFT = "2026-10-30T12:00:00+00:00"   # before the label read: describes the old rows


def test_the_cutoff_drops_a_cached_draft_written_before_it(monkeypatch):
    from dashboard import product_content as pc
    product = dict(_load("products.json")[KEEP], slug=KEEP)
    assert OLD_DRAFT < product["content_since"]
    hit = {"content": {"text": "OLD: coluracetam"}, "sources": [],
           "generated_at": OLD_DRAFT, "cached": True}
    monkeypatch.setattr(pc, "_cache_get", lambda slug, ctype: hit)
    monkeypatch.setattr(pc, "_cache_put", lambda *a: None)
    monkeypatch.setattr(pc, "_page_text", lambda p: None)
    monkeypatch.setattr(pc, "_page_text_from_product", lambda p: None)
    monkeypatch.setattr(pc, "_generate_how_it_works", lambda p, page: {"text": "NEW"})
    assert pc.get_or_generate(product, "how_it_works") is not hit
