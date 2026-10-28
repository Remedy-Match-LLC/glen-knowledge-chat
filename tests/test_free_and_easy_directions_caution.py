"""Free & Easy: directions, a caution and extract names for its nine ingredients.

Spec: production/05 Formulations/free-and-easy/2026-10-27/store-spec.md (sha256 9516058cf75d4e56).
Glen approved the directions and caution in the formulation tab on 2026-10-27. The floaters
ebook links buyers here and carries no caution, so this page is their only one.
Amounts are unchanged (FileMaker 470, run 44). Each row keeps its existing ingredient page.
"""
import copy
import importlib
import json
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "free-and-easy"
DIRECTIONS = "Take 1 capsule daily with dinner or an evening snack, or as guided."
WARNING = ("Not for use in pregnancy or while breastfeeding. If you are under medical "
           "treatment, consult your physician before use.")
NINE = [
    ("Peony extract (Paeonia albiflora), standardized to 50% paeoniflorin", "159 mg",
     "paeoniflorin"),
    ("Poria (Fu Ling) extract (Poria cocos), 50% polysaccharides", "127 mg",
     "fu-ling-hoelen-from-dutch-trade-with-jap"),
    ("Atractylodes extract 30:1 (Atractylodes macrocephala)", "8 mg",
     "atractylodes-macrocephala-30-1-rhizoma-a"),
    ("Gardenia extract (Gardenia jasminoides), standardized to 45% crocin", "48 mg",
     "crocin-45-gardenia-jasminoides"),
    ("Bupleurum extract (Bupleurum chinense), standardized to 50% saikosaponins", "9.5 mg",
     "saikosaponin-50-bupleurum-chinense"),
    ("Ashitaba extract 20:1 (Angelica keiskei)", "24 mg", "ashitaba"),
    ("Ginger extract (Zingiber officinale), standardized to 10% gingerols", "12.7 mg",
     "gingerols"),
    ("Licorice (Glycyrrhiza glabra), 5% total acids", "9.5 mg",
     "total-acid-glycyrrhizic-acid-glycyrrhizi"),
    ("Peppermint extract 10:1 (Mentha piperita)", "4.8 mg", "peppermint"),
]
OLD_NAMES = ["Paeoniflorin", "Fu Ling, Hoelen (from Dutch trade with Japan)",
             "total acid (Glycyrrhizic acid, Glycyrrhizinic acid)", "Gingerols"]


def _data(name):
    return json.load(open(os.path.join(ROOT, "data", name)))


def _rows(entry):
    return [(i["name"], i["dose"], i["page_slug"]) for i in entry["ingredients"]]


def test_the_catalogue_entry():
    e = _data("products.json")["products"][SLUG]
    assert e["directions"] == DIRECTIONS
    assert e["warning"] == WARNING
    assert e["chat_caution"] is True
    assert _rows(e) == NINE
    assert sum(float(d.split()[0]) for _, d, _ in NINE) == pytest.approx(402.5)


def test_the_corrections_entry_matches_so_a_replay_keeps_it():
    c = _data("products-manual-corrections.json")[SLUG]
    e = _data("products.json")["products"][SLUG]
    assert _rows(c) == NINE
    for field in ("directions", "warning", "chat_caution", "ingredients"):
        assert c[field] == e[field], field
    assert not any(o in json.dumps(c["ingredients"]) for o in OLD_NAMES)


def test_each_row_keeps_the_page_it_had():
    """The slugs are what the old names gave, by the page code's own slugify."""
    from dashboard.ingredients import slugify
    old = ["Paeoniflorin", "Fu Ling, Hoelen (from Dutch trade with Japan)",
           "Atractylodes macrocephala 30:1 Rhizoma Atractylodis large-headed atractylodes, "
           "white atractylodes", "Crocin 45% (Gardenia jasminoides)",
           "Saikosaponin 50% (Bupleurum chinense)", "Ashitaba", "Gingerols",
           "total acid (Glycyrrhizic acid, Glycyrrhizinic acid)", "Peppermint"]
    assert [slugify(n) for n in old] == [s for _, _, s in NINE]


def test_the_replay_puts_the_new_rows_back_over_old_ones():
    from scripts.apply_enrichment import apply_corrections
    products = _data("products.json")["products"]
    damaged = copy.deepcopy(products)
    damaged[SLUG]["ingredients"] = [{"name": n, "dose": "1 mg"} for n in OLD_NAMES]
    for f in ("directions", "warning", "chat_caution"):
        damaged[SLUG].pop(f, None)
    apply_corrections(damaged, _data("products-manual-corrections.json"))
    assert _rows(damaged[SLUG]) == NINE
    assert damaged[SLUG]["directions"] == DIRECTIONS and damaged[SLUG]["warning"] == WARNING
    assert damaged[SLUG]["chat_caution"] is True


