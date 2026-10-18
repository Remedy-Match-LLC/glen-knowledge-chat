"""Silymarin: the t33 panel removed, the panel pinned empty, listed as a Pure Powder.

Glen, 2026-10-18, production tab: 'Silymarin plan above is a "yes".'
(production/05 Formulations/_store-updates/2026-10-18-silymarin-pure-powder.md).

The generator tests cover the section-draft stream, the image queue's single-prompt
path (`_drain_sales_image_queue` with SALES_PAGES_IMAGE_VARIATIONS off, the only path
that refills from the card) and `regenerate_copy`. The entry has no `ingredients` key,
so "an empty list" is the key left absent: the prompt builders read it as `or []`.
"""
import json
import pathlib
import re
from datetime import datetime, timezone

from dashboard.pure_powders import PURE_POWDER_NAMES

REPO = pathlib.Path(__file__).resolve().parent.parent
KEY = "silymarin"
NAME = "Silymarin"
T33 = [{"name": "Silymarin", "dose": "100 mg"}, {"name": "Magnesium Ascorbate", "dose": "458 mg"},
       {"name": "Luteolin", "dose": "50 mg"}, {"name": "Biotin", "dose": "1 mg"}]


def _product():
    catalog = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))
    return dict(catalog["products"][KEY], slug=KEY)


def test_the_entry_drops_the_t33_panel_and_pins_it_empty():
    p = _product()
    assert "ingredients" not in p and "ingredients_source" not in p
    assert p["copy_pinned"] == ["ingredients"]
    assert (p["name"], p["price_cents"]) == (NAME, 4000)


def test_content_since_is_a_past_utc_timestamp():
    # Fails while the placeholder is in place: it is set just before merge.
    since = _product()["content_since"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", since), since
    assert since <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_matcher_never_recommends_it():
    import app as a
    assert a._ff_auto_excluded(NAME) and a._ff_auto_excluded(NAME.lower())


def test_the_matcher_exclusion_rests_on_the_name_list(monkeypatch):
    import app as a
    from dashboard import pure_powders
    monkeypatch.setattr(pure_powders, "PURE_POWDER_NAMES", PURE_POWDER_NAMES - {"silymarin"})
    monkeypatch.setattr(pure_powders, "PURE_POWDER_SLUGS",
                        pure_powders.PURE_POWDER_SLUGS - {KEY})
    assert not a._ff_auto_excluded(NAME)


def test_a_formula_that_contains_silymarin_is_still_matched():
    import app as a
    assert not a._ff_auto_excluded("Glutathione Syntropy")
    assert not a._ff_auto_excluded("Silymarin Liver Formula")


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


def test_the_chat_is_told_not_to_volunteer_it_but_keeps_the_formulas():
    src = (REPO / "app.py").read_text(encoding="utf-8")
    sentence = src[src.index("The pure powders ("):]
    sentence = sentence[:sentence.index("do not volunteer or recommend them")]
    assert NAME in sentence
    assert "A formula that contains MSM, quercetin or silymarin is unaffected." in src


# ── The pin holds in the three generators ─────────────────────────────────────

def test_the_draft_stream_gets_an_empty_list(monkeypatch, tmp_path):
    import app as a
    from dashboard import sales_copy as sc
    seen = []

    def fake_prompt(section, prod):
        seen.append(prod.get("ingredients"))
        return "s", "u"

    class _Stream:
        def __enter__(self): return self
        def __exit__(self, *x): return False
        text_stream = iter(["Copy."])

    class _Cl:
        class messages:
            @staticmethod
            def stream(**kw): return _Stream()

    monkeypatch.setattr(a, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(a, "_SALES_AI_COPY_ENABLED", True)
    monkeypatch.setattr(a, "_product_card", lambda p: {"ingredients": T33})
    monkeypatch.setattr(a, "_cl", _Cl())
    monkeypatch.setattr(sc, "build_section_prompt", fake_prompt)
    body = a.app.test_client().get(f"/begin/product-page-gen/{KEY}/intro").get_data(as_text=True)
    assert '"done": true' in body, body
    assert [x or [] for x in seen] == [[]], seen


def test_the_image_prompt_gets_an_empty_list(monkeypatch, tmp_path):
    import sqlite3
    import app as a
    from dashboard import sales_image_prompts as sip, sales_images as si
    seen = []

    def fake_prompts(prod):
        seen.append(prod.get("ingredients"))
        return {kind: [] for kind in sip.IMAGE_KINDS}

    monkeypatch.setattr(a, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(a, "_SALES_IMG_DIR", tmp_path / "sales-images")
    monkeypatch.setattr(a, "_SALES_AI_IMAGES_ENABLED", True)
    monkeypatch.setattr(a, "_SALES_IMAGE_VARIATIONS_ENABLED", False)
    monkeypatch.setattr(a, "_product_card", lambda p: {"ingredients": T33})
    monkeypatch.setattr(sip, "build_image_prompts", fake_prompts)
    with sqlite3.connect(a.LOG_DB) as cx:
        si.enqueue(cx, KEY)
    a._drain_sales_image_queue()
    assert [x or [] for x in seen] == [[]], seen


def test_regenerate_copy_gets_an_empty_list(monkeypatch):
    from dashboard import sales_pages_actions as spa, sales_copy as sc
    seen = []

    def fake_prompt(section, prod):
        seen.append(prod.get("ingredients"))
        return "s", "u"

    class _Blk:
        type, text = "text", "Copy."

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [_Blk()]})()

    monkeypatch.setattr(sc, "build_section_prompt", fake_prompt)
    monkeypatch.setattr(spa, "_DEPS", {})
    spa.configure(client=_Client(), get_product=lambda s: _product(),
                  product_card=lambda p: {"ingredients": T33})
    assert spa.regenerate_copy(KEY)
    assert seen and all((x or []) == [] for x in seen), seen
