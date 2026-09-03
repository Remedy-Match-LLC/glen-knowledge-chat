"""Warn when an invoice bills a client ABOVE their own saved special price.

Debra Herndon's invoice was rebuilt at full retail: Biofield Analysis at $300
against a saved $0.00 courtesy, and formulations at $69.97 against a saved $50.00
flat. $443 became $809.13 and she was sent a link to pay it. She caught it; a
less attentive client pays.

Deliberately a WARNING and not a refusal. This sits on the shared invoice pricer,
and an operator stop in shared pricing code once 400'd 79 products at CLIENT
checkout for six days. A legitimate above-rate charge must still be saveable.
"""
import pytest

from dashboard import special_price_guard as guard


SAVED = {"ff_flat_cents": 5000,
         "sku": {"biofield-analysis": 0, "iop-syntropy": 5000}}


def test_it_names_a_line_billed_above_a_saved_per_sku_price():
    items = [{"slug": "biofield-analysis", "unit_cents": 30000, "qty": 1},
             {"slug": "iop-syntropy", "unit_cents": 6997, "qty": 2}]
    found = guard.overpriced_lines(items, SAVED)
    assert [f["slug"] for f in found] == ["biofield-analysis", "iop-syntropy"]
    assert found[0]["saved_cents"] == 0 and found[0]["unit_cents"] == 30000


def test_the_flat_rate_covers_a_sku_with_no_entry_of_its_own():
    """Her $50 flat covered Transform, Brain Boost and GastroZyme, none of which
    had a per-SKU row. Ignoring the flat rate would have missed three lines."""
    items = [{"slug": "transform", "unit_cents": 6997, "qty": 1, "qty_pricing": True}]
    found = guard.overpriced_lines(items, SAVED, ff_eligible=lambda slug: True)
    assert [f["slug"] for f in found] == ["transform"]
    assert found[0]["saved_cents"] == 5000


def test_the_flat_rate_does_not_judge_a_non_formulation():
    """ff_flat applies to Functional Formulations only; judging everything by it
    would flag ordinary retail products as overpriced."""
    items = [{"slug": "water-ionizer", "unit_cents": 20000, "qty": 1}]
    assert guard.overpriced_lines(items, SAVED, ff_eligible=lambda slug: False) == []


def test_billing_at_or_below_the_saved_price_is_silent():
    items = [{"slug": "iop-syntropy", "unit_cents": 5000, "qty": 1},
             {"slug": "biofield-analysis", "unit_cents": 0, "qty": 1}]
    assert guard.overpriced_lines(items, SAVED) == []


def test_a_client_with_no_saved_prices_is_never_flagged():
    items = [{"slug": "anything", "unit_cents": 9999, "qty": 1}]
    assert guard.overpriced_lines(items, {}) == []
    assert guard.overpriced_lines(items, {"ff_flat_cents": None, "sku": {}}) == []


def test_the_message_says_what_to_do():
    items = [{"slug": "biofield-analysis", "unit_cents": 30000, "qty": 1}]
    msg = guard.warning_for(guard.overpriced_lines(items, SAVED))
    assert "biofield-analysis" in msg
    assert "$300.00" in msg and "$0.00" in msg
    assert guard.warning_for([]) == ""


def test_a_bad_row_never_raises_into_the_invoice_path():
    """This runs while saving a live invoice; it must never be the thing that
    breaks one."""
    for junk in ([{"slug": None, "unit_cents": "x"}], [{}], [None], None):
        assert guard.overpriced_lines(junk, SAVED) == []


# --- the DB half: same check, reading a real client_prices table -------------

def _seed(tmp_path, email):
    """Build and fill client_prices through its OWN DDL and writers.

    A hand-typed CREATE TABLE here could disagree with production in either
    direction: failing correct code, or passing broken code.
    """
    from dashboard import client_prices, db
    cx = db.connect(str(tmp_path / "guard.db"))
    client_prices.init_table(cx)
    client_prices.set_ff_flat(cx, email, 5000)
    client_prices.set_price(cx, email, "biofield-analysis", 0)
    cx.commit()
    return cx


def test_warning_for_client_fires_on_debras_invoice(tmp_path):
    # Her rebuilt invoice: Biofield at full $300 against her $0 courtesy and
    # formulations at $69.97 against her $50 flat rate. Both must be named.
    cx = _seed(tmp_path, "deb@example.com")
    w = guard.warning_for_client(cx, "deb@example.com", [
        {"slug": "biofield-analysis", "qty": 1, "unit_cents": 30000},
        {"slug": "neuro-magnesium", "qty": 3, "unit_cents": 6997},
    ])
    cx.close()
    assert "biofield-analysis" in w and "$300.00" in w and "$0.00" in w
    assert "neuro-magnesium" in w and "$69.97" in w and "$50.00" in w


