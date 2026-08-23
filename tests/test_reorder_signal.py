# tests/test_reorder_signal.py
""""Your reorder" lights up when a client has bought the same formulation twice.

The remaining half of journey review finding 7. I deferred this when the map first
reached the money, because `purchase_history` only exposed
`slugs_since(email, window_days)` and I would not ship a journey step on a
predicate I had not verified. Building one that can never light up is exactly how
`ascend` and `advocate` became dead rungs nobody noticed.

What the dig turned up:

  * `purchase_history` is written by TWO backfills only -- `fmp` and `groovekart`.
    Nothing writes portal or funnel orders into it. The portal code says so
    outright: "portal purchases come from the orders table, not here".
  * So the portal's per-row `is_reorder` flag compares an order against LEGACY
    purchases only. A client who buys the same thing twice through the portal is
    labelled "Order" both times. Real gap, flagged separately, not changed here.

`_has_reordered` therefore reads BOTH sources, because neither alone is complete:
the orders table catches a modern repeat, purchase_history catches a
pre-migration purchase repeated since. Glen confirmed 2026-08-23 that clients do
sometimes reorder, so this lights for some and stays grey for others -- which is
what a progress map is for.
"""

import sqlite3

import pytest


def _state(gates=("purchase",)):
    return {"travel_style": "mission", "current_rung": "ascend",
            "unlocked_gates": list(gates), "awareness_stage": "unknown"}


# ---------------------------------------------------------------------------
# The step
# ---------------------------------------------------------------------------

def test_find_carries_the_reorder_step_last():
    import begin_funnel as bf
    find = {c["key"]: c for c in bf.JOURNEY_STEPS}["find"]
    assert [s["key"] for s in find["steps"]][-1] == "reorder"


def test_reorder_is_a_predicate_not_a_gate():
    """Nothing writes a `reorder` trigger, so a gate here could never fire --
    the same defect that left `ascend` and `advocate` unreachable."""
    import begin_funnel as bf
    find = {c["key"]: c for c in bf.JOURNEY_STEPS}["find"]
    step = [s for s in find["steps"] if s["key"] == "reorder"][0]
    assert step["src"] == ("predicate", "has_reordered")


def test_the_step_lights_only_on_the_signal():
    import begin_funnel as bf
    done = lambda sig: {s["key"] for s in
                        [c for c in bf.journey_map(_state(), signals=sig)
                         if c["key"] == "find"][0]["steps"] if s["done"]}
    assert "reorder" not in done({"has_reordered": False})
    assert "reorder" not in done({})
    assert "reorder" in done({"has_reordered": True})


def test_the_ladder_still_has_four_stages():
    import begin_funnel as bf
    assert [c["key"] for c in bf.JOURNEY_STEPS] == bf.LADDER_ORDER


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------

def _app():
    import importlib, pathlib, sys
    repo = pathlib.Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app not importable: {e}")


def _cx_with_orders(rows, history=()):
    """rows: list of (status, [slug, ...]). history: slugs in purchase_history.

    Built with the module's OWN schema and its OWN writer rather than a
    hand-copied CREATE TABLE. My first pass invented the schema from memory and
    called the items column `items`; it is `items_json`, and `_row_to_dict`
    parses it into `items`. The fixture raised KeyError, `_has_reordered`
    swallowed it, and the predicate read False -- which is precisely how this
    signal would have behaved in production if the test had not existed. A
    fixture that can drift from the real table can pass while the code is
    broken, or fail while the code is fine. This one cannot drift.
    """
    from dashboard import orders as _o
    cx = sqlite3.connect(":memory:")
    _o.init_orders_table(cx)          # row_factory deliberately NOT set here:
    # _begin_signals hands over a plain connection too, so leaving it unset is
    # what makes app._has_reordered's own row_factory line load-bearing. Set it
    # in the fixture and that line can be deleted with every test still green.
    for i, (status, slugs) in enumerate(rows, 1):
        _o.upsert_order(cx, source="portal", external_ref="R%d" % i,
                        email="jane@example.com", name="Jane",
                        items=[{"slug": s, "qty": 1} for s in slugs],
                        total_cents=1000, status=status)
    cx.execute("""CREATE TABLE IF NOT EXISTS purchase_history (email TEXT,
                  slug TEXT, purchased_at TEXT, source TEXT, source_ref TEXT)""")
    for s in history:
        cx.execute("INSERT INTO purchase_history VALUES (?,?,?,?,?)",
                   ("jane@example.com", s, "2024-01-01", "fmp", "x" + s))
    cx.commit()
    return cx


