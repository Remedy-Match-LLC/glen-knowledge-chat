from dashboard import qbo_summary


def test_qbo_collapses_physical_cart_to_one_order_total():
    lines = [
        {"name": "Terrain Restore", "amount": 69.97, "qty": 2},
        {"name": "Shipping (USPS)", "amount": 9.00, "qty": 1},
    ]
    assert qbo_summary.summarize(lines, 13494) == [{
        "name": "Order Total", "description": "RemedyMatch order total",
        "amount": 134.94, "qty": 1,
    }]


def test_qbo_collapses_service_cart_to_same_order_total():
    lines = [{"name": "Biofield Analysis", "amount": 300, "qty": 1}]
    assert qbo_summary.summarize(lines, 29900, source="biofield") == [{
        "name": "Order Total", "description": "RemedyMatch order total",
        "amount": 299.0, "qty": 1,
    }]


def test_mixed_cart_allocates_discount_and_reconciles_exactly():
    lines = [
        {"name": "Physical Thing", "amount": 100, "qty": 1,
         "sale_category": "physical"},
        {"name": "Digital Thing", "amount": 50, "qty": 1,
         "sale_category": "digital"},
    ]
    assert qbo_summary.in_house_breakdown(lines, 13500) == {
        "physical_goods_cents": 9000,
        "digital_services_cents": 4500,
    }
    out = qbo_summary.summarize(lines, 13500)
    assert out == [{"name": "Order Total", "description": "RemedyMatch order total",
                    "amount": 135.0, "qty": 1}]


def test_shipping_is_physical_in_house():
    out = qbo_summary.in_house_breakdown(
        [{"name": "Shipping (USPS)", "amount": 12, "qty": 1}], 1200)
    assert out == {"physical_goods_cents": 1200, "digital_services_cents": 0}


def test_county_and_product_split_remain_available_in_house():
    tracking = qbo_summary.in_house_tracking([
        {"name": "Physical Thing", "amount": 100, "qty": 1,
         "sale_category": "physical"},
        {"name": "Digital Thing", "amount": 50, "qty": 1,
         "sale_category": "digital"},
    ], 15000, address={"zip": "96720"})
    assert tracking == {
        "sales_area": "Hawaii County",
        "physical_goods_cents": 10000,
        "digital_services_cents": 5000,
    }


def test_only_physical_lines_receive_automatic_hawaii_county():
    lines = qbo_summary.classify_in_house_lines([
        {"name": "Bottle A", "amount": 60, "qty": 1,
         "sale_category": "physical"},
        {"name": "Bottle B", "amount": 40, "qty": 1,
         "sale_category": "physical"},
        {"name": "Consultation", "amount": 50, "qty": 1,
         "sale_category": "service"},
    ], 13500, address={"zip": "96793"})

    assert [line["sales_area"] for line in lines] == [
        "Maui County", "Maui County", None]
    assert [line["sales_type"] for line in lines] == [
        "physical_goods", "physical_goods", "digital_services"]
    assert sum(line["net_cents"] for line in lines) == 13500


def test_non_hawaii_physical_line_is_classified_in_house():
    lines = qbo_summary.classify_in_house_lines(
        [{"name": "Bottle", "amount": 25, "qty": 1}], 2500,
        address={"zip": "90210"})
    assert lines[0]["sales_area"] == "Non-Hawaii"
