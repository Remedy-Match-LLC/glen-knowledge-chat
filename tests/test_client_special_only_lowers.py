"""A client special price only ever lowers a price (Glen, 2026-10-04).

"A client special price should only override pricing to give a lower price, not a
higher one." Agnes Verches, a paid member, had a $69.97 flat Formula price saved. It
outranked her member mix/match rate, so 17 Formulas on her invoice stayed at $69.97.
"""
import sqlite3
from ast import walk as ast_walk

import pytest

AGNES = "agnes@example.com"
OTHER = "someone@example.com"


@pytest.fixture
def a(monkeypatch, tmp_path):
    import app as mod
    monkeypatch.setattr(mod, "LOG_DB", tmp_path / "chat_log.db")
    monkeypatch.setattr(mod, "_mix_match_member", lambda e: e == AGNES)
    monkeypatch.setattr(mod, "_is_paid_member", lambda e: e == AGNES)
    from dashboard import client_prices as cp
    cx = sqlite3.connect(str(mod.LOG_DB))
    cp.init_table(cx)
    cx.commit()
    cx.close()
    return mod


def _save(a, *, flat=None, sku=None, email=AGNES):
    from dashboard import client_prices as cp
    cx = sqlite3.connect(str(a.LOG_DB))
    if flat is not None:
        cp.set_ff_flat(cx, email, flat)
    for slug, cents in (sku or {}).items():
        cp.set_price(cx, email, slug, cents)
    cx.commit()
    cx.close()


def _ffs(a, n=3):
    P = a._PRODUCTS["products"]
    out = [s for s, p in P.items() if isinstance(p, dict) and p.get("qty_pricing")
           and not p.get("inactive") and not p.get("info_only")
           and int(p.get("price_cents") or 0) == 6997][:n]
    assert len(out) == n
    return out


def _units(a, email, lines):
    ship = {"name": "A", "street": "1 A St", "address2": "", "city": "X", "state": "MA",
            "zip": "01950", "country": "US"}
    priced = a._price_inhouse_invoice(lines, email=email, pickup=True, ship=ship)
    return {r["slug"]: r["unit_cents"] for r in priced["items_rec"] if r.get("slug")}


def test_the_rule_itself(a):
    assert a._special_lowers_only(6997, 5800) == 5800
    assert a._special_lowers_only(5000, 5800) == 5000
    assert a._special_lowers_only(None, 5800) == 5800
    assert a._special_lowers_only("junk", 5800) == 5800
    assert a._special_lowers_only(0, 30000) == 0          # a $0 courtesy still applies


def test_a_flat_price_above_mix_and_match_no_longer_raises_the_invoice(a):
    ffs = _ffs(a)
    lines = [{"slug": s, "qty": 1} for s in ffs]
    member_rate = _units(a, AGNES, lines)
    assert all(v < 6997 for v in member_rate.values()), member_rate
    _save(a, flat=6997)
    assert _units(a, AGNES, lines) == member_rate


def test_a_flat_price_below_the_rate_still_applies(a):
    ffs = _ffs(a)
    _save(a, flat=5000)
    assert set(_units(a, AGNES, [{"slug": s, "qty": 1} for s in ffs]).values()) == {5000}


def test_a_per_sku_special_above_list_is_ignored(a):
    slug = _ffs(a, 1)[0]
    _save(a, sku={slug: 9000}, email=OTHER)
    assert _units(a, OTHER, [{"slug": slug, "qty": 1}])[slug] == 6997


def test_a_zero_courtesy_on_the_biofield_still_applies(a):
    _save(a, sku={"biofield-analysis": 0})
    assert _units(a, AGNES, [{"slug": "biofield-analysis", "qty": 1}])["biofield-analysis"] == 0


def test_an_explicit_operator_price_is_untouched(a):
    """An operator typing a price on a line is a different thing from a saved special,
    and keeps its existing behaviour."""
    slug = _ffs(a, 1)[0]
    assert _units(a, OTHER, [{"slug": slug, "qty": 1, "unit_cents": 7500}])[slug] == 7500


def test_the_ff_add_to_invoice_price_follows_the_rule(a):
    slug = _ffs(a, 1)[0]
    _save(a, flat=9000, email=OTHER)
    cx = sqlite3.connect(str(a.LOG_DB))
    cx.row_factory = sqlite3.Row
    assert a._ff_line_cents(cx, OTHER, slug) == 6997
    cx.close()


def test_no_site_lets_a_special_replace_the_price_outright():
    """Every place that reads a client's special goes through the one rule. A new
    branch that assigns the special straight to the price would bring the bug back."""
    import inspect
    import re
    import app as mod
    src = inspect.getsource(mod)
    bad = re.findall(r"(?:your_cents|override|special|unit_cents|analysis|courtesy)\s*=\s*"
                     r"(?:int\()?\s*(?:client_by_slug\[|client_ff_flat|_cp_by_slug\[|_cp_ff_flat"
                     r"|_cprices\.get\(|_ff_flat\b)", src)
    assert bad == [], bad
    # Round 1: every function that READS a client's saved price must also apply the
    # rule, so a new reader cannot slip past by naming its variable differently.
    tree = __import__("ast").parse(src)
    readers = ("get_price", "price_map", "get_ff_flat", "list_for")
    # These list the saved specials themselves for the owner; they price nothing.
    allowed = {"api_console_client_prices", "console_client_commerce_status"}
    missing = []
    for fn in [n for n in ast_walk(tree) if type(n).__name__ == "FunctionDef"]:
        body = __import__("ast").get_source_segment(src, fn) or ""
        if any(f".{r}(" in body for r in readers) and "_special_lowers_only" not in body \
                and "_client_special_for" not in body and fn.name not in allowed:
            missing.append(fn.name)
    assert missing == [], missing


def test_portal_a_saved_special_above_the_automatic_price_is_ignored(a):
    slug = _ffs(a, 1)[0]
    _save(a, sku={slug: 9000}, email=OTHER)
    _l, items, _sub = a._portal_priced_lines([{"slug": slug, "qty": 1}], email=OTHER)
    assert items[0]["unit_cents"] == 6997


def test_portal_a_saved_special_still_outranks_an_older_baked_price(a):
    """The earlier ruling stands: a current saved special beats an older baked line
    price, here a lower one. It just never rises above the automatic price."""
    slug = _ffs(a, 1)[0]
    _save(a, sku={slug: 4200}, email=OTHER)
    _l, items, _sub = a._portal_priced_lines([{"slug": slug, "qty": 1, "price_cents": 3900}],
                                             email=OTHER)
    assert items[0]["unit_cents"] == 4200


def test_the_lower_of_a_per_sku_special_and_the_flat_rate_wins(a):
    """Glen, 2026-10-04, "yes": a $60 per-SKU special no longer hides a $40 flat rate."""
    slug = _ffs(a, 1)[0]
    _save(a, flat=4000, sku={slug: 6000}, email=OTHER)
    assert _units(a, OTHER, [{"slug": slug, "qty": 1}])[slug] == 4000
    assert a._client_special_for(slug, a._get_product(slug), {slug: 6000}, 4000) == 4000
    assert a._client_special_for(slug, a._get_product(slug), {slug: 3000}, 4000) == 3000


def test_the_flat_rate_never_applies_to_a_non_formula(a):
    assert a._client_special_for("biofield-analysis", a._get_product("biofield-analysis"),
                                 {}, 4000) is None
