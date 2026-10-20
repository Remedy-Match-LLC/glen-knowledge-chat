"""A Terrain Restore bottle with two or more added ingredients is always $70.

Glen, 2026-10-19: "yes, always $70", then "yes, when there is more than one specially
added ingredient". Josie's order 236 billed the folded bottle at $58, because #2074
folded it into plain Terrain Restore, which takes the member volume rate. The bottle
is now its own product, terrain-restore-custom: never discounted, still counted
toward the volume rate of the other formulations, and Rae can type over its price.
"""
import pytest

app_mod = pytest.importorskip("app")

CUSTOM = "terrain-restore-custom"
# Order 236's shape: a Biofield Analysis (member pricing) and several formulations.
FFS = ["paracleanse", "nerve-repair", "neuroceramides", "microbiome", "oxygen-cleanse"]


def _lines(*extra):
    return ([{"slug": "biofield-analysis", "qty": 1}]
            + [{"slug": s, "qty": 1} for s in FFS] + list(extra))


def _priced(lines):
    out = app_mod._price_inhouse_invoice(lines, email="custom-tr-test@example.com",
                                         pickup=True, ship=None)
    assert out is not None
    return {r["slug"]: r for r in out["items_rec"]}


def test_the_catalog_entry_is_70_and_never_discounted_itself():
    p = app_mod._get_product(CUSTOM)
    assert p["price_cents"] == 7000
    assert not app_mod._qty_eligible(p)
    assert app_mod._counts_toward_volume(p)


def test_the_custom_bottle_bills_70_on_a_member_invoice():
    recs = _priced(_lines({"slug": CUSTOM, "qty": 1, "note": "With A and B"}))
    assert recs[CUSTOM]["unit_cents"] == 7000
    # The other formulations are discounted, so this is a real member invoice.
    assert recs["paracleanse"]["unit_cents"] < 7000


def test_plain_terrain_restore_keeps_its_normal_pricing():
    recs = _priced(_lines({"slug": "terrain-restore", "qty": 1}))
    assert recs["terrain-restore"]["unit_cents"] == recs["paracleanse"]["unit_cents"] < 7000


def test_the_custom_bottle_counts_toward_the_volume_rate():
    without = _priced(_lines())["paracleanse"]["unit_cents"]
    with_custom = _priced(_lines({"slug": CUSTOM, "qty": 3}))["paracleanse"]["unit_cents"]
    assert with_custom < without


def test_a_price_rae_types_still_wins():
    recs = _priced(_lines({"slug": CUSTOM, "qty": 1, "unit_cents": 6500}))
    assert recs[CUSTOM]["unit_cents"] == 6500


def test_the_price_preview_shows_70():
    client = app_mod.app.test_client()
    r = client.post("/api/orders/price-preview",
                    json={"email": "custom-tr-test@example.com",
                          "lines": _lines({"slug": CUSTOM, "qty": 2})},
                    headers={"X-Console-Key": app_mod.CONSOLE_SECRET})
    assert r.status_code == 200, r.data
    line = next(l for l in r.get_json()["lines"] if l["slug"] == CUSTOM)
    assert line["effective_unit_cents"] == 7000 and line["line_cents"] == 14000


SHIP = {"name": "T", "address1": "1 A St", "city": "Hilo", "state": "HI", "zip": "96720",
        "country": "US"}


def _cart_unit(cart, slug, **kw):
    """What one bottle is charged, read off the engine's line total (items_rec
    carries the list price, so it would pass whether or not a discount applied)."""
    out = app_mod._price_cart(cart, ship=SHIP, **kw)
    line = next(l for l in out["priced"]["lines"] if l["slug"] == slug)
    return line["line_total_cents"] // line["qty"]


def test_the_cart_engine_never_discounts_it():
    cart = [{"slug": s, "qty": 2} for s in FFS] + [{"slug": CUSTOM, "qty": 3}]
    # The same cart does discount a formulation, so these offers are live.
    assert _cart_unit(cart, "paracleanse", coupon_pct=20) < app_mod._get_product("paracleanse")["price_cents"]
    assert _cart_unit(cart, CUSTOM, program_member=True) == 7000
    assert _cart_unit(cart, CUSTOM, coupon_pct=20) == 7000
    assert _cart_unit(cart, CUSTOM, subscriber_tier_pct=15) == 7000


def test_the_cart_engine_counts_it_toward_volume(monkeypatch):
    """The order-total rate is read off the order's month count; the bottle adds to it."""
    from dashboard import pricing
    seen = []
    real = pricing.program_total_pct
    monkeypatch.setattr(pricing, "program_total_pct",
                        lambda q, s, m: seen.append(q) or real(q, s, m))
    app_mod._price_cart([{"slug": "paracleanse", "qty": 1}, {"slug": CUSTOM, "qty": 5}],
                        ship=SHIP, program_member=True)
    assert seen and seen[-1] == 6


def test_no_cohort_policy_prices_it():
    from dashboard import biofield_invoice as bi, cohorts as co
    assert bi.CUSTOM_TERRAIN_RESTORE_SLUG in co._NEVER_COHORT_PRICED
    for pol in ({"type": "percent_off", "pct": 20, "scope": "all"},
                {"type": "per_sku", "prices": {CUSTOM: 5000}}):
        assert co.policy_unit_cents(pol, slug=CUSTOM, list_cents=7000, is_ff=False) is None
    assert co.policy_unit_cents({"type": "percent_off", "pct": 20, "scope": "all"},
                                slug="terrain-restore", list_cents=7000, is_ff=True) == 5600


def test_a_published_report_gives_it_no_courtesy_price():
    import inspect
    from dashboard import biofield_portal_publish as bpp
    src = inspect.getsource(bpp)
    assert "slug != CUSTOM_TERRAIN_RESTORE_SLUG" in src


def test_a_family_pool_counts_it_toward_volume():
    members = [{"items": [{"slug": "paracleanse", "qty": 2}]},
               {"items": [{"slug": CUSTOM, "qty": 3}, {"slug": "biofield-analysis", "qty": 1}]}]
    assert app_mod._pooled_volume_qty(members) == 5
