"""The coaching window runs 30 days from DELIVERY, not from when we noticed.

Glen, 2026-09-11: "window should extend 30 days from delivery", and "back dating is
ok when needed".

Before this, the delivery path passed `utcnow()` as `delivered_at`, so the window
always started the moment the sweep happened to run. Pamela Kilmer's parcel arrived
2026-08-31 and was only linked on 09-11, so she would have been granted a month
starting eleven days late — eleven days of coaching she had already paid for and
could not use.

A window is the client's entitlement to reach a coach: three /api/community
endpoints refuse without an active one. So the dates here are a real benefit, not
bookkeeping.

The uncomfortable consequence is deliberate. A parcel delivered 40 days ago opens a
window that has ALREADY expired. That is the honest outcome — the client had the
remedies all along — and it is what makes a wide catch-up sweep safe to run.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dashboard import coaching as C
from dashboard import usps_status as US


# ── Reading the carrier's own timestamp ──────────────────────────────────────
# Every string below is real USPS wording from Glen's mailbox.

def test_reads_the_delivery_time_from_a_real_mailbox_body():
    body = ("Your item was delivered in or at the mailbox at 10:58 am on "
            "September 9, 2026 in COEUR D ALENE, ID 83814. Tracking Number: "
            "9405530109355412869056")
    assert US.parse_delivered_at(body) == "2026-09-09T10:58:00Z"


def test_reads_a_parcel_locker_delivery_with_a_pm_time():
    body = ("Your item was delivered to a parcel locker at 2:39 pm on August 31, "
            "2026 in DAVENPORT, FL 33837.")
    assert US.parse_delivered_at(body) == "2026-08-31T14:39:00Z"


def test_noon_and_midnight_do_not_wrap():
    assert US.parse_delivered_at(
        "delivered at 12:05 pm on August 31, 2026") == "2026-08-31T12:05:00Z"
    assert US.parse_delivered_at(
        "delivered at 12:05 am on August 31, 2026") == "2026-08-31T00:05:00Z"


def test_a_body_with_no_timestamp_returns_none_rather_than_guessing():
    """The caller falls back to now. A guess here mis-dates a client's month."""
    for body in ["Your item is out for delivery on September 9, 2026 at 6:10 am.",
                 "USPS expects to deliver your package by Monday.",
                 "", None]:
        assert US.parse_delivered_at(body) is None


def test_an_unreadable_month_or_date_returns_none():
    assert US.parse_delivered_at("delivered at 9:00 am on Smarch 4, 2026") is None
    assert US.parse_delivered_at("delivered at 9:00 am on February 30, 2026") is None


def test_a_future_delivery_is_refused():
    """A window starting in the future would deny access the client has earned, so
    a parse that claims one is treated as unreadable."""
    ahead = datetime.now(timezone.utc) + timedelta(days=400)
    body = f"delivered at 9:00 am on {ahead.strftime('%B %-d, %Y')}"
    assert US.parse_delivered_at(body) is None


# ── open_window already honours an injected start ────────────────────────────

@pytest.fixture
def cx():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    C.init_coaching_table(conn)
    yield conn
    conn.close()


def test_a_back_dated_window_ends_30_days_after_delivery(cx):
    delivered = datetime.utcnow() - timedelta(days=10)
    res = C.open_window(cx, email="a@b.c", order_id=146, days=C.WINDOW_DAYS,
                        source="delivery", now=delivered)
    assert res["created"] is True
    started = datetime.fromisoformat(res["window"]["started_at"].rstrip("Z"))
    ends = datetime.fromisoformat(res["window"]["ends_at"].rstrip("Z"))
    assert abs((started - delivered).total_seconds()) < 2
    assert (ends - started).days == C.WINDOW_DAYS
    # 30 days from a delivery 10 days ago leaves about 20, not 30.
    assert 19 <= res["window"]["days_remaining"] <= 20


def test_a_long_past_delivery_opens_an_already_expired_window(cx):
    """Deliberate. Glen approved back-dating, and this is what it means at the far
    end: the month elapsed while the client had the remedies."""
    delivered = datetime.utcnow() - timedelta(days=40)
    res = C.open_window(cx, email="a@b.c", order_id=1, days=C.WINDOW_DAYS,
                        source="delivery", now=delivered)
    assert res["created"] is True
    assert res["window"]["days_remaining"] == 0
    # And it must not read as active, so it cannot block a later real window.
    assert C.active_window(cx, "a@b.c") is None


def test_an_expired_back_dated_window_still_blocks_a_re_open_for_that_order(cx):
    """One per order. Re-running a sweep must not hand out a second month."""
    delivered = datetime.utcnow() - timedelta(days=40)
    C.open_window(cx, email="a@b.c", order_id=146, days=C.WINDOW_DAYS,
                  source="delivery", now=delivered)
    again = C.open_window(cx, email="a@b.c", order_id=146, days=C.WINDOW_DAYS,
                          source="delivery", now=delivered)
    assert again["created"] is False


