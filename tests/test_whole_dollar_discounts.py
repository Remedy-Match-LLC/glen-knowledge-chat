"""Every discounted bottle price is a whole dollar, rounded UP.

Glen 2026-10-02: "change code to exact dollar amounts. We're getting away from uneven
dollar pricing." The rule is the store-wide one: cents round up to the next dollar.

It applies to every percentage discount (same-SKU quantity, mixed-bottle, autoship,
coupon, repertoire reorder) and to the floor a discount clamps to. It does NOT apply to:
  - an undiscounted list price, which is whatever the catalog says;
  - points, which are the customer's own credit and subtract to the cent.
Rounding up never lifts a price above its list.
"""
import pytest

from dashboard import pricing


S = pricing.load_settings({})
FF = {"slug": "ff", "price_cents": 7000, "qty_pricing": True}


@pytest.mark.parametrize("list_cents", [7000, 6997, 10000, 3500, 8000])
@pytest.mark.parametrize("pct", [0.5, 2.6, 5, 7.25, 10, 13, 15, 18.45, 25, 29])
def test_a_discounted_price_has_no_cents(list_cents, pct):
    got = pricing.apply_discount(list_cents, pct, 0)
    assert got % 100 == 0 or got == list_cents, got


def test_rounding_is_up_never_down():
    # 3 bottles on today's ramp: 70.00 * (1 - 0.29 * 2/11) = 66.31 -> 67, not 66
    pct = pricing.same_sku_pct(3, S)
    assert pricing.apply_discount(7000, pct, 0) == 6700


def test_a_whole_dollar_result_is_left_alone():
    assert pricing.apply_discount(10000, 10, 0) == 9000


def test_no_discount_leaves_the_list_price_alone():
    assert pricing.apply_discount(6997, 0, 3990) == 6997


def test_rounding_never_lifts_a_price_above_its_list():
    # 1% off 3.97 = 3.93 -> 4.00 would exceed the list. A floor at 3.50 rules out the
    # $1-minimum fallback (3.00), so the list is charged.
    assert pricing.apply_discount(397, 1, 350) == 397


@pytest.mark.parametrize("list_cents,pct,want", [
    (997, 5, 900),     # 9.47 -> 10.00 would save nothing; the next dollar down instead
    (1000, 5, 900),    # Glen's example: a $10 item ends at $9
    (797, 10, 700),
    (6997, 1, 6900),   # 69.27 -> 70.00 is above list
    (397, 1, 300),
])
def test_a_discount_that_rounds_away_drops_to_the_next_dollar_down(list_cents, pct, want):
    # Glen 2026-10-02, yes to: a discount that would round away to nothing gives the
    # next whole dollar below the list instead, so the buyer always saves something.
    assert pricing.apply_discount(list_cents, pct, 0) == want


def test_the_next_dollar_down_still_respects_the_floor():
    # 5% off 1.50: the next dollar down (1.00) is under the 1.40 floor, so no discount
    assert pricing.apply_discount(150, 5, 140) == 150


def test_an_uneven_floor_is_rounded_up_too():
    # 50% off 70.00 clamps to the 57% floor, 39.90 -> 40.00
    assert pricing.apply_discount(7000, 50, 3990) == 4000


def test_today_s_ladder_in_whole_dollars():
    floor = pricing.unit_floor_cents(FF, 7000, S, "discount")
    got = {n: pricing.apply_discount(7000, pricing.same_sku_pct(n, S), floor)
           for n in (1, 2, 3, 4, 6, 12)}
    assert got == {1: 7000, 2: 6900, 3: 6700, 4: 6500, 6: 6100, 12: 5000}


def _line(qty, **kw):
    out = pricing.compute([{"slug": "ff", "name": "FF", "qty": qty, "product": FF,
                            "unit_cents": 7000, "months": qty, "volume_eligible": True}],
                          settings=S, **kw)
    return out["lines"][0]


def test_cart_lines_are_whole_dollars_PER_BOTTLE():
    # A line of 3 is 3 x 67.00. Rounding the LINE total instead would give 198.93 -> 199,
    # which is 66.33 a bottle: whole dollars on the line, cents on every bottle.
    line = _line(3)
    assert line["line_total_cents"] == 3 * 6700
    assert line["discount_cents"] == 3 * 7000 - 3 * 6700


@pytest.mark.parametrize("qty", range(1, 19))
def test_every_quantity_prices_each_bottle_in_whole_dollars(qty):
    line = _line(qty)
    assert line["line_total_cents"] % qty == 0
    assert (line["line_total_cents"] // qty) % 100 == 0


def test_autoship_and_coupon_percentages_round_up_too():
    assert _line(1, subscriber_tier_pct=3)["line_total_cents"] == 6800    # 67.90 -> 68
    assert _line(1, coupon_pct=7)["line_total_cents"] == 6600             # 65.10 -> 66


def test_repertoire_reorder_rounds_up_too():
    line = _line(1, program_member=True, repertoire_slugs={"ff"})
    assert line["line_total_cents"] == 5000                               # 49.70 -> floor 50


def test_points_redeem_in_whole_dollars_only():
    """Glen, 2026-10-01: points redeem in whole dollars, 20 points at a time."""
    out = pricing.compute([{"slug": "ff", "name": "FF", "qty": 1, "product": FF,
                            "unit_cents": 7000, "months": 1, "volume_eligible": True}],
                          settings=S, points_to_redeem_cents=135)
    assert out["lines"][0]["line_total_cents"] == 7000 - 100
    assert out["points_redeemed_cents"] == 100


def test_a_percent_off_cohort_price_is_whole_dollars_too():
    # Round 1 review: cohorts priced percent_off outside apply_discount, so a winning
    # cohort line kept its cents. 15% off 69.97 = 59.47 -> 60.
    from dashboard import cohorts
    got = cohorts.policy_unit_cents({"type": "percent_off", "pct": 15}, slug="ff",
                                    list_cents=6997, is_ff=True)
    assert got == 6000


@pytest.mark.parametrize("list_cents", [3500, 6997, 7000, 10000])
@pytest.mark.parametrize("pct", [5, 10, 15, 29])
def test_a_real_discount_is_never_rounded_away_on_a_full_price_bottle(list_cents, pct):
    # The cents check above alone would pass an engine that never discounts.
    assert pricing.apply_discount(list_cents, pct, 0) < list_cents
