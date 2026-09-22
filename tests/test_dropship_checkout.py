"""Tests for dashboard.dropship_checkout — drop-ship wholesale pricing + order building.

QBO and wallet are stubbed using the same pattern as test_wholesale_checkout.py.
"""

from dashboard import dropship_checkout as dc


# ── dropship_line_cents ───────────────────────────────────────────────────────

def test_dropship_unit_price_is_base_plus_retail_fee():
    # base from blended curve; fee = 33% of (retail - base); drop-ship unit = base + fee
    # 1 bottle, uncertified: base $50.00, retail $70.00 -> fee 33%*(7000-5000)=660 -> unit 5660
    line = dc.dropship_line_cents(retail_cents=7000, qty=1, modules=0,
                                  settings=dc._settings())
    assert line["base_cents"] == 5000
    assert line["fee_cents"] == 660
    assert line["unit_cents"] == 5660          # what the practitioner pays per bottle
    assert line["line_cents"] == 5660          # x qty 1


def test_dropship_unit_uses_blended_volume_and_cert():
    # 12 bottles, fully certified: base $42.76, retail $70 -> fee 33%*(7000-4276)=899 -> 5175
    line = dc.dropship_line_cents(retail_cents=7000, qty=12, modules=12,
                                  settings=dc._settings())
    assert line["base_cents"] == 4276
    assert line["fee_cents"] == 899
    assert line["unit_cents"] == 5175
    assert line["line_cents"] == 5175 * 12


def test_dropship_fee_zero_when_retail_equals_base():
    line = dc.dropship_line_cents(retail_cents=5000, qty=1, modules=0, settings=dc._settings())
    assert line["fee_cents"] == 0 and line["unit_cents"] == 5000


# ── build_dropship_order ──────────────────────────────────────────────────────

def test_build_dropship_order_invoices_practitioner_ships_patient(monkeypatch):
    """Paid-only (Stage 4): build_dropship_order creates NO QBO invoice/customer
    -- it returns a checkout_ref token + a line-faithful qbo_payload for the
    route to persist and the return-handler to book once payment is confirmed."""
    cart = [{"slug": "brain-boost", "qty": 6}]
    prac = {"id": "p1", "modules_completed": 0, "email": "doc@x.com", "name": "Doc"}
    patient_ship = {"name": "Pat", "state": "CA", "country": "US", "address1": "1 St"}

    # stubs
    monkeypatch.setattr(dc, "_retail_for", lambda slug: 7000)

    def boom(*a, **k):
        raise AssertionError("build_dropship_order must not touch QBO invoicing (paid-only)")
    monkeypatch.setattr(dc.qb, "find_or_create_customer", boom)
    monkeypatch.setattr(dc.qb, "create_invoice", boom)

    # stub wallet — no balance to redeem
    import dashboard.wallet as _wallet
    monkeypatch.setattr(_wallet, "redeem_for_order", lambda pid, total, ref: 0)
    monkeypatch.setattr(_wallet, "earn_fee_free", lambda pid, charged, ref: 0)

    # stub tax
    import dashboard.tax as _tax
    monkeypatch.setattr(_tax, "compute_get_cents",
        lambda subtotal, *, channel, ship_to_state, resale_ok=False: 0)

    out = dc.build_dropship_order(cart, prac, patient_ship=patient_ship, method="zelle")

    assert out["ok"] is True
    assert out["ship_to"]["name"] == "Pat"            # ships to the PATIENT
    assert out["source"] == "dropship"
    assert out["customer_id"] == ""
    assert isinstance(out["invoice_id"], str) and len(out["invoice_id"]) == 32
    # 6 bottles uncertified: qbo_payload carries unit qty
    assert out["qbo_payload"]["lines"][0]["qty"] == 6


def _stub_order(monkeypatch, retail=7000, get=0):
    monkeypatch.setattr(dc, "_retail_for", lambda slug: retail)

    def boom(*a, **k):
        raise AssertionError("build_dropship_order must not touch QBO invoicing (paid-only)")
    monkeypatch.setattr(dc.qb, "find_or_create_customer", boom)
    monkeypatch.setattr(dc.qb, "create_invoice", boom)
    import dashboard.wallet as _wallet, dashboard.tax as _tax
    monkeypatch.setattr(_wallet, "redeem_for_order", lambda pid, total, ref: 0)
    monkeypatch.setattr(_wallet, "earn_fee_free", lambda pid, charged, ref: 0)
    monkeypatch.setattr(_tax, "compute_get_cents",
        lambda subtotal, *, channel, ship_to_state, resale_ok=False: get)


