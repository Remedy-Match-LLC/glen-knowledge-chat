"""The console's payment-method dropdowns list Authorize.net, and stay in step.

Glen, 2026-09-24: "add Authorize.net to the list of payment methods". The method is
stored as text (order_payments.method, orders.pay_method); nothing validates it
against a list, so the three dropdowns are the only lists there are.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def _options(page, select_id):
    src = (STATIC / page).read_text()
    m = re.search(r'<select id="%s">(.*?)</select>' % select_id, src, re.S)
    assert m, (page, select_id)
    return re.findall(r"<option>([^<]+)</option>", m.group(1))


LISTS = [("order-new.html", "o-method"), ("order-new.html", "pay-method"),
         ("console-orders.html", "pay-method")]


def test_every_list_offers_authorize_net():
    for page, sid in LISTS:
        assert "Authorize.net" in _options(page, sid), (page, sid)


def test_the_lists_offer_the_same_methods():
    """Stripe is labelled "Credit card (Stripe)" on the order form; same method."""
    norm = lambda xs: sorted("Stripe" if x == "Credit card (Stripe)" else x for x in xs)
    sets = [norm(_options(p, s)) for p, s in LISTS]
    assert sets[0] == sets[1] == sets[2], sets
