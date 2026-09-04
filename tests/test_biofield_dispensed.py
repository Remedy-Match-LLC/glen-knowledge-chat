"""What this client has actually been dispensed, most often first.

Glen, 2026-09-04: a reference list in Biofield Intake showing the products a
client has had before, as a percentage of their own recent orders, with a
selector to attach any of them to a symptom or condition.

Scoped to the individual client's order history, over up to 50 of their orders.
A ten-order window was considered and rejected against real data: 27 of 36
products tied on the same count, so "in order of frequency" would have ranked
noise.

A product counts ONCE per order. Ordering three bottles of one remedy is one
order that contained it, not three; counting units would rank bulk buys as
"frequent" when they are a single decision.
"""
import pytest

from dashboard import biofield_dispensed as bd


def _o(email, when, *names, status="done"):
    return {"email": email, "created_at": when, "status": status,
            "items": [{"name": n, "slug": n.lower().replace(" ", "-")} for n in names]}


ORDERS = [
    _o("Pat@Example.com", "2026-09-01", "Microbiome", "Liver Support"),
    _o("pat@example.com", "2026-08-01", "Microbiome", "Brain Boost"),
    _o("pat@example.com", "2026-07-01", "Microbiome"),
    _o("other@example.com", "2026-08-15", "Rescue", "Rescue", "Rescue"),
]


def test_it_ranks_this_clients_products_by_how_often_they_appear():
    rows = bd.frequency(ORDERS, "pat@example.com")
    assert [(r["name"], r["count"], r["pct"]) for r in rows] == [
        ("Microbiome", 3, 100), ("Brain Boost", 1, 33), ("Liver Support", 1, 33)]


def test_it_only_counts_this_clients_orders():
    rows = bd.frequency(ORDERS, "pat@example.com")
    assert "Rescue" not in {r["name"] for r in rows}


def test_the_email_match_ignores_case_and_padding():
    assert bd.frequency(ORDERS, "  PAT@Example.com ")[0]["count"] == 3


def test_a_product_counts_once_per_order_however_many_bottles():
    """Three bottles in one order is one order that contained it. Counting units
    would rank a bulk buy as the most frequent thing the client takes."""
    rows = bd.frequency(ORDERS, "other@example.com")
    assert rows[0]["name"] == "Rescue" and rows[0]["count"] == 1
    assert rows[0]["orders_considered"] == 1 and rows[0]["pct"] == 100


def test_cancelled_orders_are_not_history():
    orders = ORDERS + [_o("pat@example.com", "2026-09-02", "Ghost", status="cancelled")]
    assert "Ghost" not in {r["name"] for r in bd.frequency(orders, "pat@example.com")}


def test_only_the_most_recent_orders_are_considered():
    many = [_o("p@e.com", "2026-%02d-01" % m, "Old") for m in range(1, 13)]
    many.append(_o("p@e.com", "2026-12-15", "New"))
    rows = bd.frequency(many, "p@e.com", limit=2)
    assert rows[0]["orders_considered"] == 2
    assert {r["name"] for r in rows} == {"New", "Old"}


def test_services_and_memberships_are_not_dispensed_products():
    """Biofield Analysis rides on half of all orders and would top the list
    permanently while being nothing anyone dispenses."""
    o = {"email": "p@e.com", "created_at": "2026-09-01", "status": "done", "items": [
        {"name": "Biofield Analysis", "slug": "biofield-analysis", "service": True},
        {"name": "Care membership", "slug": "membership:care"},
        {"name": "Microbiome", "slug": "microbiome"}]}
    assert [r["name"] for r in bd.frequency([o], "p@e.com")] == ["Microbiome"]


def test_any_service_line_is_excluded_not_just_the_biofield_fee():
    """The previous test used a service line that ALSO had the biofield-analysis
    slug, so the slug check alone satisfied it and the service flag went untested.
    A consult or a class is not something you dispense."""
    o = {"email": "p@e.com", "created_at": "2026-09-01", "status": "done", "items": [
        {"name": "Consultation", "slug": "consult-60", "service": True},
        {"name": "Microbiome", "slug": "microbiome"}]}
    assert [r["name"] for r in bd.frequency([o], "p@e.com")] == ["Microbiome"]


