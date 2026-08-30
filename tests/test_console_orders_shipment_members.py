from pathlib import Path


ORDERS_HTML = Path(__file__).resolve().parents[1] / "static" / "console-orders.html"


def test_combined_shipment_members_show_order_number_and_name():
    html = ORDERS_HTML.read_text(encoding="utf-8")

    assert (
        "'&bull; Order #'+Number(m.id)+' &middot; '"
        "+esc(m.name||m.email||'Unnamed client')"
    ) in html