def test_warning_for_client_silent_when_priced_correctly(tmp_path):
    cx = _seed(tmp_path, "deb@example.com")
    w = guard.warning_for_client(cx, "deb@example.com", [
        {"slug": "biofield-analysis", "qty": 1, "unit_cents": 0},
        {"slug": "neuro-magnesium", "qty": 3, "unit_cents": 5000},
    ])
    cx.close()
    assert w == ""


def test_warning_for_client_honors_ff_eligible(tmp_path):
    # The flat rate covers Functional Formulations. Judging a $2,000 ionizer by a
    # $50 FF rate would cry wolf on every ordinary retail line.
    cx = _seed(tmp_path, "deb@example.com")
    line = [{"slug": "water-ionizer", "qty": 1, "unit_cents": 200000}]
    assert guard.warning_for_client(
        cx, "deb@example.com", line, ff_eligible=lambda s: False) == ""
    assert "water-ionizer" in guard.warning_for_client(
        cx, "deb@example.com", line, ff_eligible=lambda s: True)
    cx.close()


def test_saved_prices_none_for_client_without_special_pricing(tmp_path):
    cx = _seed(tmp_path, "deb@example.com")
    assert guard.saved_prices(cx, "nobody@example.com") is None
    assert guard.saved_prices(cx, "") is None
    assert guard.warning_for_client(cx, "nobody@example.com", [
        {"slug": "neuro-magnesium", "qty": 1, "unit_cents": 6997}]) == ""
    cx.close()


def test_saved_prices_excludes_the_reserved_ff_flat_slug(tmp_path):
    # The flat rate is stored as a reserved slug row. If it leaked into the per-SKU
    # map, a line for that pseudo-slug could be judged against itself.
    from dashboard import client_prices
    cx = _seed(tmp_path, "deb@example.com")
    saved = guard.saved_prices(cx, "deb@example.com")
    cx.close()
    assert saved["ff_flat_cents"] == 5000
    assert client_prices.FF_FLAT_SLUG not in saved["sku"]
    assert saved["sku"]["biofield-analysis"] == 0


def test_saved_prices_matches_regardless_of_email_case(tmp_path):
    cx = _seed(tmp_path, "deb@example.com")
    w = guard.warning_for_client(cx, "  Deb@Example.COM ", [
        {"slug": "biofield-analysis", "qty": 1, "unit_cents": 30000}])
    cx.close()
    assert "biofield-analysis" in w


# --- the record: a warning nobody can trace is a warning that repeats ---------

def test_recording_an_event_captures_the_route_and_actor(tmp_path):
    """A one-off overcharge is a mystery; six with a route on each is a bug report.

    Debra's cost a day of guessing which path rebuilt her invoice. The row must
    name the route so the next one names its own cause.
    """
    from dashboard import db
    cx = db.connect(str(tmp_path / "ev.db"))
    guard.record_events(cx, [
        {"slug": "biofield-analysis", "unit_cents": 30000, "saved_cents": 0}],
        order_id=165, route="/api/orders/<oid>/edit", actor="glen@example.com")
    cx.commit()
    rows = guard.recent_events(cx)
    cx.close()
    assert len(rows) == 1
    r = rows[0]
    assert r["order_id"] == 165 and r["route"] == "/api/orders/<oid>/edit"
    assert r["actor"] == "glen@example.com" and r["slug"] == "biofield-analysis"
    assert r["billed_cents"] == 30000 and r["saved_cents"] == 0
    assert r["ts"]


def test_recording_nothing_writes_nothing(tmp_path):
    from dashboard import db
    cx = db.connect(str(tmp_path / "ev.db"))
    guard.record_events(cx, [], order_id=1, route="r", actor="a")
    cx.commit()
    assert guard.recent_events(cx) == []
    cx.close()


def test_recent_events_returns_newest_first_and_honors_limit(tmp_path):
    from dashboard import db
    cx = db.connect(str(tmp_path / "ev.db"))
    for n in range(3):
        guard.record_events(cx, [
            {"slug": "s%d" % n, "unit_cents": 100 * (n + 1), "saved_cents": 0}],
            order_id=n, route="r", actor="a")
    cx.commit()
    rows = guard.recent_events(cx, limit=2)
    cx.close()
    assert [r["slug"] for r in rows] == ["s2", "s1"]


def test_recording_never_raises_on_a_bad_connection():
    # It runs inside the invoice save. A logging fault must not cost the save.
    guard.record_events(None, [{"slug": "x", "unit_cents": 1, "saved_cents": 0}],
                        order_id=1, route="r", actor="a")


# --- the wiring: a guard on one of three write paths is a guard with two holes -