def test_no_start_given_means_the_window_starts_now(cx):
    """The manual 'mark delivered' paths pass nothing, and today is right there."""
    res = C.open_window(cx, email="a@b.c", order_id=5, days=C.WINDOW_DAYS,
                        source="admin")
    assert res["window"]["days_remaining"] in (C.WINDOW_DAYS - 1, C.WINDOW_DAYS)


# ── The sweep hands the timestamp onward ─────────────────────────────────────
TN = "9405530109355412869056"
DELIVERED_BODY = (
    "Your item was delivered in or at the mailbox at 10:58 am on September 9, "
    "2026 in COEUR D ALENE, ID 83814. Tracking Number: " + TN)
SUBJ = "USPS® Item Delivered, In/At Mailbox " + TN


class _Exec:
    def __init__(self, v):
        self.value = v

    def execute(self):
        return self.value


class _Svc:
    def __init__(self, msgs):
        self.msgs = msgs

    def users(self):
        return self

    def messages(self):
        return self

    def getProfile(self, **k):
        return _Exec({"emailAddress": "drglenswartwout@gmail.com"})

    def list(self, **k):
        return _Exec({"messages": [{"id": m[0]} for m in self.msgs]})

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        import base64
        subj, body = next((s, b) for i, s, b in self.msgs if i == id)
        return _Exec({"internalDate": "1789000000000",
                      "payload": {"mimeType": "text/plain",
                                  "body": {"data": base64.urlsafe_b64encode(
                                      body.encode()).decode()},
                                  "headers": [{"name": "Subject", "value": subj}]}})


def test_the_sweep_passes_the_carrier_timestamp_to_advance():
    from dashboard.tracking import init_tracking_schema, record_shipment
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="X", status="sent")
    seen = {}

    def advance(cx, tracking_code, carrier_status, delivered_at=None):
        seen["delivered_at"] = delivered_at
        return 1

    US.run_status_sweep(conn, _Svc([("m1", SUBJ, DELIVERED_BODY)]), days=7,
                        advance=advance)
    conn.close()
    assert seen["delivered_at"] == "2026-09-09T10:58:00Z"


def test_the_sweep_keeps_the_earliest_scan_when_usps_repeats_itself():
    """USPS re-sends the delivery scan. The first one carries the real moment."""
    from dashboard.tracking import init_tracking_schema, record_shipment
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="X", status="sent")
    later = DELIVERED_BODY.replace("10:58 am", "4:12 pm")
    seen = {}

    def advance(cx, tracking_code, carrier_status, delivered_at=None):
        seen["delivered_at"] = delivered_at
        return 1

    US.run_status_sweep(conn, _Svc([("m1", SUBJ, later),
                                    ("m2", SUBJ, DELIVERED_BODY)]),
                        days=7, advance=advance)
    conn.close()
    assert seen["delivered_at"] == "2026-09-09T10:58:00Z"


def test_a_delivery_with_no_readable_time_passes_none():
    """Then the app side falls back to now, rather than inventing a date."""
    from dashboard.tracking import init_tracking_schema, record_shipment
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="X", status="sent")
    body = "Your item was delivered. Tracking Number: " + TN
    seen = {}

    def advance(cx, tracking_code, carrier_status, delivered_at=None):
        seen["delivered_at"] = delivered_at
        return 1

    US.run_status_sweep(conn, _Svc([("m1", SUBJ, body)]), days=7, advance=advance)
    conn.close()
    assert seen["delivered_at"] is None


# ── The app actually USES it (the link a mutation exposed as untested) ────────
#
# Every test above passed with app.py ignoring `started_at` and starting the window
# at now. That is the one link that makes the whole change real, so it needs its own
# test rather than being implied by the others.

def test_open_coaching_for_order_back_dates_the_window(monkeypatch, tmp_path):
    import importlib
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        app_module = importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    C.init_coaching_table(conn)
    conn.execute("CREATE TABLE orders (id INTEGER, email TEXT, created_at TEXT)")
    conn.execute("INSERT INTO orders VALUES (146,'pk@example.com','2026-08-20T00:00:00Z')")
    conn.commit()
    # Eligibility is orchestrated elsewhere and is not what this test is about.
    monkeypatch.setattr(app_module, "_membership_active_at", lambda *a, **k: True)

    delivered = (datetime.utcnow() - timedelta(days=10))
    stamp = delivered.isoformat() + "Z"
    res = app_module._open_coaching_for_order(
        conn, "pk@example.com", 146, "groovekart",
        window_source="delivery", started_at=stamp)

    assert res["ok"] is True
    ends = datetime.fromisoformat(res["ends_at"].rstrip("Z"))
    # 30 days from the DELIVERY, so it ends ~20 days out, not ~30.
    assert 19 <= (ends - datetime.utcnow()).days <= 20
    conn.close()