def test_a_full_enrichment_run_on_a_copy_keeps_the_nine_rows(tmp_path, monkeypatch):
    """Spec step 6: run scripts/apply_enrichment.py against a COPY of the data and read
    what it wrote."""
    import scripts.apply_enrichment as ae
    for f in ("products-enrich-clean.json", "products.json", "products-manual-corrections.json"):
        src = os.path.join(ROOT, "data", f)
        if os.path.exists(src):
            shutil.copy(src, tmp_path / f)
    monkeypatch.setattr(ae, "CLEAN", str(tmp_path / "products-enrich-clean.json"))
    monkeypatch.setattr(ae, "PRODUCTS", str(tmp_path / "products.json"))
    monkeypatch.setattr(ae, "CORRECTIONS", str(tmp_path / "products-manual-corrections.json"))
    ae.main()
    out = json.load(open(tmp_path / "products.json"))["products"][SLUG]
    assert _rows(out) == NINE
    assert out["directions"] == DIRECTIONS and out["warning"] == WARNING
    assert out["chat_caution"] is True
    # The real data files were not touched.
    assert _rows(_data("products.json")["products"][SLUG]) == NINE


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    return a


def test_the_page_shows_the_directions_caution_and_nine_rows(appmod):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{SLUG}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    body = secs["ingredients"]["body"]
    assert body["directions"] == DIRECTIONS
    assert secs["caution"]["body"] == WARNING
    assert [(i["name"], i["dose"], i["slug"]) for i in body["ingredients"]] == NINE


def test_a_made_up_slug_still_404s(appmod):
    r = appmod.app.test_client().get("/begin/product-page-data/free-and-easy-not-real")
    assert r.status_code == 404


@pytest.mark.parametrize("q", ["Can I take Free & Easy while pregnant?",
                               "Is Free & Easy safe while breastfeeding?"])
def test_a_safety_question_links_its_caution(q):
    import app
    assert SLUG in app._caution_link_slugs(q)


def test_a_panel_rebuild_from_the_database_leaves_the_pinned_rows(tmp_path, monkeypatch):
    """scripts/generate_panels_from_db.py --write rebuilt panels from FileMaker raw names
    with no regard for Glen's corrections. It now skips any slug whose ingredients are
    pinned there, and still writes the others."""
    import scripts.generate_panels_from_db as gp
    from dashboard import formulations
    shutil.copy(os.path.join(ROOT, "data", "products.json"), tmp_path / "products.json")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    prods, corr = _data("products.json")["products"], _data("products-manual-corrections.json")
    ids = [str(p.get("fmp_id")).strip() for p in prods.values() if p.get("fmp_id")]
    other = next(s for s, p in prods.items() if p.get("fmp_id") and s not in corr
                 and ids.count(str(p["fmp_id"]).strip()) == 1)
    fmp = {SLUG: _data("products.json")["products"][SLUG]["fmp_id"],
           other: _data("products.json")["products"][other]["fmp_id"]}
    monkeypatch.setattr(formulations, "search_formulations", lambda *a, **k: forms)
    monkeypatch.setattr(formulations, "list_items_for_formulation",
                        lambda fid, **k: [{"ingredient_name": "Paeoniflorin", "dose": 159,
                                           "dose_unit": "mg"}])
    # A third product has a correction entry with no pinned ingredients: it is rebuilt.
    unpinned = next(s for s, c in corr.items() if not s.startswith("_") and isinstance(c, dict)
                    and not c.get("ingredients") and prods.get(s, {}).get("fmp_id")
                    and ids.count(str(prods[s]["fmp_id"]).strip()) == 1)
    fmp[unpinned] = prods[unpinned]["fmp_id"]
    forms = [{"id": i, "fmp_id": f, "name": s} for i, (s, f) in enumerate(fmp.items())]
    # The corrections file beside the catalogue is the one obeyed.
    shutil.copy(os.path.join(ROOT, "data", "products-manual-corrections.json"),
                tmp_path / "products-manual-corrections.json")
    assert gp.main(["--write", "--db", str(tmp_path / "none.db")]) == 0
    out = json.load(open(tmp_path / "products.json"))["products"]
    assert _rows(out[SLUG]) == NINE
    rebuilt = [{"name": "Paeoniflorin", "dose": "159 mg"}]
    assert out[other]["ingredients"] == rebuilt
    assert out[unpinned]["ingredients"] == rebuilt


def test_a_rebuild_obeys_the_corrections_beside_its_catalogue(tmp_path, monkeypatch):
    import scripts.generate_panels_from_db as gp
    (tmp_path / "products.json").write_text("{}")
    (tmp_path / "products-manual-corrections.json").write_text(
        json.dumps({"_comment": "x", "only-here": {"ingredients": [{"name": "A", "dose": "1 mg"}]}}))
    assert gp._manual_slugs(str(tmp_path / "products.json")) == {"only-here"}


def test_a_rebuild_refuses_when_no_corrections_file_can_be_read(tmp_path, monkeypatch):
    import scripts.generate_panels_from_db as gp
    (tmp_path / "products.json").write_text("{}")
    monkeypatch.setattr(gp, "_corrections_path", lambda p: str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        gp._manual_slugs(str(tmp_path / "products.json"))