def test_multi_line_cart_prices_off_total_bottles(monkeypatch):
    # 3 + 3 = 6 total bottles → BOTH lines price at the 6-bottle blended base ($48.68),
    # not the 3-bottle base — and never the 1-bottle $50.
    _stub_order(monkeypatch)
    prac = {"id": "p1", "modules_completed": 0, "email": "doc@x.com", "name": "Doc"}
    out = dc.build_dropship_order(
        [{"slug": "a", "qty": 3}, {"slug": "b", "qty": 3}], prac,
        patient_ship={"name": "Pat", "state": "CA", "country": "US"}, method="zelle")
    assert out["ok"] is True
    base6 = dc.dropship_line_cents(retail_cents=7000, qty=6, modules=0, settings=dc._settings())
    lines = out["qbo_payload"]["lines"]
    assert round(lines[0]["amount"] * 100) == base6["unit_cents"]   # 6-bottle unit
    assert lines[0]["qty"] == 3 and lines[1]["qty"] == 3            # per-line qty


def test_practitioner_specific_flat_dropship_price(monkeypatch):
    _stub_order(monkeypatch, retail=6997)
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents",
                        lambda pid: 4000 if pid == "ashley" else None)
    ashley = {"id": "ashley", "modules_completed": 0,
              "email": "ashley@example.com", "name": "Ashley"}
    standard = {"id": "standard", "modules_completed": 0,
                "email": "standard@example.com", "name": "Standard"}
    cart = [{"slug": "a", "qty": 2}, {"slug": "b", "qty": 1}]
    ship = {"name": "Patient", "state": "CA", "country": "US"}

    special = dc.build_dropship_order(cart, ashley, patient_ship=ship, method="card")
    regular = dc.build_dropship_order(cart, standard, patient_ship=ship, method="card")

    assert special["subtotal_cents"] == 3 * 4000
    assert all(round(line["amount"] * 100) == 4000
               for line in special["qbo_payload"]["lines"])
    assert regular["subtotal_cents"] != special["subtotal_cents"]


def test_practitioner_specific_price_matches_quote_and_checkout(monkeypatch):
    _stub_order(monkeypatch, retail=6997)
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents",
                        lambda pid: 4000 if pid == "ashley" else None)
    ashley = {"id": "ashley", "modules_completed": 0,
              "email": "ashley@example.com", "name": "Ashley"}
    cart = [{"slug": "iron-out", "qty": 1}]

    quote = dc.quote_dropship_cart(cart, ashley)
    order = dc.build_dropship_order(
        cart, ashley,
        patient_ship={"name": "Linda", "state": "TX", "country": "US"},
        method="card")

    assert quote["lines"][0]["unit_cents"] == 4000
    assert quote["subtotal_cents"] == order["subtotal_cents"] == 4000
    assert round(order["qbo_payload"]["lines"][0]["amount"] * 100) == 4000


def test_get_recorded_not_charged(monkeypatch):
    # GET comes back in the result, never added to the qbo_payload lines.
    _stub_order(monkeypatch, get=275)
    prac = {"id": "p1", "modules_completed": 0, "email": "doc@x.com", "name": "Doc"}
    out = dc.build_dropship_order([{"slug": "a", "qty": 1}], prac,
        patient_ship={"name": "Pat", "state": "HI", "country": "US"}, method="zelle")
    assert out["get_cents"] == 275
    lines = out["qbo_payload"]["lines"]
    names = " ".join(l.get("name", "") + l.get("description", "") for l in lines).lower()
    assert "tax" not in names and "get" not in names      # no GET line in the payload


def test_shipping_is_added_to_practitioner_charge_and_qbo_payload(monkeypatch):
    _stub_order(monkeypatch)
    prac = {"id": "p1", "modules_completed": 0, "email": "doc@x.com", "name": "Doc"}
    out = dc.build_dropship_order(
        [{"slug": "a", "qty": 1}], prac,
        patient_ship={"name": "Pat", "state": "CA", "country": "US"},
        method="card", shipping_cents=1300)

    assert out["shipping_cents"] == 1300
    assert out["total"] == 69.60
    assert out["qbo_payload"]["lines"][-1] == {
        "name": "Shipping (USPS)", "amount": 13.0, "qty": 1,
        "description": "USPS shipping",
    }