def test_open_coaching_for_order_starts_now_when_given_nothing(monkeypatch):
    import importlib
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        app_module = importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    C.init_coaching_table(conn)
    conn.execute("CREATE TABLE orders (id INTEGER, email TEXT, created_at TEXT)")
    conn.execute("INSERT INTO orders VALUES (9,'x@example.com','2026-08-20T00:00:00Z')")
    conn.commit()
    monkeypatch.setattr(app_module, "_membership_active_at", lambda *a, **k: True)

    res = app_module._open_coaching_for_order(conn, "x@example.com", 9, "groovekart")

    ends = datetime.fromisoformat(res["ends_at"].rstrip("Z"))
    assert (ends - datetime.utcnow()).days in (C.WINDOW_DAYS - 1, C.WINDOW_DAYS)
    conn.close()


def test_an_unreadable_started_at_falls_back_to_now_rather_than_raising(monkeypatch):
    """A malformed timestamp must not 500 the delivery path."""
    import importlib
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        app_module = importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    C.init_coaching_table(conn)
    conn.execute("CREATE TABLE orders (id INTEGER, email TEXT, created_at TEXT)")
    conn.execute("INSERT INTO orders VALUES (7,'y@example.com','2026-08-20T00:00:00Z')")
    conn.commit()
    monkeypatch.setattr(app_module, "_membership_active_at", lambda *a, **k: True)

    res = app_module._open_coaching_for_order(
        conn, "y@example.com", 7, "groovekart",
        window_source="delivery", started_at="not a date")

    assert res["ok"] is True
    ends = datetime.fromisoformat(res["ends_at"].rstrip("Z"))
    assert (ends - datetime.utcnow()).days in (C.WINDOW_DAYS - 1, C.WINDOW_DAYS)
    conn.close()


def test_activate_for_shipment_forwards_the_delivery_time_to_the_window(monkeypatch):
    """The middle link. A mutation that dropped `started_at` here left every other
    test in this file green, because each one exercises the layer above or below."""
    import importlib
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        app_module = importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable: {e}")

    seen = {}
    monkeypatch.setattr(app_module._tracking, "mark_shipment_delivered",
                        lambda *a, **k: True)
    monkeypatch.setattr(app_module._coaching, "shipment_member_orders",
                        lambda *a, **k: [{"id": 146, "email": "pk@example.com",
                                          "source": "groovekart"}])
    monkeypatch.setattr(app_module._bos_orders, "set_order_status",
                        lambda *a, **k: True)
    monkeypatch.setattr(app_module, "_extend_biofield_month_on_delivery",
                        lambda *a, **k: "none")
    monkeypatch.setattr(app_module._bos_orders, "get_order", lambda *a, **k: {})

    def recorder(cx, email, order_id, source, window_source="self_serve",
                 started_at=None):
        seen["started_at"] = started_at
        return {"ok": True, "created": True, "ends_at": "2026-09-30T00:00:00Z"}
    monkeypatch.setattr(app_module, "_open_coaching_for_order", recorder)

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE shipments (id INTEGER, coaching_opened INTEGER)")
    conn.execute("INSERT INTO shipments VALUES (19, 0)")
    conn.commit()
    shipment = {"id": 19, "order_uuid": None, "delivered_at": None,
                "resolved_email": "pk@example.com"}
    app_module._activate_coaching_for_shipment(
        conn, shipment, delivered_at="2026-08-31T14:39:00Z")
    conn.close()

    assert seen["started_at"] == "2026-08-31T14:39:00Z"


def test_the_log_line_names_the_delivery_date_it_will_use():
    """This whole change is about a date. If the line does not print it, a
    mis-dated window is undetectable from outside: the summary counts read the
    same whether the window starts at delivery or at now."""
    from dashboard.tracking import init_tracking_schema, record_shipment
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="X", status="sent")
    lines = []

    US.run_status_sweep(conn, _Svc([("m1", SUBJ, DELIVERED_BODY)]), days=7,
                        advance=lambda *a, **k: 1, log=lines.append)
    conn.close()
    blob = "\n".join(lines)
    assert "2026-09-09T10:58:00Z" in blob


def test_a_parcel_with_no_readable_date_logs_no_date_rather_than_a_wrong_one():
    from dashboard.tracking import init_tracking_schema, record_shipment
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="X", status="sent")
    body = "Your item was delivered. Tracking Number: " + TN
    lines = []

    US.run_status_sweep(conn, _Svc([("m1", SUBJ, body)]), days=7,
                        advance=lambda *a, **k: 1, log=lines.append)
    conn.close()
    blob = "\n".join(lines)
    # "delivered None" is as misleading as a wrong date, and a weaker assertion
    # here let a mutation printing exactly that slip through.
    assert "delivered" not in blob.split(": delivered", 1)[0].split("—")[0][len(TN):]
    assert "None" not in blob
    assert "acted" in blob