def test_the_fixture_writes_rows_the_real_reader_can_read():
    """Guards the fixture itself: if the orders schema moves under us, this
    fails loudly instead of every reorder assertion quietly reading False."""
    from dashboard import orders as _o
    cx = _cx_with_orders([("paid", ["terrain-restore"])])
    cx.row_factory = sqlite3.Row      # this test calls the reader directly
    got = _o.list_orders_by_email(cx, "jane@example.com")
    assert len(got) == 1, "the real reader cannot see the fixture's rows"
    assert [i.get("slug") for i in got[0]["items"]] == ["terrain-restore"]


def test_one_order_is_not_a_reorder():
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"])])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_same_slug_in_two_orders_is_a_reorder():
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"]), ("paid", ["terrain-restore"])])
    assert app._has_reordered(cx, "jane@example.com") is True


def test_two_of_the_same_thing_in_ONE_order_is_not_a_reorder():
    """Buying two bottles at once is a bigger first order, not a second one.
    The pre-refactor loop walked line items across all orders into one set, so
    two lines of the same slug in a SINGLE order tripped `repeated` -- untested
    in either direction. _order_slug_counts dedupes within an order, which makes
    the count mean "orders containing this slug"; that is the meaning the portal
    CTA needs too, and both surfaces now read it from the same function."""
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore", "terrain-restore"])])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_two_orders_of_different_things_is_not_a_reorder():
    """Buying more is not buying again."""
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"]), ("paid", ["nous-energy"])])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_a_legacy_purchase_repeated_now_counts():
    """The case the portal's is_reorder flag was built for: bought before the
    migration, bought again since."""
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"])], history=["terrain-restore"])
    assert app._has_reordered(cx, "jane@example.com") is True


def test_a_legacy_purchase_never_repeated_does_not_count():
    app = _app()
    cx = _cx_with_orders([("paid", ["nous-energy"])], history=["terrain-restore"])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_a_renamed_product_bought_twice_is_a_reorder():
    """Glen retired `relax` in favour of `stress-release` (products.json
    superseded_by, set 2026-08-21 when the shortlinks were repointed). A client
    who bought one then the other bought the SAME formulation twice. Without the
    fold they read as two different products and the step stays grey forever for
    every client whose first purchase predates a rename."""
    app = _app()
    live = app._superseded("relax")
    if not live:
        pytest.skip("`relax` no longer carries a superseded_by pointer")
    cx = _cx_with_orders([("paid", ["relax"]), ("paid", [live])])
    assert app._has_reordered(cx, "jane@example.com") is True


def test_a_blank_slug_is_not_a_product_bought_twice():
    """Two line items with no slug (a shipping line, a hand-keyed adjustment)
    are not evidence of anything. Counting them would light the step for
    somebody who has ordered exactly once."""
    app = _app()
    cx = _cx_with_orders([("paid", ["", ""])])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_cancelled_orders_do_not_make_a_reorder():
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"]),
                          ("cancelled", ["terrain-restore"])])
    assert app._has_reordered(cx, "jane@example.com") is False


def test_no_email_is_not_a_reorder():
    app = _app()
    cx = _cx_with_orders([("paid", ["terrain-restore"]), ("paid", ["terrain-restore"])])
    assert app._has_reordered(cx, "") is False


