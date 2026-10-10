"""A blank courtesy price publishes at store prices (Glen, 2026-10-09).

"It should publish to portal when I leave the special pricing blank, which will be the
case for most new clients." The price box stopped silently on a blank entry, so a
report never published. And a publish with no price stored 0 on every reorder item,
which the portal read as a $0 price: 30 portals carried 239 such items.
"""
import sqlite3

import pytest

CLIENT = "dana.hale@example.com"


@pytest.fixture
def a(monkeypatch, tmp_path):
    import app as mod
    monkeypatch.setattr(mod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "_mix_match_member", lambda e: False)
    monkeypatch.setattr(mod, "_is_paid_member", lambda e: False)
    from dashboard import client_prices as cp
    cx = sqlite3.connect(str(mod.LOG_DB))
    cp.init_table(cx)
    cx.commit()
    cx.close()
    return mod


def _formula(a):
    P = a._PRODUCTS["products"]
    return next(s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
                and not p.get("inactive") and not p.get("info_only")
                and int(p.get("price_cents") or 0) == 7000)


@pytest.mark.parametrize("stored,expected", [
    (None, None), ("", None), (0, None), ("0", None), (-100, None), ("junk", None),
    (5000, 5000), ("5000", 5000)])
def test_only_a_positive_curated_price_counts(a, stored, expected):
    item = {"slug": "x"} if stored is None else {"slug": "x", "price_cents": stored}
    assert a._curated_unit_cents(item) == expected


def test_a_stored_zero_is_charged_the_store_price(a):
    slug = _formula(a)
    _l, items, _sub = a._portal_priced_lines(
        [{"slug": slug, "qty": 1, "price_cents": 0}], email=CLIENT)
    assert items[0]["unit_cents"] == 7000


def test_a_real_courtesy_price_still_applies(a):
    slug = _formula(a)
    _l, items, _sub = a._portal_priced_lines(
        [{"slug": slug, "qty": 1, "price_cents": 5000}], email=CLIENT)
    assert items[0]["unit_cents"] == 5000


def test_the_button_publishes_a_blank_and_explains_a_bad_entry():
    from dashboard import biofield_report_html as brh
    import inspect
    src = inspect.getsource(brh)
    assert "if(!cents)return;" not in src
    assert "Leave blank for store prices." in src
    assert "if(t==='')return 0;" in src
    assert "Nothing was published." in src
