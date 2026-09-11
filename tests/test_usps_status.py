"""USPS status-email reader: the decision core plus the sweep's guards.

Every SUBJ/BODY string here was copied from a real message in Glen's mailbox on
2026-09-10, not written from memory. USPS reuses one subject line ("Expected
Delivery by ...") for label-created, in-transit AND out-for-delivery, so the
subject alone cannot tell a bought label from a moving parcel. The body sentence
is the only discriminator, and that is what these tests pin.

The guard that matters most: a label-created email must NEVER advance an order.
Buying a label is not carrier acceptance, and marking an order shipped before the
parcel moves is the mistake this pillar exists to prevent.
"""

import sqlite3

import pytest

from dashboard import usps_status as US
from dashboard.tracking import init_tracking_schema, record_shipment


# ── Real bodies, one per distinct wording USPS actually sent ──────────────────
LABEL_CREATED = (
    "USPS expects to deliver your package by Monday, September 14, 2026 arriving "
    "by 9:00pm. Tracking Number: 9405530109355451998458 Package Shipped from: "
    "USPS CLICK-N-SHIP")
ARRIVED_ORIGIN = (
    "Your item arrived at our HONOLULU HI DISTRIBUTION CENTER origin facility on "
    "September 3, 2026 at 10:08 pm. The item is currently in transit to the "
    "destination. Tracking Number: 9405530109355412869056")
DEPARTED_FACILITY = (
    "Your item departed our USPS facility in SPOKANE WA DISTRIBUTION CENTER on "
    "September 8, 2026 at 1:17 pm. The item is currently in transit to the "
    "destination. Tracking Number: 9405530109355412869056")
# Acceptance at the counter. USPS says "USPS picked up your item", which the phrase
# table missed until 2026-09-10: "picked up by" covers a shipping partner, not this.
# Real message, parcel accepted in Hilo the afternoon the sync went live.
ACCEPTED_AT_COUNTER = (
    "USPS picked up your item at 1:14 pm on September 10, 2026 in HILO, HI 96720. "
    "Tracking Number: 9405530109355460080243 Package Shipped from: USPS CLICK-N-SHIP")
ARRIVED_POST_OFFICE = (
    "Your item arrived at the Post Office at 12:14 am on September 9, 2026 in "
    "COEUR D ALENE, ID 83815. Tracking Number: 9405530109355412869056")
MOVING_WITHIN_NETWORK = (
    "Your package is moving within the USPS network and is on track to be "
    "delivered by the expected delivery date. As of August 31, 2026 at 4:40 am, "
    "it is currently in transit to the next facility. Tracking Number: "
    "9405530109355413439098")
OUT_FOR_DELIVERY = (
    "Your item is out for delivery on September 9, 2026 at 6:10 am in COEUR D "
    "ALENE, ID 83814. USPS expects to deliver your package today between 7:15am "
    "and 11:15am. Tracking Number: 9405530109355412869056")
DELIVERED_MAILBOX = (
    "Your item was delivered in or at the mailbox at 10:58 am on September 9, "
    "2026 in COEUR D ALENE, ID 83814. Tracking Number: 9405530109355412869056 "
    "Package Shipped from: USPS CLICK-N-SHIP")
DELIVERED_LOCKER = (
    "Your item was delivered to a parcel locker at 2:39 pm on August 31, 2026 in "
    "DAVENPORT, FL 33837. Tracking Number: 9405530109355413439098 Package "
    "Shipped from: USPS CLICK-N-SHIP")

# The one subject USPS reuses for three different states.
SHARED_SUBJECT = ("USPS® Expected Delivery on Wednesday, September 9, 2026 "
                  "arriving by 9:00pm 9405530109355412869056")


@pytest.mark.parametrize("body,expected", [
    (LABEL_CREATED, US.PRE_TRANSIT),
    (ARRIVED_ORIGIN, US.IN_TRANSIT),
    (DEPARTED_FACILITY, US.IN_TRANSIT),
    (ACCEPTED_AT_COUNTER, US.IN_TRANSIT),
    (ARRIVED_POST_OFFICE, US.IN_TRANSIT),
    (MOVING_WITHIN_NETWORK, US.IN_TRANSIT),
    (OUT_FOR_DELIVERY, US.OUT_FOR_DELIVERY),
    (DELIVERED_MAILBOX, US.DELIVERED),
    (DELIVERED_LOCKER, US.DELIVERED),
])
def test_real_bodies_map_to_the_right_status(body, expected):
    assert US.parse_status_email(SHARED_SUBJECT, body)["status"] == expected


