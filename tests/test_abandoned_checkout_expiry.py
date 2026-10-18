from datetime import datetime, timedelta, timezone

from dashboard import orders


def _add(cx, *, source, created_at, status="new", pay_status="unpaid", ref="r"):
    cx.execute(
        "INSERT INTO orders (created_at, source, external_ref, status, pay_status) "
        "VALUES (?,?,?,?,?)",
        (created_at, source, ref, status, pay_status))
    cx.commit()


def test_expires_only_stale_unpaid_customer_checkouts(tmp_path):
    cx = orders.sqlite3.connect(tmp_path / "orders.db")
    cx.row_factory = orders.sqlite3.Row
    orders.init_orders_table(cx)
    now = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    old = (now - timedelta(hours=25)).isoformat()
    fresh = (now - timedelta(hours=23)).isoformat()

    _add(cx, source="reorder", created_at=old, ref="old-cart")
    _add(cx, source="funnel", created_at=fresh, ref="fresh-cart")
    _add(cx, source="reorder", created_at=old, pay_status="paid", ref="paid")
    _add(cx, source="in-house", created_at=old, status="proposed", ref="proposal")
    _add(cx, source="dropship", created_at=old, ref="dropship")

    assert orders.expire_abandoned_checkouts(cx, now=now) == 1
    states = {r[0]: r[1] for r in cx.execute(
        "SELECT external_ref, status FROM orders").fetchall()}
    assert states == {
        "old-cart": "cancelled", "fresh-cart": "new", "paid": "new",
        "proposal": "proposed", "dropship": "new",
    }


def test_live_board_query_excludes_cancelled_before_limit(tmp_path):
    cx = orders.sqlite3.connect(tmp_path / "orders.db")
    cx.row_factory = orders.sqlite3.Row
    orders.init_orders_table(cx)
    stamp = datetime(2026, 8, 25, tzinfo=timezone.utc).isoformat()
    _add(cx, source="reorder", created_at=stamp, status="new", ref="live")
    _add(cx, source="reorder", created_at=stamp, status="cancelled", ref="hidden")

    visible = orders.list_orders(cx, limit=1, include_cancelled=False)
    assert [row["external_ref"] for row in visible] == ["live"]
    assert {row["external_ref"] for row in orders.list_orders(cx)} == {"live", "hidden"}


def test_an_unpaid_zelle_order_is_not_abandoned(tmp_path):
    # A portal Zelle order is unpaid until the bank transfer lands, often days later.
    # It must not be cancelled by the 24-hour cart expiry (2026-10-10).
    cx = orders.sqlite3.connect(tmp_path / "orders.db")
    cx.row_factory = orders.sqlite3.Row
    orders.init_orders_table(cx)
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    old = (now - timedelta(hours=72)).isoformat()
    _add(cx, source="portal-reorder", created_at=old, ref="zelle-order")
    _add(cx, source="portal-reorder", created_at=old, ref="card-cart")
    cx.execute("UPDATE orders SET pay_method='zelle' WHERE external_ref='zelle-order'")
    cx.commit()

    assert orders.expire_abandoned_checkouts(cx, now=now) == 1
    states = {r[0]: r[1] for r in cx.execute(
        "SELECT external_ref, status FROM orders").fetchall()}
    assert states == {"zelle-order": "new", "card-cart": "cancelled"}


def test_a_card_retry_under_a_zelle_request_id_still_expires(tmp_path):
    # Zelle try saved, response lost, customer switches to card under the same request
    # id. The card branch writes pay_method "" so the abandoned card checkout expires.
    cx = orders.sqlite3.connect(tmp_path / "orders.db")
    cx.row_factory = orders.sqlite3.Row
    orders.init_orders_table(cx)
    orders.upsert_order(cx, source="portal-reorder", external_ref="portal-x", pay_method="zelle")
    orders.upsert_order(cx, source="portal-reorder", external_ref="portal-x", pay_method="")
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    cx.execute("UPDATE orders SET created_at=?", ((now - timedelta(hours=30)).isoformat(),))
    cx.commit()
    assert orders.expire_abandoned_checkouts(cx, now=now) == 1
