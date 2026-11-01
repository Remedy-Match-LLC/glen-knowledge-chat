"""Iodine caution line on the products that carry potassium iodide or kelp iodine (Glen,
2026-10-31, production tab: "yes"). Spec: production/05 Formulations/_store-updates/
2026-10-31-iodine-caution-and-plan-check.md, Part 1.

Thermogenesis gets the line after its vinpocetine line. Estro-Clear and Vitamin D Syntropy
already named thyroid, so marketing merged the line into their cautions and Glen said yes in
the marketing tab on 2026-10-31 (marketing/04 Copy/iodine-caution-merge-2026-10-31/).
GastroZyme was held until Glen read a shelf bottle. Its line now ships with its label rows.
Thyroid Support was held while sales asked Glen about its panel. Its caution v7 (store
page 117, Glen 2026-10-31) carries the 150 mcg sentence with a thyroid tail of its own;
tests/test_thyroid_support_v7_2026_10_31.py pins the whole text. The Estrogen and
Fibrin Balance Program's intro repeats Estro-Clear's caution, so it carries the same lines
(Glen "yes", marketing tab, 2026-10-31); tests/test_estrogen_fibrin_balance_program.py pins it.
"""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAIL = (" If you have a thyroid condition or take other iodine products, consult your health "
        "professional before use.")
L150 = "Each capsule contains about 150 mcg of iodine, the adult daily requirement." + TAIL
VIN = ("This formula contains vinpocetine, so do not use it if you are pregnant or could "
       "become pregnant.")
WARNING = {
    "thermogenesis": VIN + " " + L150,
    "vitamin-d-syntropy": (
        "If you take a blood thinner such as warfarin, consult your physician before use, "
        "because vitamin K affects it. Consult your physician before use if you are pregnant. "
        + L150 + " Consult your physician first if you have high blood calcium, kidney stones "
        "or kidney disease, or take thiazide diuretics or digoxin. It is not for children, or "
        "for use while breastfeeding."),
    "estro-clear": (
        "Contains piperine, which can raise blood levels of some medications. Consult your "
        "health professional before use with medications, hormone-sensitive conditions, "
        "pregnancy, or nursing. Each capsule contains about 150 mcg of iodine, the adult daily "
        "requirement. At 2 capsules daily this is about 300 mcg." + TAIL),
}
# GastroZyme's line came with its label rows (tests/test_gastrozyme_label_rows_2026_10_31.py).
# Thyroid Support's v7 caution keeps the 150 mcg sentence. Its tail says "any other thyroid
# condition", because v7 already rules out an overactive thyroid.
THYROID_LINE = ("Each capsule contains about 150 mcg of iodine, the adult daily requirement. "
                "If you have any other thyroid condition or take other iodine products, consult "
                "your health professional before use.")


def _load(name):
    d = json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))
    return d.get("products", d)


@pytest.mark.parametrize("slug", sorted(WARNING))
def test_the_caution_carries_the_iodine_line(slug):
    assert _load("products.json")[slug]["warning"] == WARNING[slug]
    # All three have a corrections entry, which a rebuild replays (review round 2).
    assert _load("products-manual-corrections.json")[slug]["warning"] == WARNING[slug]


def test_thyroid_support_carries_the_v7_iodine_line():
    p, c = _load("products.json"), _load("products-manual-corrections.json")
    for d in (p, c):
        assert THYROID_LINE in d["thyroid-support"]["warning"]
        assert d["thyroid-support"]["chat_caution"] is True


def test_a_rebuild_keeps_the_lines(tmp_path, monkeypatch):
    import shutil
    from scripts import apply_enrichment as ae
    for name, attr in (("products.json", "PRODUCTS"), ("products-enrich-clean.json", "CLEAN"),
                       ("products-manual-corrections.json", "CORRECTIONS")):
        shutil.copy(os.path.join(ROOT, "data", name), tmp_path / name)
        monkeypatch.setattr(ae, attr, str(tmp_path / name))
    ae.main()
    rebuilt = json.load(open(tmp_path / "products.json"))["products"]
    for slug, w in WARNING.items():
        assert rebuilt[slug]["warning"] == w, slug
    assert THYROID_LINE in rebuilt["thyroid-support"]["warning"]


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD MARKER", "ingredients": [],
                                   "benefits": ["AI BENEFIT MARKER"]})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    return a


@pytest.mark.parametrize("slug", sorted(WARNING))
def test_the_page_shows_the_line(appmod, slug):
    d = appmod.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["caution"]["body"] == WARNING[slug]