def test_acceptance_is_in_transit_not_a_bought_label():
    """This is the signal the PRE_TRANSIT hold exists to wait for: the moment the
    parcel stops being a bought label and starts existing to the network. Until
    2026-09-10 it read as unrecognised, so an order stayed held until some later
    scan happened to arrive. A delay rather than a permanent miss, but the whole
    point of holding a label is to act the instant acceptance lands."""
    got = US.parse_status_email(SHARED_SUBJECT, ACCEPTED_AT_COUNTER)
    assert got["status"] == US.IN_TRANSIT
    assert got["tracking"] == "9405530109355460080243"


def test_a_pickup_phrase_does_not_swallow_the_delivered_state():
    """'picked up' must not outrank an actual delivery. Order matters in the
    classifier and this pins it."""
    body = ("Your item was delivered at 2:39 pm after USPS picked up your item "
            "earlier. Tracking Number: 9405530109355412869056")
    assert US.parse_status_email("USPS®", body)["status"] == US.DELIVERED


def test_out_for_delivery_is_not_read_as_label_created():
    """This body contains 'USPS expects to deliver your package today' too. A
    naive check on that phrase would demote a parcel already on the truck."""
    assert "USPS expects to deliver your package" in OUT_FOR_DELIVERY
    assert US.parse_status_email(SHARED_SUBJECT, OUT_FOR_DELIVERY)["status"] == (
        US.OUT_FOR_DELIVERY)


def test_the_body_number_wins_over_a_complete_subject_number():
    """Precedence, isolated. The subject here carries a different, complete
    number; the body's labelled one must still be the answer. Gmail also
    truncates long subjects mid-number, which is why the subject is a last
    resort rather than a source."""
    other = "USPS® Expected Delivery by Wednesday 9405530109355499999999"
    got = US.parse_status_email(other, ARRIVED_ORIGIN)
    assert got["tracking"] == "9405530109355412869056"


def test_the_subject_is_used_only_when_the_body_has_no_number():
    got = US.parse_status_email(
        "USPS® Item Delivered, In/At Mailbox 9405530109355412869056",
        "Your item was delivered in or at the mailbox at 10:58 am.")
    assert got["tracking"] == "9405530109355412869056"


def test_a_failed_delivery_is_never_read_as_delivered():
    """These wordings never say 'was delivered', so the affirmative pattern must
    not over-match on a bare 'delivered'."""
    for body in [
        "Your item could not be delivered at 9:00 am on September 9, 2026. "
        "Tracking Number: 9405530109355412869056",
        "We attempted to deliver your item at 9:00 am. Tracking Number: "
        "9405530109355412869056",
        "Your item is being returned to sender. Tracking Number: "
        "9405530109355412869056",
    ]:
        assert US.parse_status_email("USPS®", body)["status"] != US.DELIVERED


def test_a_blocker_phrase_beats_an_affirmative_one():
    """Defensive, not observed. USPS has not sent this shape into Glen's mailbox,
    so it pins the blocker list rather than a known message. A parcel handed back
    to the sender must not close the customer's order."""
    body = ("Your item was delivered to the original sender at 3:12 pm on "
            "September 9, 2026 after being returned to sender. Tracking Number: "
            "9405530109355412869056")
    assert "was delivered" in body
    assert US.parse_status_email("USPS®", body)["status"] != US.DELIVERED


def test_an_unrecognised_body_yields_no_status():
    got = US.parse_status_email("USPS® Something New",
                                "Tracking Number: 9405530109355412869056")
    assert got["tracking"] == "9405530109355412869056"
    assert got["status"] is None


def test_no_tracking_number_yields_nothing():
    got = US.parse_status_email("USPS® Newsletter", "Nothing useful in here.")
    assert got["tracking"] is None and got["status"] is None


