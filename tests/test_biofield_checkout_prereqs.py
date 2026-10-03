"""Glen 2026-10-02, two rulings.

1. Every Biofield client finishes the fresh scan, intake and photo before paying,
   "otherwise she pays and 'waits'". The $300 checkout refuses until they are done.
2. The $100 program is the Remedy Match Program: a member month built from the
   Bioenergetic Wellness Scan, with no consultation. Its payment must not record a
   paid Biofield, which unlocked Glen's booking link and the paid-Biofield pricing.
"""
import sqlite3

import app as appmod
from dashboard import biofield_store, client_photos, client_scans, intake, scan_freshness

E = "buyer@x.com"


def _setup(monkeypatch, tmp_path):
    db = str(tmp_path / "log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setenv("BIOFIELD_CHECKOUT_ENABLED", "1")
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", True, raising=False)
    cx = sqlite3.connect(db)
    biofield_store.init_table(cx)
    client_photos.init_table(cx)
    client_scans.init_client_scans_table(cx)
    intake.init_intake_table(cx)
    scan_freshness.init_table(cx)
    cx.commit()
    cx.close()
    cap = {}
    import dashboard.stripe_pay as _sp

    def fake_session(amount_cents, *, customer_email, description, metadata,
                     success_url, cancel_url, save_card=False):
        cap.update(amount=amount_cents, email=customer_email, description=description,
                   metadata=metadata)
        return {"id": "cs_test", "url": "https://stripe/biofield"}
    monkeypatch.setattr(_sp, "create_checkout_session", fake_session)
    monkeypatch.setattr(appmod.stripe_pay, "create_checkout_session", fake_session,
                        raising=False)
    monkeypatch.setattr(appmod, "_ingest_order", lambda **kw: cap.setdefault("order", kw))
    import dashboard.orders as _orders
    monkeypatch.setattr(_orders, "set_order_qbo_lines", lambda *a, **k: None)
    return cap, db


def _prepare_in_portal(db, email=E):
    """The portal's own records: a photo, a submitted intake, a scan today."""
    import datetime as dt
    cx = sqlite3.connect(db)
    client_photos.put(cx, email, b"jpeg", "image/jpeg")
    intake.submit(cx, email, {}, "2026-10-01T00:00:00Z")
    client_scans.upsert_scans(cx, email, [{"scan_date": dt.date.today().isoformat(),
                                           "scan_id": "s1"}])
    cx.commit()
    cx.close()


def test_unprepared_buyer_is_refused_and_nothing_is_charged(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    r = appmod.app.test_client().post("/biofield/checkout", json={"email": E})
    assert r.status_code == 409, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is False and body["reason"] == "prereqs"
    assert body["prereqs"] == {"photo": False, "intake": False, "scan": False, "ready": False}
    assert "amount" not in cap and "order" not in cap


def test_two_of_three_is_still_refused(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    client_photos.put(cx, E, b"jpeg", "image/jpeg")
    intake.submit(cx, E, {}, "2026-10-01T00:00:00Z")
    cx.commit()
    cx.close()
    r = appmod.app.test_client().post("/biofield/checkout", json={"email": E})
    assert r.status_code == 409
    assert r.get_json()["prereqs"]["scan"] is False
    assert "amount" not in cap


def test_buyer_prepared_in_the_portal_can_pay_300(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    r = appmod.app.test_client().post("/biofield/checkout", json={"email": E})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["amount"] == 30000
    assert cap["metadata"]["tier"] == "premium"


def test_readiness_page_posts_no_email_and_the_signed_in_one_is_used(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db, "signed@x.com")
    c = appmod.app.test_client()
    c.set_cookie("rm_biofield_email", "signed@x.com", domain="localhost")
    r = c.post("/biofield/checkout", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["email"] == "signed@x.com"


def test_remedy_match_program_is_not_held_to_the_biofield_steps(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    r = appmod.app.test_client().post("/biofield/checkout",
                                      json={"email": E, "tier": "scalable"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["amount"] == 10000
    assert cap["description"] == "Remedy Match Program"
    assert "Biofield" not in cap["order"]["items"][0]["name"]


def _paid_return(monkeypatch, tier):
    import dashboard.stripe_pay as _sp
    md = {"kind": "biofield", "email": E, "invoice_id": "INVP", "customer_id": "",
          "points_redeemed_cents": "0"}
    if tier is not None:
        md["tier"] = tier
    monkeypatch.setattr(_sp, "get_session", lambda sid: {
        "id": sid, "payment_status": "paid", "payment_intent": "pi_1", "metadata": md})
    monkeypatch.setattr(appmod.stripe_pay, "get_session", _sp.get_session, raising=False)
    monkeypatch.setattr(_sp, "get_payment_intent",
                        lambda pi: {"status": "succeeded", "customer": "", "payment_method": ""})
    monkeypatch.setattr(appmod.stripe_pay, "get_payment_intent", _sp.get_payment_intent,
                        raising=False)
    import dashboard.qbo_billing as _qb
    monkeypatch.setattr(_qb, "record_payment", lambda *a, **k: None)
    monkeypatch.setattr(appmod._bos_orders, "find_order_by_external_ref", lambda cx, ref: None)
    return appmod.app.test_client().get("/begin/checkout-return?session_id=cs_1")


def _paid_at(db):
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    row = biofield_store.get(cx, E)
    cx.close()
    return (row or {}).get("paid_at")


def test_remedy_match_payment_records_no_biofield_and_lands_on_the_receipt(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    r = _paid_return(monkeypatch, "scalable")
    assert r.status_code == 302
    assert "/order-confirmation?session_id=cs_1" in r.headers["Location"]
    assert not _paid_at(db)


def test_biofield_payment_still_records_a_biofield_and_opens_the_gate(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    r = _paid_return(monkeypatch, "premium")
    assert "/biofield/ready" in r.headers["Location"]
    assert _paid_at(db)


def test_a_session_from_before_tiers_is_treated_as_the_300_one(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    r = _paid_return(monkeypatch, None)
    assert "/biofield/ready" in r.headers["Location"]
    assert _paid_at(db)


def test_a_portal_photo_turns_the_readiness_page_photo_green(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    client_photos.put(cx, E, b"jpeg", "image/jpeg")
    cx.commit()
    cx.close()
    c = appmod.app.test_client()
    c.set_cookie("rm_biofield_email", E, domain="localhost")
    st = c.get("/api/biofield/ready").get_json()
    assert st["items"]["photo"]["status"] == "green"