def test_every_invoice_writing_route_runs_the_guard():
    """Debra's invoice was rebuilt by ONE path; the next one need not be.

    Parses app.py rather than grepping it, so a mention inside a comment or a
    docstring cannot satisfy this the way a string search once did.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    guarded = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "_special_price_warning"):
                route = next((k.value.value for k in call.keywords
                              if k.arg == "route" and isinstance(k.value, ast.Constant)), None)
                assert route, f"{fn.name} calls the guard with no route to blame"
                guarded[fn.name] = route
    assert set(guarded) == {"api_orders_edit", "api_orders_manual",
                            "api_orders_grant_member_access"}, guarded
    assert len(set(guarded.values())) == 3, f"routes must be distinguishable: {guarded}"


def test_the_guard_is_reported_not_enforced():
    """It must warn, never refuse.

    An operator stop in shared pricing code once returned 400 for 79 products at
    CLIENT checkout for six days. Nothing here may abort a save.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
              and f.name == "_special_price_warning")
    for node in ast.walk(fn):
        assert not isinstance(node, ast.Raise), "the guard must not raise"
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns and all(
        not (isinstance(r.value, ast.Tuple)) for r in returns), "the guard must not return a status code"


# --- the fix: a saved rate is a ceiling, not a suggestion ---------------------
#
# Order #165 was CREATED at $809.13 by a script that passed catalog list prices as
# explicit unit_cents for every line. Confirmed against production: with no
# unit_cents her two lines price at $50.00, with explicit retail they price at
# $369.97. An explicit price silently outranked the $0 courtesy Glen had granted
# her in July. A line may still be discounted BELOW her rate; it may not quietly
# rise above it.

def test_saved_rate_for_prefers_the_per_sku_price():
    assert guard.saved_rate_for("biofield-analysis", SAVED) == 0
    assert guard.saved_rate_for("iop-syntropy", SAVED) == 5000


def test_saved_rate_for_falls_back_to_the_flat_rate_for_formulations():
    assert guard.saved_rate_for("brain-boost", SAVED, ff_eligible=lambda s: True) == 5000
    assert guard.saved_rate_for("brain-boost", SAVED, ff_eligible=lambda s: False) is None


def test_saved_rate_for_is_none_without_saved_pricing():
    assert guard.saved_rate_for("brain-boost", {"ff_flat_cents": None, "sku": {}}) is None
    assert guard.saved_rate_for("brain-boost", None) is None


def test_cap_lowers_an_override_that_rises_above_the_saved_rate():
    # The exact defect: a script passed $300 for a line she is owed at $0.
    assert guard.cap_to_saved(30000, 0) == 0
    assert guard.cap_to_saved(6997, 5000) == 5000


def test_cap_leaves_a_deeper_courtesy_alone():
    # Glen discounting BELOW her rate is intent, not a defect. Never raise a line.
    assert guard.cap_to_saved(2500, 5000) == 2500
    assert guard.cap_to_saved(0, 5000) == 0


def test_cap_is_inert_without_a_saved_rate():
    assert guard.cap_to_saved(6997, None) == 6997


def test_cap_never_invents_a_price_from_junk():
    assert guard.cap_to_saved(None, 5000) is None
    assert guard.cap_to_saved("", 5000) == ""


def test_the_pricer_caps_explicit_overrides_to_the_saved_rate():
    """The ceiling must live in the shared pricer, not in one caller.

    #165 was created by a script, not by the editor, so a fix applied only to the
    console would have left the path that actually caused this untouched.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    fn = next(f for f in ast.walk(ast.parse(src.read_text()))
              if isinstance(f, ast.FunctionDef) and f.name == "_price_inhouse_invoice")
    called = {c.func.attr for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert "cap_to_saved" in called, "the pricer no longer holds a line to the saved rate"
    assert "saved_rate_for" in called, "the pricer no longer resolves the saved rate"
    # Applying the cap without recording it would correct the price SILENTLY, which
    # is how the first one went unnoticed for a day. The record must be kept and
    # must leave the function.
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "append" and isinstance(c.func.value, ast.Name)
               and c.func.value.id == "price_caps"
               for c in ast.walk(fn)), "caps are applied but never recorded"
    returns = [r for r in ast.walk(fn) if isinstance(r, ast.Return)
               and isinstance(r.value, ast.Dict)]
    assert any(any(isinstance(k, ast.Constant) and k.value == "price_caps" for k in r.value.keys)
               for r in returns), "price_caps never leaves the pricer"


def test_the_cap_is_reported_wherever_an_invoice_is_written():
    # Silent correction is how the first one went unnoticed. Every write path that
    # can cap a line must also say that it did.
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    users = {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
             for c in ast.walk(f)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
             and c.func.id == "_price_cap_notice"}
    assert users == {"api_orders_edit", "api_orders_manual",
                     "api_orders_grant_member_access"}, users