@pytest.mark.parametrize("seen,expected", [
    ([US.IN_TRANSIT, US.DELIVERED, US.PRE_TRANSIT], US.DELIVERED),
    ([US.PRE_TRANSIT, US.IN_TRANSIT], US.IN_TRANSIT),
    ([US.OUT_FOR_DELIVERY, US.IN_TRANSIT], US.OUT_FOR_DELIVERY),
    ([US.PRE_TRANSIT], US.PRE_TRANSIT),
    ([], None),
])
def test_strongest_wins_regardless_of_arrival_order(seen, expected):
    assert US.strongest(seen) == expected


# ── The sweep ────────────────────────────────────────────────────────────────
TN = "9405530109355412869056"


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Messages:
    """Minimal Gmail stub: one message per (id, subject, body) tuple given."""

    def __init__(self, msgs):
        self.msgs = msgs
        self.queries = []

    def list(self, userId=None, q=None, maxResults=None, pageToken=None):
        self.queries.append(q)
        return _Exec({"messages": [{"id": m[0]} for m in self.msgs]})

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        import base64
        subject, body = next((s, b) for i, s, b in self.msgs if i == id)
        data = base64.urlsafe_b64encode(body.encode()).decode()
        return _Exec({"internalDate": "1789000000000",
                      "payload": {"mimeType": "text/plain",
                                  "body": {"data": data},
                                  "headers": [{"name": "Subject", "value": subject}]}})


class _Users:
    def __init__(self, msgs):
        self._m = _Messages(msgs)

    def messages(self):
        return self._m

    def getProfile(self, userId=None):
        return _Exec({"emailAddress": "drglenswartwout@gmail.com"})


class _Service:
    def __init__(self, msgs):
        self._u = _Users(msgs)

    def users(self):
        return self._u


@pytest.fixture
def cx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "chat_log.db"))
    conn.row_factory = sqlite3.Row
    init_tracking_schema(conn)
    record_shipment(conn, tracking_number=TN, recipient_name="Ashley King",
                    status="sent")
    yield conn
    conn.close()


def _recorder():
    calls = []

    def advance(cx, tracking_code, carrier_status):
        calls.append((tracking_code, carrier_status))
        return 1
    return advance, calls


def test_a_label_created_email_never_advances_an_order(cx):
    """The guard. USPS emails this the moment Rae buys the label, hours or days
    before the parcel is accepted."""
    svc = _Service([("m1", SHARED_SUBJECT, LABEL_CREATED)])
    advance, calls = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert calls == []
    assert out["pre_transit_held"] == 1
    assert out["acted"] == 0


def test_movement_advances_the_order(cx):
    svc = _Service([("m1", SHARED_SUBJECT, DEPARTED_FACILITY)])
    advance, calls = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert calls == [(TN, US.IN_TRANSIT)]
    assert out["acted"] == 1


def test_delivered_wins_over_earlier_scans_in_the_same_window(cx):
    """Several emails per parcel arrive in one window. The sweep must act once,
    on the furthest state, not once per email."""
    svc = _Service([("m1", SHARED_SUBJECT, ARRIVED_ORIGIN),
                    ("m2", SHARED_SUBJECT, OUT_FOR_DELIVERY),
                    ("m3", SHARED_SUBJECT, DELIVERED_MAILBOX)])
    advance, calls = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert calls == [(TN, US.DELIVERED)]
    assert out["emails"] == 3 and out["parcels"] == 1


def test_acted_and_cards_reported_are_counted_separately(cx):
    """A delivered signal returns 1 even when the shipment resolves to no member
    order, which is the common case for a parcel whose order was never linked.
    Observed on prod 2026-09-10: parcel ...869056 was delivered and its shipment
    row exists, but no order carries that tracking number. Collapsing the two
    counts would report an order moving when the board did not change."""
    svc = _Service([("m1", SHARED_SUBJECT, DELIVERED_MAILBOX)])

    def advance_touching_nothing(cx, tracking_code, carrier_status):
        return 0

    out = US.run_status_sweep(cx, svc, days=7, advance=advance_touching_nothing)
    assert out["acted"] == 1
    assert out["cards_reported"] == 0


