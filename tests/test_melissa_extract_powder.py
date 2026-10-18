"""Melissa 20:1 Extract Powder, listed as a Pure Powder at $40.

Glen, 2026-10-18, production tab: "yes" to $40, then "send" for the whole spec
(production/05 Formulations/_store-updates/2026-10-18-melissa-extract-powder.md).
"""
import importlib
import json
import pathlib

import pytest

from dashboard.pure_powders import PURE_POWDER_NAMES

REPO = pathlib.Path(__file__).resolve().parent.parent
KEY = "melissa-20-1-extract-powder"
NAME = "Melissa 20:1 Extract Powder"
INTRO = ("Melissa 20:1 Extract Powder is an extract of Melissa officinalis, the herb "
         "commonly called lemon balm.")
DESCRIPTION = ("Lemon balm is a member of the mint family.\n\n"
               "Each scoop holds 500 mg of Melissa 20:1 extract.")


def _product():
    catalog = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))
    return dict(catalog["products"][KEY], slug=KEY)


def test_the_key_is_what_the_store_makes_from_the_name():
    import app as a
    assert a._slugify_product(NAME) == KEY


def test_the_entry_carries_the_approved_fields_and_no_prose():
    p = _product()
    assert (p["name"], p["price_cents"], p["fmp_id"], p["bottle_type"]) == (
        NAME, 4000, "1194", "30 g")
    assert p["ingredients"] == [{"name": "Melissa (Melissa officinalis) 20:1 extract",
                                 "dose": "500 mg"}]
    assert p["directions"] == "Take 1 scoop 2 times daily in a drink, or as guided."
    assert p["copy_pinned"] == ["ingredients", "intro", "description", "research",
                                "directions", "benefits"]
    # The intro and description are rnd's copy, which Glen approved in the production
    # tab on 2026-10-18 ("send without the last line").
    assert p["intro"] == INTRO and p["description"] == DESCRIPTION
    for left_out in ("how_it_works", "regular_cents", "qbo_item_id"):
        assert left_out not in p, left_out


def test_the_matcher_never_recommends_it():
    import app as a
    assert a._ff_auto_excluded(NAME) and a._ff_auto_excluded(NAME.lower())


def test_the_matcher_exclusion_rests_on_the_name_list(monkeypatch):
    import app as a
    from dashboard import pure_powders
    monkeypatch.setattr(pure_powders, "PURE_POWDER_NAMES",
                        PURE_POWDER_NAMES - {"melissa 20:1 extract powder"})
    monkeypatch.setattr(pure_powders, "PURE_POWDER_SLUGS",
                        pure_powders.PURE_POWDER_SLUGS - {KEY})
    assert not a._ff_auto_excluded(NAME)


def test_checkout_gives_no_volume_curve_and_the_pure_powder_floor():
    import app as a
    item = a._engine_item(_product(), 3)
    assert item["volume_eligible"] is False
    assert item["product"]["sku_discount_floor_pct"] == 0.75
    assert item["product"]["sku_points_floor_pct"] == 0.75
    bare = {k: v for k, v in _product().items()
            if k not in ("volume_eligible", "sku_discount_floor_pct", "sku_points_floor_pct")}
    without = a._engine_item(bare, 3)
    # Without the keys the name is not read as a pure powder: the formula curve applies.
    assert without["volume_eligible"] is True
    assert "sku_discount_floor_pct" not in without["product"]


def test_the_chat_can_link_it_but_is_told_not_to_volunteer_it():
    aliases = json.loads((REPO / "data" / "product-aliases.json").read_text(encoding="utf-8"))
    assert aliases["aliases"][NAME]["url"] == \
        "https://myhealingoasis.com/begin/product/melissa-20-1-extract-powder"
    src = (REPO / "app.py").read_text(encoding="utf-8")
    sentence = src[src.index("The pure powders ("):]
    sentence = sentence[:sentence.index("do not volunteer or recommend them")]
    assert NAME in sentence


@pytest.fixture
def appmod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as a
    importlib.reload(a)
    a.app.config["TESTING"] = True
    monkeypatch.setattr(a, "_product_card",
                        lambda p: {"description": "AI CARD MARKER supports the nervous system",
                                   "ingredients": [], "benefits": []})
    monkeypatch.setattr(a, "_product_how", lambda p: "AI HOW MARKER")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    return a


def test_the_page_shows_the_approved_copy_and_never_the_card(appmod):
    """Production's after-deploy check found the card's text in both sections."""
    c = appmod.app.test_client()
    d = c.get(f"/begin/product-page-data/{KEY}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["intro"]["body"] == INTRO
    assert secs["description"]["body"] == DESCRIPTION
    for url in (f"/begin/product-page-data/{KEY}", f"/begin/product-data/{KEY}"):
        dump = json.dumps(c.get(url).get_json())
        for phrase in ("AI CARD MARKER", "nervous system", "cognitive"):
            assert phrase not in dump, (url, phrase)
    # "This formula" also heads the shared comparison table on every page, so only
    # the copy sections are checked for it.
    copy = secs["intro"]["body"] + secs["description"]["body"]
    for phrase in ("This formula", "leaf"):
        assert phrase not in copy, phrase
