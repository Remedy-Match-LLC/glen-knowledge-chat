"""Five eye products get Glen-approved page copy in place of disease claims.

Glen typed "approve" in the communication tab on 2026-10-30 to
communication/drafts/2026-10-30-product-page-copy/copy.md, after three blind reviews and
production's label check, and said yes to the write in the platform tab. Intro, description,
research (how_it_works, also the learn page) and benefits are pinned, as AngiogenX's are.
The same approval corrects two ingredient rows from the printed labels and adds Lipid Zyme's
directions (production tab).
"""
import io
import json
import os
import re
import shutil
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUGS = ["macular-wellness-lycopene", "lipid-zyme", "lipid-cleanse", "scar-solve", "scar-silk"]
FIELDS = ("intro", "description", "how_it_works", "learn_markdown", "benefits")
# The old copy's disease claims and scraped junk ("Scar Solve . Price: $70.").
# sha256 of the four approved fields as parsed from copy.md on 2026-10-30, the first 16 hex
# digits; Scar Solve's dose and zinc lines were added the same day (Glen "approve").
# Review round 2: comparing the two data files with each other cannot catch the
# same edit made to both.
APPROVED_SHA = {
    "macular-wellness-lycopene": "7ee97d06b0d8c288",
    "lipid-zyme": "b04d956c9fa7b923",
    "lipid-cleanse": "a5b16159edfde43f",
    "scar-solve": "96ee2e19d8db4578",
    "scar-silk": "97e0eb0b1c18be7e"
}
BANNED = re.compile(r"drusen|dissolv|revers|blindness|\bAMD\b|Price:|\s\.\s", re.IGNORECASE)


def _data(name):
    doc = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return doc.get("products", doc)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_copy_is_pinned_in_both_files(slug):
    p, c = _data("products.json")[slug], _data("products-manual-corrections.json")[slug]
    for k in FIELDS:
        assert p[k] and p[k] == c[k], k
    assert p["learn_markdown"] == p["how_it_works"]
    assert isinstance(p["benefits"], list) and all(isinstance(b, str) for b in p["benefits"])
    for k in ("intro", "description", "research", "benefits"):
        assert k in p["copy_pinned"] and k in c["copy_pinned"], k
    # Scar Silk keeps its directions pin.
    if slug == "scar-silk":
        assert "directions" in p["copy_pinned"]


@pytest.mark.parametrize("slug", SLUGS)
def test_the_copy_is_the_approved_text(slug):
    import hashlib
    p = _data("products.json")[slug]
    four = {k: p[k] for k in ("intro", "description", "how_it_works", "benefits")}
    digest = hashlib.sha256(json.dumps(four, sort_keys=True, ensure_ascii=False).encode())
    assert digest.hexdigest()[:16] == APPROVED_SHA[slug]


@pytest.mark.parametrize("slug", SLUGS)
def test_no_disease_claim_or_junk_remains(slug):
    p = _data("products.json")[slug]
    for k in FIELDS:
        assert not BANNED.search(json.dumps(p[k], ensure_ascii=False)), (k, BANNED.search(
            json.dumps(p[k], ensure_ascii=False)))
    assert "Sources:" not in p["how_it_works"]


def test_scar_solve_gives_its_dose_and_the_zinc_limits():
    """Glen, production tab: Scar Solve stays at 1 to 2 a day, with the zinc arithmetic."""
    for name in ("products.json", "products-manual-corrections.json"):
        paras = _data(name)["scar-solve"]["description"].split("\n\n")
        assert "Take 1 to 2 vegicaps a day with food." in paras
        assert "Take 1 vegicap a day with food." not in paras
        assert "Two vegicaps taken with an eye vitamin that contains zinc go over that limit." in paras
        assert "Each vegicap supplies 100 mg zinc ascorbate." in paras


def test_the_label_corrections():
    for name in ("products.json", "products-manual-corrections.json"):
        d = _data(name)
        mw = d["macular-wellness-lycopene"]["ingredients"]
        assert [i["dose"] for i in mw if i["name"] == "Lycopene"] == ["30 mg"]
        lc = d["lipid-cleanse"]["ingredients"]
        assert [i["dose"] for i in lc if i["name"] == "MSC"] == ["0.05 mg"]
        assert d["lipid-zyme"]["directions"] == (
            "1 capsule 1 to 3 times daily between meals, or as guided.")


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card", lambda p: {"description": "AI CARD MARKER",
                                                       "ingredients": [],
                                                       "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


@pytest.mark.parametrize("slug", SLUGS)
def test_the_page_and_learn_page_show_only_the_approved_copy(appmod, slug):
    p = _data("products.json")[slug]
    c = appmod.app.test_client()
    d = c.get(f"/begin/product-page-data/{slug}").get_json()
    dump = json.dumps(d, ensure_ascii=False)
    assert "AI CARD MARKER" not in dump and "AI HOW MARKER" not in dump
    assert "AI BENEFIT MARKER" not in dump
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == p["intro"]
    assert secs["research"]["body"]["how_it_works"] == p["how_it_works"]
    assert secs["description"]["body"] == p["description"]
    # Benefits come from the product-data route, pinned (review round 2).
    assert c.get(f"/begin/product-data/{slug}").get_json()["benefits"] == p["benefits"]
    assert not BANNED.search(dump.replace(" . ", "."))
    learn = c.get(f"/begin/learn/{slug}")
    assert learn.status_code == 200
    page = learn.get_data(as_text=True)
    # Review round 2: the learn page must carry the approved text, not only lack the claims.
    # The page shell loads its text from learn-data.
    assert c.get(f"/begin/learn-data/{slug}").get_json()["markdown"] == p["how_it_works"]
    assert not re.search(r"drusen|dissolv|blindness|\bAMD\b", page, re.IGNORECASE)


def test_a_rebuild_keeps_the_copy_and_the_corrections(tmp_path, monkeypatch):
    import scripts.apply_enrichment as ae
    names = ("products-enrich-clean.json", "products.json", "products-manual-corrections.json")
    for f in names:
        shutil.copy(os.path.join(ROOT, "data", f), tmp_path / f)
    monkeypatch.setattr(ae, "CLEAN", str(tmp_path / names[0]))
    monkeypatch.setattr(ae, "PRODUCTS", str(tmp_path / names[1]))
    monkeypatch.setattr(ae, "CORRECTIONS", str(tmp_path / names[2]))
    want = _data("products.json")
    cat = json.load(open(tmp_path / names[1]))
    for s in SLUGS:
        for k in FIELDS + ("copy_pinned", "ingredients", "directions"):
            cat["products"][s].pop(k, None)
    json.dump(cat, open(tmp_path / names[1], "w"))
    for _ in range(2):
        with redirect_stdout(io.StringIO()):
            ae.main()
        out = json.load(open(tmp_path / names[1]))["products"]
        for s in SLUGS:
            for k in FIELDS + ("copy_pinned", "warning"):
                assert out[s][k] == want[s][k], (s, k)
        for s in ("macular-wellness-lycopene", "lipid-cleanse"):
            assert out[s]["ingredients"] == want[s]["ingredients"], s
        assert out["lipid-zyme"]["directions"] == want["lipid-zyme"]["directions"]