def test_a_non_numeric_advance_return_does_not_break_the_summary(cx):
    """_advance_orders_by_tracking_status returns an int today. A future change
    must degrade the count, never 500 the cron."""
    svc = _Service([("m1", SHARED_SUBJECT, DELIVERED_MAILBOX)])
    out = US.run_status_sweep(cx, svc, days=7,
                              advance=lambda *a, **k: {"ok": True})
    assert out["acted"] == 1
    assert out["cards_reported"] == 0


def test_dry_run_mutates_nothing(cx):
    svc = _Service([("m1", SHARED_SUBJECT, DELIVERED_MAILBOX)])
    advance, calls = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance, dry_run=True)
    assert calls == []
    assert out["mode"] == "DRY-RUN"
    assert out["would_act"] == 1


def test_a_tracking_number_we_never_shipped_is_counted_not_raised(cx):
    """Rae's mailbox also carries her personal USPS mail. An unknown parcel is
    not an error, and must not reach the order tables."""
    body = DELIVERED_MAILBOX.replace(TN, "9400000000000000000000")
    svc = _Service([("m1", "USPS®", body)])
    advance, calls = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert calls == []
    assert out["unknown_parcels"] == 1


def test_the_sweep_reports_which_mailbox_it_read(cx):
    """The 60-day outage was invisible because a wrong-account token and an empty
    inbox looked identical."""
    svc = _Service([])
    advance, _ = _recorder()
    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert out["mailbox"] == "drglenswartwout@gmail.com"
    assert out["emails"] == 0


def test_the_query_targets_the_usps_status_sender(cx):
    svc = _Service([])
    advance, _ = _recorder()
    US.run_status_sweep(cx, svc, days=3, advance=advance)
    q = svc.users().messages().queries[0]
    assert "tracking.usps.com" in q
    assert "newer_than:3d" in q


def test_an_advance_failure_does_not_abort_the_rest(cx):
    record_shipment(cx, tracking_number="9405530109355413439098",
                    recipient_name="Pam Schreur", status="sent")
    body2 = DELIVERED_LOCKER
    svc = _Service([("m1", SHARED_SUBJECT, DELIVERED_MAILBOX),
                    ("m2", SHARED_SUBJECT, body2)])
    seen = []

    def advance(cx, tracking_code, carrier_status):
        seen.append(tracking_code)
        if tracking_code == TN:
            raise RuntimeError("order table locked")
        return 1

    out = US.run_status_sweep(cx, svc, days=7, advance=advance)
    assert len(seen) == 2
    assert out["errors"] == 1 and out["acted"] == 1
    # A count with no reason is not diagnosable. The first live cron run reported
    # errors=1 and nothing recorded why.
    assert out["first_error"] is not None
    assert TN in out["first_error"]
    assert "order table locked" in out["first_error"]


def test_the_sweep_logs_a_reason_for_every_parcel_it_skips(cx):
    """The endpoint passes a logger so these reach the Render logs. Silence on a
    held or unknown parcel is what makes a quiet sweep unreadable."""
    unknown = DELIVERED_MAILBOX.replace(TN, "9400000000000000000000")
    svc = _Service([("m1", SHARED_SUBJECT, LABEL_CREATED),
                    ("m2", "USPS®", unknown)])
    lines = []
    US.run_status_sweep(cx, svc, days=7, advance=lambda *a, **k: 1,
                        log=lines.append)
    blob = "\n".join(lines)
    assert "held" in blob
    assert "no shipment row" in blob


def test_only_the_first_error_is_kept(cx):
    """first_error is a pointer for a human, not an error log. Two failures must
    not grow the summary."""
    record_shipment(cx, tracking_number="9405530109355413439098",
                    recipient_name="Pam Schreur", status="sent")
    svc = _Service([("m1", SHARED_SUBJECT, DELIVERED_MAILBOX),
                    ("m2", SHARED_SUBJECT, DELIVERED_LOCKER)])

    def always_fails(cx, tracking_code, carrier_status):
        raise RuntimeError("boom " + tracking_code[-4:])

    out = US.run_status_sweep(cx, svc, days=7, advance=always_fails)
    assert out["errors"] == 2
    assert out["first_error"].count("boom") == 1
