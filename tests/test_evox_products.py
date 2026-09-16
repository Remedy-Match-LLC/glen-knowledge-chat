import json, pathlib, sqlite3, tempfile, os

from dashboard import shipping

def _products():
    p = pathlib.Path(__file__).resolve().parent.parent / "data" / "products.json"
    return json.loads(p.read_text())["products"]

def test_hand_cradle_sku_present():
    p = _products()["hand-cradle"]
    assert p["price_cents"] == 29700
    assert p.get("info_only") is not True          # physical: goes through the packer
    assert p.get("bottle_type") == "handcradle"    # registered packer dim, resolves to M box

def test_hand_cradle_packs_into_medium_flat_rate_box():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cx = sqlite3.connect(db_path)
        shipping.init_shipping_schema(cx)
        cx.commit()
        cx.close()
        assert shipping.pick_box({"handcradle": 1}, db_path=db_path) == "M"
    finally:
        os.remove(db_path)

def test_evox_session_sku_present():
    p = _products()["evox-session"]
    # Reconciled 2026-07-20 (Glen): $150 public (= the in-app EVOX booking rate),
    # $197 as SRP/compare-at. Was a bare $197 SKU conflicting with the $150
    # booking.
    assert p["price_cents"] == 15000
    assert p["regular_cents"] == 19700        # $197 SRP, struck through
    assert p["info_only"] is True and p["service"] is True   # prepay service, no shipping


def test_evox_session_carries_glens_three_tier_ladder():
    """Glen 2026-09-16: "EVOX: public $197 (value), free member $150, paid member
    $50 per session." The July build deferred the member rate to Rae's invoice by
    hand, at $100. This pins the ladder he actually set, so the next session does
    not reinstate $100 from the old plan document."""
    p = _products()["evox-session"]
    assert p["regular_cents"] == 19700, "the $197 value anchor"
    assert p["price_cents"] == 15000, "what a free member pays"
    assert p["member_price_cents"] == 5000, "what a paid member pays per session"
    # Same shape as Biofield Analysis, which is the pattern _member_price_cents
    # reads. Keyed off real membership, so a non-member cannot self-serve $50.
    b = _products()["biofield-analysis"]
    assert b["member_price_cents"] < b["price_cents"]
    assert p["member_price_cents"] < p["price_cents"]
