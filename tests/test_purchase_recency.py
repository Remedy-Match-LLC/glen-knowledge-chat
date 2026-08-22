# tests/test_purchase_recency.py
"""A purchase must reach the engine, and stop selling for about a month.

Journey review finding 7. Two defects, one cause.

Nothing ever called `record_unlock(trigger="purchase")`. `/begin/checkout/<slug>`
wrote a `journey_events` audit row and stopped, while gates live on
`journey_state.unlocked_gates`. So:

  * a real buyer topped out at rung `choose_path`, and the top two rungs of the
    ten-rung ladder -- `ascend` and `advocate` -- were unreachable by any client;
  * `_mission_chips` already carried `if k == "remedy_match" and "purchase" in
    gates: continue`, a "stop offering the matcher to someone who bought" rule
    that could never fire. A client who had just paid was still told "Get your
    remedy match".

Glen's ruling, 2026-08-22: that suppression is right "for about 30 days". So it is
keyed on RECENCY, read from the timestamped event the checkout was already writing,
rather than on the permanent gate -- which would suppress the matcher forever, and
a client whose terrain has moved on should be matched again.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _cx():
    import begin_funnel as bf
    cx = sqlite3.connect(":memory:")
    bf.init_journey_tables(cx)
    return cx


def _event(cx, *, email="", session_id="", days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cx.execute("INSERT INTO journey_events (ts, session_id, email, trigger, detail) "
               "VALUES (?,?,?,?,?)", (ts, session_id, email, "purchase", "buy-x-card"))
    cx.commit()


def _state(gates=(), style="mission"):
    return {"travel_style": style, "current_rung": "inquire",
            "unlocked_gates": list(gates), "awareness_stage": "unknown"}


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_the_window_is_thirty_days():
    import begin_funnel as bf
    assert bf.PURCHASE_RECENCY_DAYS == 30


@pytest.mark.parametrize("days_ago,expected", [(0, True), (1, True), (29, True),
                                               (31, False), (400, False)])
def test_recency_by_email(days_ago, expected):
    import begin_funnel as bf
    cx = _cx()
    _event(cx, email="jane@example.com", days_ago=days_ago)
    assert bf.purchased_recently(cx, email="jane@example.com") is expected


def test_recency_by_session_for_a_guest():
    """A guest checkout has no email on the journey row yet."""
    import begin_funnel as bf
    cx = _cx()
    _event(cx, session_id="s-guest", days_ago=2)
    assert bf.purchased_recently(cx, session_id="s-guest") is True
    assert bf.purchased_recently(cx, session_id="s-other") is False


def test_recency_needs_an_identity():
    import begin_funnel as bf
    cx = _cx()
    _event(cx, email="jane@example.com", days_ago=1)
    assert bf.purchased_recently(cx) is False


def test_only_purchase_events_count():
    import begin_funnel as bf
    cx = _cx()
    cx.execute("INSERT INTO journey_events (ts, session_id, email, trigger, detail) "
               "VALUES (?,?,?,?,?)",
               (datetime.now(timezone.utc).isoformat(), "s1", "jane@example.com",
                "question", ""))
    cx.commit()
    assert bf.purchased_recently(cx, email="jane@example.com") is False


# ---------------------------------------------------------------------------
# What it changes on screen
# ---------------------------------------------------------------------------

def test_a_recent_buyer_is_not_sent_back_to_the_matcher():
    import begin_funnel as bf
    chips = bf.next_step_chips(_state(), query_texts=["what helps with my insomnia"],
                               signals={"purchased_recently": True})
    labels = [c["label"] for c in chips]
    assert "Get your remedy match" not in labels
    assert [c for c in chips if c["role"] == "primary"], "must still move forward"


def test_an_older_buyer_is_matched_again():
    """The point of Glen's 30 days: their terrain moves."""
    import begin_funnel as bf
    chips = bf.next_step_chips(_state(gates=["purchase"]),
                               query_texts=["what helps with my insomnia"],
                               signals={"purchased_recently": False})
    assert "Get your remedy match" in [c["label"] for c in chips]


def test_the_permanent_gate_alone_no_longer_suppresses():
    """Keying on the gate would silence the matcher forever."""
    import begin_funnel as bf
    with_gate = bf.next_step_chips(_state(gates=["purchase"]),
                                   query_texts=["what helps with my insomnia"],
                                   signals={})
    assert "Get your remedy match" in [c["label"] for c in with_gate]


# ---------------------------------------------------------------------------
# The ladder can be climbed again
# ---------------------------------------------------------------------------

def test_the_top_rungs_are_reachable_once_the_gate_is_recorded():
    import begin_funnel as bf
    buyer = {"video", "question", "name", "email", "tos", "scan", "paid_fork"}
    assert bf.compute_rung(buyer, "a@b.com", True) == "choose_path"
    assert bf.compute_rung(buyer | {"purchase"}, "a@b.com", True) == "ascend"


def test_something_records_the_purchase_gate():
    """The regression itself: a bare INSERT into journey_events left the gate unset.

    Asserted by PARSING app.py, not by grepping a window. The first version of this
    test searched 900 characters around the checkout for the string "record_unlock"
    and passed even with the call deleted, because other record_unlock calls live
    nearby. A substring over a wide window proves proximity, not wiring."""
    import ast
    src = __import__("pathlib").Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    def is_record_unlock(node):
        f = node.func
        return ((isinstance(f, ast.Attribute) and f.attr == "record_unlock")
                or (isinstance(f, ast.Name) and f.id == "record_unlock"))

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and is_record_unlock(n)
             and any(k.arg == "trigger" and isinstance(k.value, ast.Constant)
                     and k.value.value == "purchase" for k in n.keywords)]
    assert calls, ("nothing calls record_unlock(trigger='purchase'), so the gate "
                   "never reaches journey_state and the top two rungs die again")


# ---------------------------------------------------------------------------
# The map reaches the money
# ---------------------------------------------------------------------------

def test_find_now_carries_the_first_order():
    import begin_funnel as bf
    find = {c["key"]: c for c in bf.JOURNEY_STEPS}["find"]
    assert [s["key"] for s in find["steps"]][-1] == "first_order"
    assert find["steps"][-1]["src"] == ("gate", "purchase")


def test_the_first_order_step_lights_up_on_purchase():
    import begin_funnel as bf
    before = {c["key"]: c for c in bf.journey_map(_state(), signals={})}["find"]
    after = {c["key"]: c for c in bf.journey_map(_state(gates=["purchase"]),
                                                 signals={})}["find"]
    done = lambda card: {s["key"] for s in card["steps"] if s["done"]}
    assert "first_order" not in done(before)
    assert "first_order" in done(after)


def test_the_ladder_still_has_exactly_four_stages():
    """#1390's canon and the 5-hotspot quest scene both depend on this."""
    import begin_funnel as bf
    assert [c["key"] for c in bf.JOURNEY_STEPS] == bf.LADDER_ORDER
