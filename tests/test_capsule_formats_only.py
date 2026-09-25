"""Capsule packaging formats appear only for 30-capsule products.

Clinical and production, 2026-09-24: every volume-priced product (droppers, powders,
sprays, oils) was offered "Standard bottles: 30 capsules per bottle", "Larger bottle"
and "Cellophane refill packs". A non-default choice booked a line for a product that
does not exist ("OcuHeal+ Eye Drops (Larger bottle)"), and the portal's cellophane
default did it without the client choosing. The cause: `_qty_eligible` (volume
pricing) stood in for "is capsules". bottle_type names the jar, not the contents, so
only "30 Caps" counts; a "120 caps" jar can hold a powder.
"""
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def a(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    return appmod


CAPS = {"slug": "caps", "name": "Caps", "price_cents": 6997, "bottle_type": "30 Caps", "qty_pricing": True}
DROPS = {"slug": "drops", "name": "Drops", "price_cents": 6997, "bottle_type": "Dropper 5 mL", "qty_pricing": True}


@pytest.mark.parametrize("p, want", [
    (CAPS, True),
    ({**CAPS, "bottle_type": "30 caps"}, True),
    (DROPS, False),
    ({**CAPS, "bottle_type": "30 g"}, False),
    ({**CAPS, "bottle_type": "120 caps"}, False),     # MSM powder ships in this jar
    ({**CAPS, "bottle_type": ""}, False),
    ({k: v for k, v in CAPS.items() if k != "bottle_type"}, False),
    ({**CAPS, "qty_pricing": False}, False),
    ({**CAPS, "info_only": True}, False),
], ids=["30-caps", "30-caps-lower", "dropper", "powder", "120-caps-jar", "blank", "none",
        "no-volume-pricing", "info-only"])
def test_only_a_30_capsule_bottle_gets_formats(a, p, want):
    assert a._capsule_formats_ok(p) is want


@pytest.mark.parametrize("fmt, want_caps, want_drops", [
    ("larger", "larger", ""), ("refill", "refill", ""), ("bottle", "bottle", "bottle"),
    ("", "", ""), ("bogus", "", ""), (" Refill ", "refill", ""),
])
def test_clean_format(a, fmt, want_caps, want_drops):
    assert a._clean_format(CAPS, fmt) == want_caps
    assert a._clean_format(DROPS, fmt) == want_drops


def test_the_public_product_data_offers_formats_to_capsules_only(a):
    c = a.app.test_client()
    ocu = c.get("/begin/product-data/ocuheal-plus-eye-drops").get_json()
    assert ocu.get("formats") is None, "an eye drop must not offer capsule bottles"
    caps = c.get("/begin/product-data/brain-boost").get_json()   # a real 30 Caps FF
    assert [f["id"] for f in caps["formats"]] == ["bottle", "larger", "refill"]


def test_a_larger_bottle_line_on_a_dropper_books_the_real_product(a, monkeypatch):
    monkeypatch.setattr(a, "_get_product", lambda s: {"drops": DROPS, "caps": CAPS}.get(s))
    ship = {"country": "US", "zip": "01950", "state": "MA", "city": "X", "street": "1 A St"}
    out = a._price_cart([{"slug": "drops", "qty": 3, "format": "larger"}], ship=ship)
    assert [l["description"] for l in out["qbo_lines"]] == ["Drops"]
    out = a._price_cart([{"slug": "caps", "qty": 3, "format": "larger"}], ship=ship)
    assert [l["description"] for l in out["qbo_lines"]] == ["Caps (Larger bottle)"]


def test_the_portal_never_books_refill_packs_for_a_dropper(a, monkeypatch):
    monkeypatch.setattr(a, "_get_product", lambda s: {"drops": DROPS, "caps": CAPS}.get(s))
    _lines, recs, _sub = a._portal_priced_lines([{"slug": "drops", "qty": 1, "format": "refill"}])
    assert [r.get("format", "") for r in recs] == [""]
    _lines, recs, _sub = a._portal_priced_lines([{"slug": "caps", "qty": 1, "format": "refill"}])
    assert [r.get("format", "") for r in recs] == ["refill"]


def _code(src):
    return re.sub(r"(?m)^\s*#.*$", "", src)


def test_every_refill_eligible_flag_uses_the_capsule_check():
    """Round-0 review (production): seven payloads still read _qty_eligible, so the
    client portal kept offering "Refill packs" on droppers."""
    code = _code((ROOT / "app.py").read_text())
    flags = re.findall(r'"refill_eligible":\s*bool\(([^)]*\))\)', code)
    assert len(flags) >= 7, flags
    assert all("_capsule_formats_ok(" in f and "_qty_eligible(" not in f for f in flags), flags


def test_the_buy_page_hides_the_format_heading_when_there_are_none():
    js = (ROOT / "static" / "begin-buy.html").read_text()
    assert re.search(r"getElementById\('qp-format'\)\.style\.display\s*=\s*\(p\.formats\s*&&\s*p\.formats\.length\)\s*\?\s*''\s*:\s*'none'", js)


# ── review round 1: the writers and displays that still stored refill ────────

def test_the_inhouse_order_never_stores_refill_on_a_dropper(a, monkeypatch):
    monkeypatch.setattr(a, "_get_product", lambda s: {"drops": DROPS, "caps": CAPS}.get(s))
    ship = {"country": "US", "zip": "01950", "state": "MA", "city": "X", "street": "1 A St"}
    out = a._price_inhouse_invoice([{"slug": "drops", "qty": 3, "format": "refill"}],
                                   email="c@x.com", pickup=False, ship=ship)
    assert [r.get("format", "") for r in out["items_rec"]] == [""]
    out = a._price_inhouse_invoice([{"slug": "caps", "qty": 3, "format": "refill"}],
                                   email="c@x.com", pickup=False, ship=ship)
    assert [r.get("format") for r in out["items_rec"]] == ["refill"]


def test_the_invoice_rebuild_turns_a_dropper_refill_into_a_bottle():
    from dashboard import client_invoice_lines as cil
    ok = {"caps": True, "drops": False}
    got = cil.rebuild([{"slug": "drops", "qty": 1, "format": "refill"},
                       {"slug": "caps", "qty": 1, "format": "refill"}], {},
                      known=lambda s: True, refill_ok=lambda s: ok[s])
    assert [(r["slug"], r["format"]) for r in got] == [("drops", "bottle"), ("caps", "refill")]


def test_the_invoice_line_view_tells_the_page_where_refill_applies(a, monkeypatch):
    monkeypatch.setattr(a, "_get_product", lambda s: {"drops": DROPS, "caps": CAPS}.get(s))
    d = a._invoice_line_view({"slug": "drops", "name": "Drops", "qty": 1, "unit_cents": 1,
                              "line_cents": 1, "format": "refill"})
    c = a._invoice_line_view({"slug": "caps", "name": "Caps", "qty": 1, "unit_cents": 1,
                              "line_cents": 1, "format": "refill"})
    assert (d["format"], d["refill_eligible"]) == ("bottle", False)
    assert (c["format"], c["refill_eligible"]) == ("refill", True)


def test_the_override_table_decides_before_the_catalog(a, monkeypatch):
    """Review round 1: packing honours product_bottle_types; so must formats."""
    monkeypatch.setattr(a._shipping, "resolve_bottle_type",
                        lambda slug, p, db_path=None: {"caps": "Dropper 5 mL", "drops": "30 Caps"}[slug])
    assert a._capsule_formats_ok(CAPS) is False
    assert a._capsule_formats_ok(DROPS) is True


def test_the_portal_quantity_split_refuses_a_dropper(a, monkeypatch):
    monkeypatch.setattr(a, "_get_product", lambda s: {"drops": DROPS}.get(s))
    r = a.app.test_client().post("/api/portal/any-token/cart/set-format-quantities",
                                 json={"slug": "drops", "bottle_qty": 1, "refill_qty": 1})
    assert r.status_code == 400 and "bottle only" in r.get_json()["error"]


def test_both_pages_offer_refill_only_where_the_server_allows():
    inv = (ROOT / "static" / "invoice.html").read_text()
    assert re.search(r"const packaging = \([^;]*ORDER\.editable && l\.refill_eligible\)", inv)
    order = (ROOT / "static" / "order-new.html").read_text()
    assert re.search(r"\$\{refillOk\(l\.slug\) \? `<select onchange=\"editLine\(\$\{i\},'format'", order)
    assert "return !!(p && p.refill_eligible);" in order


def test_the_order_forms_product_list_carries_the_refill_flag(a, monkeypatch):
    import dashboard
    monkeypatch.setattr(a, "CONSOLE_SECRET", "test-secret")
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "test-secret")
    r = a.app.test_client().get("/api/products?all=1", headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200
    by = {p["slug"]: p for p in r.get_json()["products"]}
    assert by["brain-boost"]["refill_eligible"] is True
    assert by["ocuheal-plus-eye-drops"]["refill_eligible"] is False
