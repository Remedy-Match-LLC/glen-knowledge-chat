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


def test_the_remedy_itself_is_clickable_to_choose_a_condition():
    html = render_dispensed_panel(ROWS)
    assert html.count("pickCondition(this)") == len(ROWS)
    assert "IOP Syntropy</button>" in html


def test_each_related_condition_is_its_own_clickable_chip():
    rows = [dict(ROWS[0], conditions=["Dry Eye", "Glaucoma"])]
    html = render_dispensed_panel(rows)
    assert html.count("attachPair(this)") == 2
    assert 'data-cond="Dry Eye"' in html and 'data-cond="Glaucoma"' in html


def test_a_remedy_with_no_known_conditions_still_renders():
    html = render_dispensed_panel([dict(ROWS[0], conditions=[])])
    assert "attachPair(this)" not in html
    assert "pickCondition(this)" in html, "the remedy must still be clickable"


def test_a_condition_name_with_an_apostrophe_is_escaped_not_inlined():
    """Meniere's would break a handler built by string-concatenating the name."""
    rows = [dict(ROWS[0], conditions=["Meniere's Disease"])]
    html = render_dispensed_panel(rows)
    assert "attachPair(this)" in html
    assert "Meniere&#x27;s Disease" in html
    assert "attachPair(this,'Meniere's" not in html
    # the ATTRIBUTE itself, not just the visible chip text: an unescaped
    # data-cond would end the attribute at the apostrophe.
    assert 'data-cond="Meniere&#x27;s Disease"' in html


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


# --- FMP history: the orders that predate this system --------------------------
#
# Glen, 2026-09-04: "You are missing many orders for Debra... I see 9 in FMP."
# Our orders table held 2 of hers; FileMaker holds 8 more. Ranking on 2 of 10
# orders is not thin, it is wrong.

HISTORY = [
    {"client": {"name": "Anastasia Herndon", "email": "chakamom1@gmail.com"}, "orders": []},
    {"client": {"name": "Debra Herndon", "email": "chakamom1@gmail.com"}, "orders": [
        {"date": "2026-06-01", "items": [{"description": "Biofield Analysis"},
                                         {"description": "Courtesy"},
                                         {"description": "Chelation in cello"}]},
        {"date": "2026-04-29", "items": [{"description": "Chelation in bottle"},
                                         {"description": "Reverse AGE"}]}]},
    {"client": {"name": "Eliana Herndon", "email": "chakamom1@gmail.com"}, "orders": [
        {"date": "2025-11-15", "items": [{"description": "Nous Energy"}]}]},
]


def test_a_shared_family_email_does_not_merge_their_histories():
    """chakamom1@gmail.com is five different Herndons in FileMaker. Matching on
    email alone would file Eliana's order as Debra's."""
    orders = bd.fmp_orders_for(HISTORY, "Debra Herndon", "chakamom1@gmail.com")
    names = {i["name"] for o in orders for i in o["items"]}
    assert "Nous Energy" not in names, "another family member's order leaked in"
    assert "Chelation" in names


def test_an_unmatched_name_returns_nothing_rather_than_everyone():
    """Fail closed: showing one client another's history is worse than showing
    none of it."""
    assert bd.fmp_orders_for(HISTORY, "Someone Else", "chakamom1@gmail.com") == []
    assert bd.fmp_orders_for(HISTORY, "", "chakamom1@gmail.com") == []


def test_the_name_match_tolerates_case_and_spacing():
    assert bd.fmp_orders_for(HISTORY, "  debra   herndon ", "x@y.com")


def test_the_packaging_suffix_is_stripped_so_one_product_is_one_row():
    """'Chelation in cello' and 'Chelation in bottle' are the same remedy in two
    packagings. Left alone they rank as two different products, each at half the
    frequency they deserve."""
    orders = bd.fmp_orders_for(HISTORY, "Debra Herndon", "chakamom1@gmail.com")
    rows = bd.frequency(orders, orders[0]["email"])
    top = [r for r in rows if r["name"] == "Chelation"]
    assert top and top[0]["count"] == 2, [r["name"] for r in rows]


def test_service_and_discount_lines_are_not_products():
    orders = bd.fmp_orders_for(HISTORY, "Debra Herndon", "chakamom1@gmail.com")
    names = {i["name"] for o in orders for i in o["items"]}
    assert "Courtesy" not in names and "Biofield Analysis" not in names


def test_numbered_biofield_lines_are_excluded_too():
    """FileMaker carries 'Biofield Analysis #1' as well as the plain one."""
    h = [{"client": {"name": "P Q", "email": "p@e.com"},
          "orders": [{"date": "2026-01-01", "items": [{"description": "Biofield Analysis #1"},
                                                      {"description": "Vitality"}]}]}]
    names = {i["name"] for o in bd.fmp_orders_for(h, "P Q", "p@e.com") for i in o["items"]}
    assert names == {"Vitality"}


def test_blank_descriptions_are_dropped():
    h = [{"client": {"name": "P Q", "email": "p@e.com"},
          "orders": [{"date": "2026-01-01", "items": [{"description": ""},
                                                      {"description": None},
                                                      {"description": "Vitality"}]}]}]
    assert len(bd.fmp_orders_for(h, "P Q", "p@e.com")[0]["items"]) == 1


def test_fmp_and_current_orders_rank_together():
    """The whole point: one ranking across both sources."""
    current = [_o("chakamom1@gmail.com", "2026-09-01", "Chelation")]
    fmp = bd.fmp_orders_for(HISTORY, "Debra Herndon", "chakamom1@gmail.com")
    rows = bd.frequency(current + fmp, "chakamom1@gmail.com")
    chel = [r for r in rows if r["name"] == "Chelation"][0]
    assert chel["count"] == 3 and chel["orders_considered"] == 3 and chel["pct"] == 100


def test_fmp_junk_never_raises():
    for bad in (None, [], [None], [{"client": None}], [{"client": {}, "orders": "no"}]):
        assert bd.fmp_orders_for(bad, "A B", "p@e.com") == []


def test_the_author_route_merges_filemaker_history_matched_by_name():
    """Two mutants walked past the earlier tests: dropping the FileMaker orders
    from the ranking, and matching them without the client's name. The first
    silently returns to ranking 2 of Debra's 10 orders; the second files a
    daughter's order as her mother's."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "biofield_local_app.py").read_text()
    tree = ast.parse(src)
    fmp = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
           and isinstance(c.func, ast.Attribute) and c.func.attr == "fmp_orders_for"]
    assert fmp, "FileMaker history is no longer consulted"
    name_arg = ast.unparse(fmp[0].args[1])
    assert "name" in name_arg and '""' not in name_arg and "''" not in name_arg, name_arg

    freq = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute) and c.func.attr == "frequency"]
    assert freq, "nothing is ranked"
    orders_arg = ast.unparse(freq[0].args[0])
    assert "client_orders" in orders_arg, orders_arg
    assert "_older" in orders_arg, (
        "the ranking no longer includes FileMaker history: " + orders_arg)


def test_the_author_route_looks_up_each_remedys_conditions():
    """Parsed, not grepped: without this the column silently renders empty and
    the whole clickable half of the panel disappears."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "biofield_local_app.py").read_text()
    assigns = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                       and t.slice.value == "conditions" for t in n.targets)]
    assert assigns, "the panel never gets any conditions"
    # The file assigns ["conditions"] elsewhere too, so require that at least one
    # of them is the reverse lookup rather than assuming the first is ours.
    exprs = [ast.unparse(a.value) for a in assigns]
    assert any("conditions_for_remedy" in e for e in exprs), exprs