def test_a_read_failure_returns_false_rather_than_raising():
    """It feeds /begin/state; an exception here would 500 the journey payload."""
    app = _app()
    cx = sqlite3.connect(":memory:")          # no orders table at all
    assert app._has_reordered(cx, "jane@example.com") is False


def test_the_signal_is_exposed_to_the_journey():
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_begin_signals"]
    assert fn, "_begin_signals is gone"
    keys = [k.value for n in ast.walk(fn[0]) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)]
    assert "has_reordered" in keys, "the journey never receives the signal"


# ---------------------------------------------------------------------------
# `reorder` must not tell a fresh buyer to go shopping
# ---------------------------------------------------------------------------
# Adding the step re-introduced, by the side door, the behaviour Glen asked to
# suppress: because a card is only "done" at fill >= 1.0, a client who had
# scanned, taken the course, been matched AND bought dropped from "Find: done"
# to "3 of 4", which handed Find the "your next step" tag pointing at the
# matcher -- a person who paid days ago told to go get their remedy match.
# `defer_while` removes a not-yet-due step from the card entirely rather than
# showing it undone, because an undone step is what drags fill below 1.0.

_SCAN = ["scan", "course_ww"]
_ALL = _SCAN + ["question", "biofield", "purchase"]


def _map(gates, sig):
    import begin_funnel as bf
    return {c["key"]: c for c in bf.journey_map(_state(gates), signals=sig)}


def test_a_fresh_buyer_is_not_sent_back_to_the_matcher():
    m = _map(_ALL, {"purchased_recently": True, "has_reordered": False})
    assert m["find"]["status"] == "done", "a client who just paid reads as incomplete"
    nxt = [k for k, c in m.items() if c["status"] == "next"]
    assert "find" not in nxt, "the matcher is being offered to someone who just bought"


def test_the_reorder_step_is_hidden_while_the_purchase_is_fresh():
    m = _map(_ALL, {"purchased_recently": True, "has_reordered": False})
    assert "reorder" not in [s["key"] for s in m["find"]["steps"]]
    assert len(m["find"]["steps"]) == 3


def test_it_comes_back_once_the_supply_would_have_run_out():
    m = _map(_ALL, {"purchased_recently": False, "has_reordered": False})
    keys = [s["key"] for s in m["find"]["steps"]]
    assert "reorder" in keys and len(keys) == 4
    assert m["find"]["status"] == "next"
    assert m["find"]["href"].startswith("/reorder"), m["find"]["href"]


def test_a_client_who_reordered_completes_the_card():
    m = _map(_ALL, {"purchased_recently": False, "has_reordered": True})
    assert m["find"]["status"] == "done"
    assert [k for k, c in m.items() if c["status"] == "next"] == ["heal"]


def test_the_step_points_at_the_reorder_cart_not_the_matcher():
    """A client reordering wants the thing they already chose. /begin/match
    would re-run the matcher on somebody who has already decided."""
    import begin_funnel as bf
    find = {c["key"]: c for c in bf.JOURNEY_STEPS}["find"]
    step = [s for s in find["steps"] if s["key"] == "reorder"][0]
    assert step["href"] == "/reorder"


def test_deferring_never_hides_a_step_that_is_already_done():
    """Guards the interaction: purchased_recently and has_reordered can BOTH be
    true (bought twice, most recently last week). Hiding a completed step would
    silently un-credit work the client actually did."""
    m = _map(_ALL, {"purchased_recently": True, "has_reordered": True})
    assert m["find"]["status"] == "done"
    assert m["find"]["fill"] == 1.0


def test_no_other_card_defers_anything_by_accident():
    import begin_funnel as bf
    deferring = [(c["key"], s["key"]) for c in bf.JOURNEY_STEPS
                 for s in c["steps"] if s.get("defer_while")]
    assert deferring == [("find", "reorder")], deferring