def test_infoceuticals_are_included():
    """Glen's ruling: the E4L side is dispensed too."""
    o = _o("p@e.com", "2026-09-01", "ED6 Heart Driver", "ET2 Immu 2: Immunity 2")
    assert len(bd.frequency([o], "p@e.com")) == 2


def test_a_client_with_no_history_gets_an_empty_list_not_an_error():
    assert bd.frequency(ORDERS, "nobody@example.com") == []
    assert bd.frequency([], "p@e.com") == []
    assert bd.frequency(None, "p@e.com") == []
    assert bd.frequency(ORDERS, "") == []


def test_ties_are_ordered_by_name_so_the_list_is_stable():
    """A list that reshuffles on every load cannot be used as a reference."""
    rows = bd.frequency(ORDERS, "pat@example.com")
    tied = [r["name"] for r in rows if r["count"] == 1]
    assert tied == sorted(tied)


def test_junk_never_raises():
    for bad in ([None, 7, {}], [{"items": "nope", "email": "p@e.com"}],
                [{"email": "p@e.com", "items": [None, 3, {}]}]):
        assert isinstance(bd.frequency(bad, "p@e.com"), list)


# --- the panel -----------------------------------------------------------------

from dashboard.biofield_report_html import render_author_html, render_dispensed_panel

ROWS = [{"name": "IOP Syntropy", "slug": "iop-syntropy", "count": 2,
         "orders_considered": 2, "pct": 100},
        {"name": "Brain Boost", "slug": "brain-boost", "count": 1,
         "orders_considered": 2, "pct": 50}]


def test_the_panel_shows_the_count_beside_the_percentage():
    """100% off two orders and off forty are very different claims. Most clients
    have only a handful of orders, so a bare percentage would overstate."""
    html = render_dispensed_panel(ROWS)
    assert "100%" in html and "2 of 2" in html
    assert "50%" in html and "1 of 2" in html


def test_the_panel_is_collapsed_until_asked_for():
    html = render_dispensed_panel(ROWS)
    assert "display:none" in html and "Show previously dispensed" in html
    assert "Hide previously dispensed" in render_dispensed_panel(ROWS, open_=True)


def test_every_row_can_be_attached_to_a_condition():
    html = render_dispensed_panel(ROWS)
    assert html.count("attachDispensed(this)") == len(ROWS)
    assert 'data-remedy="IOP Syntropy"' in html
    assert "dispconds" in html, "no condition list is offered"


def test_a_client_with_no_history_is_told_so_plainly():
    html = render_dispensed_panel([])
    assert "No order history" in html
    assert "attachDispensed" not in html


def test_the_panel_only_appears_when_the_caller_looked_it_up():
    """None means nobody asked; [] means this client genuinely has no history.
    Rendering an empty panel for every caller that never fetched would be noise."""
    rep = {"test_id": "a1", "client": {"name": "P", "email": "p@e.com"}, "date": "",
           "layers": [], "schedule": {"slots": [], "entries": []}}
    # id=dispbody is emitted only by the panel. The words "previously dispensed"
    # also live in the page's JS, so matching those would pass either way.
    assert "id=dispbody" not in render_author_html(rep)
    assert "id=dispbody" in render_author_html(rep, dispensed=ROWS)
    assert "No order history" in render_author_html(rep, dispensed=[])


def test_the_product_name_is_escaped_into_the_attach_control():
    evil = [{"name": '<img src=x onerror=alert(1)>', "slug": "x", "count": 1,
             "orders_considered": 1, "pct": 100}]
    html = render_dispensed_panel(evil)
    assert "<img src=x" not in html and "&lt;img" in html


def test_the_author_route_asks_for_this_clients_orders():
    """Parsed, not grepped: the panel must be fed from the client's own history."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "biofield_local_app.py").read_text()
    calls = [c for c in ast.walk(ast.parse(src))
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr == "frequency"]
    assert calls, "the author page never ranks anything"
    call = calls[0]
    assert len(call.args) >= 2, ast.unparse(call)
    orders_arg, email_arg = ast.unparse(call.args[0]), ast.unparse(call.args[1])
    assert "client_orders" in orders_arg, orders_arg
    # The email must be THIS client's, not a blank that would rank everyone.
    assert email_arg.strip() == "c_email", email_arg