def test_empty_cart_rejected(monkeypatch):
    _stub_order(monkeypatch)
    prac = {"id": "p1", "modules_completed": 0, "email": "doc@x.com", "name": "Doc"}
    assert dc.build_dropship_order([], prac, patient_ship={}, method="zelle")["ok"] is False
    assert dc.build_dropship_order([{"slug": "a", "qty": 0}], prac,
        patient_ship={}, method="zelle")["ok"] is False


# ── The flat price stops at the standard FF bottle (Glen, 2026-09-22) ─────────
#
# Ashley King's $40 flat bottle price reached WholOmega 120 gelcaps ($190 retail) on
# order #203. Glen: "a price for Functional Formulations generally doesn't extend to
# larger than minimum size bottles." Ruled the same day: the flat applies to items
# listed at or below the standard FF price, $69.97; anything dearer is a larger bottle
# and gets standard drop-ship pricing.

def _ashley(monkeypatch, retail_by_slug):
    _stub_order(monkeypatch)
    monkeypatch.setattr(dc, "_retail_for", lambda slug: retail_by_slug[slug])
    monkeypatch.setattr(dc, "_flat_ceiling_cents", lambda: 6997)
    monkeypatch.setattr(dc, "_practitioner_dropship_unit_cents",
                        lambda pid: 4000 if pid == "ashley" else None)
    return {"id": "ashley", "modules_completed": 0,
            "email": "ashley@example.com", "name": "Ashley"}


def test_flat_price_does_not_reach_a_larger_bottle(monkeypatch):
    ashley = _ashley(monkeypatch, {"wholomega": 6997, "wholomega-120-gelcaps": 19000})
    cart = [{"slug": "wholomega", "qty": 1}, {"slug": "wholomega-120-gelcaps", "qty": 1}]
    by = {l["slug"]: l for l in dc.quote_dropship_cart(cart, ashley)["lines"]}
    standard = dc.dropship_line_cents(retail_cents=19000, qty=2, modules=0,
                                      settings=dc._settings())["unit_cents"]
    assert by["wholomega"]["unit_cents"] == 4000
    assert by["wholomega-120-gelcaps"]["unit_cents"] == standard, (
        "REGRESSION: the $40 flat reached the 120-gelcap bottle, as on order #203")
    assert standard != 4000


def test_flat_price_still_covers_items_below_the_ff_price(monkeypatch):
    """Her $39.97 items (CoQ10, ESR, NAC...) keep the flat. Glen chose this line."""
    ashley = _ashley(monkeypatch, {"coq10": 3997, "clear-the-way": 6997})
    cart = [{"slug": "coq10", "qty": 1}, {"slug": "clear-the-way", "qty": 1}]
    assert [l["unit_cents"] for l in dc.quote_dropship_cart(cart, ashley)["lines"]] == [4000, 4000]


def test_checkout_charges_what_the_quote_showed(monkeypatch):
    ashley = _ashley(monkeypatch, {"wholomega-120-gelcaps": 19000})
    cart = [{"slug": "wholomega-120-gelcaps", "qty": 1}]
    quote = dc.quote_dropship_cart(cart, ashley)
    order = dc.build_dropship_order(cart, ashley, method="card",
                                    patient_ship={"name": "P", "state": "HI", "country": "US"})
    assert order["subtotal_cents"] == quote["subtotal_cents"] != 4000


def test_ceiling_follows_the_catalog_ff_price():
    """Tied to the catalog, so a future FF price change cannot silently strip every flat."""
    import app as _app
    assert dc._flat_ceiling_cents() == int(_app._PRODUCTS.get("default_price_cents"))


def test_quickbooks_lines_carry_the_product_name_not_the_slug(monkeypatch):
    """Glen, 2026-09-22: the invoice printed "lower case hyphenated" names."""
    ashley = _ashley(monkeypatch, {"wholomega-120-gelcaps": 19000})
    monkeypatch.setattr(dc, "_product_name",
                        lambda slug: {"wholomega-120-gelcaps": "WholOmega 120 gelcaps"}[slug])
    order = dc.build_dropship_order([{"slug": "wholomega-120-gelcaps", "qty": 1}], ashley,
                                    patient_ship={"name": "P", "state": "HI", "country": "US"})
    line = order["qbo_payload"]["lines"][0]
    assert line["name"] == "WholOmega 120 gelcaps"
    assert line["description"] == "WholOmega 120 gelcaps (drop-ship wholesale)"
    assert order["lines"][0]["slug"] == "wholomega-120-gelcaps"   # the record keeps the slug


def test_product_name_falls_back_to_the_slug_for_an_unknown_product():
    assert dc._product_name("no-such-product-xyz") == "no-such-product-xyz"
