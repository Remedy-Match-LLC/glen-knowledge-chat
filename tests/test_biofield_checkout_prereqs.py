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
    monkeypatch.setattr(_sp, "sessions_for_email", lambda email, since, **k: iter([]))
    monkeypatch.setattr(_sp, "expire_session", lambda sid: cap.setdefault("expired", []).append(sid))
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
    assert "prereqs" not in body, "a refusal must not reveal another person's progress"
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
    r = c.post("/biofield/checkout", json={"signed_in": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["email"] == "signed@x.com"


def test_a_post_with_no_email_and_no_flag_never_uses_the_cookie(monkeypatch, tmp_path):
    """Round 1 review: the portal offer button posts {}. With a caregiver's or Rae's
    cookie on the browser, a bare fallback would have charged them instead."""
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db, "cookie@x.com")
    c = appmod.app.test_client()
    c.set_cookie("rm_biofield_email", "cookie@x.com", domain="localhost")
    r = c.post("/biofield/checkout?token=someone-else", json={})
    assert r.status_code == 400
    assert "amount" not in cap


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


def test_steps_confirmed_on_the_readiness_page_open_the_checkout(monkeypatch, tmp_path):
    """End to end through the app's own connections. A dict-only read of the readiness
    record failed silently there, so these confirmations were ignored at checkout."""
    cap, db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    biofield_store.set_photo_on_file(cx, E, "x.jpg")
    cx.close()
    c = appmod.app.test_client()
    c.set_cookie("rm_biofield_email", E, domain="localhost")
    assert c.post("/biofield/checkout", json={"signed_in": True}).status_code == 409
    for item in ("scan", "intake"):
        assert c.post("/api/biofield/confirm", json={"item": item}).status_code == 200
    st = c.get("/api/biofield/ready").get_json()
    assert all(st["items"][k]["status"] == "green" for k in ("photo", "intake", "scan"))
    r = c.post("/biofield/checkout", json={"signed_in": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["amount"] == 30000


def test_the_portal_data_marks_a_paid_client_paid(monkeypatch, tmp_path):
    """Round 3 review: if `paid` were lost, a paid client would be offered payment again."""
    from dashboard import portal_view
    cap, db = _setup(monkeypatch, tmp_path)
    cx = sqlite3.connect(db)
    assert portal_view._biofield_prereqs_block(cx, E)["paid"] is False
    biofield_store.seed_paid(cx, E, via="stripe", order_ref="INVB")
    assert portal_view._biofield_prereqs_block(cx, E)["paid"] is True
    cx.close()


# ── Glen 2026-10-02: the same program is not paid twice inside 21 days ──────────

def _paid(meta, sid="cs_old", status="complete", payment_status="paid"):
    return {"id": sid, "status": status, "payment_status": payment_status, "metadata": meta}


def _stripe(monkeypatch, sessions, seen=None):
    import dashboard.stripe_pay as _sp

    def fake(email, since, **k):
        if seen is not None:
            seen.append((email, since))
        for s in sessions:
            if isinstance(s, Exception):
                raise s
            yield s
    monkeypatch.setattr(_sp, "sessions_for_email", fake)


def _post(json):
    return appmod.app.test_client().post("/biofield/checkout", json=json)


def test_a_second_300_inside_21_days_is_refused(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": "Buyer@X.com ", "tier": "premium"})])
    r = _post({"email": E})
    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.get_json()["reason"] == "repeat"
    assert "amount" not in cap and "order" not in cap


def test_a_second_100_inside_21_days_is_refused(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E, "tier": "scalable"})])
    r = _post({"email": E, "tier": "scalable"})
    assert r.status_code == 409
    assert r.get_json()["reason"] == "repeat"
    assert "amount" not in cap


def test_an_untiered_session_counts_as_the_300(monkeypatch, tmp_path):
    """Sessions from before the $100 tier carry no tier and were all the $300."""
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E})])
    r = _post({"email": E})
    assert r.status_code == 409
    assert r.get_json()["reason"] == "repeat"
    assert "amount" not in cap


def test_a_100_buyer_can_still_move_up_to_the_300(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E, "tier": "scalable"})])
    r = _post({"email": E})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["amount"] == 30000


def test_unpaid_other_people_and_other_kinds_do_not_count(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, payment_status="unpaid",
              status="expired"),
        _paid({"kind": "biofield", "email": "other@x.com", "tier": "premium"}),
        _paid({"kind": "reorder", "email": E})])
    r = _post({"email": E})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_stripe_is_asked_for_this_email_over_22_days(monkeypatch, tmp_path):
    """21 days, plus one: a session can be paid up to a day after it was created."""
    import time
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    seen = []
    _stripe(monkeypatch, [], seen)
    _post({"email": E})
    assert seen[0][0] == E
    assert abs((time.time() - seen[0][1]) - 22 * 86400) < 60


def test_stripe_unreadable_lets_the_checkout_proceed(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [RuntimeError("stripe down")])
    assert _post({"email": E}).status_code == 200


def test_a_match_before_a_failed_page_still_refuses(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E, "tier": "premium"}),
                          RuntimeError("page 2 failed")])
    assert _post({"email": E}).status_code == 409


def test_a_new_checkout_expires_the_other_open_one(monkeypatch, tmp_path):
    """Two tabs: neither was paid when the other opened. The older one is closed."""
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    _stripe(monkeypatch, [
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, sid="cs_tab1",
              status="open", payment_status="unpaid"),
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, sid="cs_test",
              status="open", payment_status="unpaid"),
        _paid({"kind": "biofield", "email": E, "tier": "scalable"}, sid="cs_100",
              status="open", payment_status="unpaid"),
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, sid="cs_gone",
              status="expired", payment_status="unpaid")])
    r = _post({"email": E})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["expired"] == ["cs_tab1"]


def test_an_expiry_failure_does_not_break_the_checkout(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    import dashboard.stripe_pay as _sp
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E, "tier": "premium"},
                                sid="cs_tab1", status="open", payment_status="unpaid")])

    def boom(sid):
        raise RuntimeError("expire failed")
    monkeypatch.setattr(_sp, "expire_session", boom)
    r = _post({"email": E})
    assert r.status_code == 200 and r.get_json()["stripe_url"]


def test_sessions_for_email_filters_on_stripe_and_pages(monkeypatch):
    import dashboard.stripe_pay as _sp
    calls = []
    pages = [{"data": [{"id": "a", "payment_status": "paid", "status": "complete",
                        "metadata": {"k": 1}},
                       {"id": "b", "payment_status": "unpaid", "status": "open"}],
              "has_more": True},
             {"data": [{"id": "c", "payment_status": "paid"}], "has_more": False}]

    def fake_get(path, **k):
        calls.append(path)
        return pages[len(calls) - 1]
    monkeypatch.setattr(_sp, "_get", fake_get)
    out = list(_sp.sessions_for_email("a+b@x.com", 1700000000))
    assert [s["id"] for s in out] == ["a", "b", "c"]
    assert out[0]["metadata"] == {"k": 1}, "the guard reads kind, email and tier from here"
    assert out[1]["status"] == "open" and out[1]["payment_status"] == "unpaid"
    assert "created%5Bgte%5D=1700000000" in calls[0]
    assert "customer_details%5Bemail%5D=a%2Bb%40x.com" in calls[0]
    assert "starting_after=b" in calls[1]


def test_sessions_for_email_refuses_a_truncated_read(monkeypatch):
    import pytest
    import dashboard.stripe_pay as _sp
    calls = []

    def fake_get(path, **k):
        calls.append(path)
        return {"data": [{"id": "x", "payment_status": "paid"}], "has_more": True}
    monkeypatch.setattr(_sp, "_get", fake_get)
    with pytest.raises(RuntimeError):
        list(_sp.sessions_for_email(E, 0, max_pages=2))
    assert len(calls) == 2


def test_sessions_for_email_stops_on_an_empty_page(monkeypatch):
    import dashboard.stripe_pay as _sp
    monkeypatch.setattr(_sp, "_get", lambda path, **k: {"data": [], "has_more": True})
    assert list(_sp.sessions_for_email(E, 0, max_pages=2)) == []


def _seen_open(monkeypatch, sessions, seen):
    import dashboard.stripe_pay as _sp

    def fake(email, since, **k):
        seen.append(since)
        for s in sessions:
            if isinstance(s, Exception):
                raise s
            yield s
    monkeypatch.setattr(_sp, "sessions_for_email", fake)


def test_the_expiry_reads_two_days_and_the_buyers_own_tier(monkeypatch, tmp_path):
    import time
    cap, db = _setup(monkeypatch, tmp_path)
    seen = []
    _seen_open(monkeypatch, [
        _paid({"kind": "biofield", "email": E, "tier": "scalable"}, sid="cs_100_tab",
              status="open", payment_status="unpaid"),
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, sid="cs_300_tab",
              status="open", payment_status="unpaid"),
        _paid({"kind": "biofield", "email": E, "tier": "scalable"}, sid=None,
              status="open", payment_status="unpaid")], seen)
    r = _post({"email": E, "tier": "scalable"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["expired"] == ["cs_100_tab"]
    assert abs((time.time() - seen[-1]) - 2 * 86400) < 60


def test_a_failed_later_page_still_expires_the_earlier_tab(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    seen = []
    _seen_open(monkeypatch, [
        _paid({"kind": "biofield", "email": E, "tier": "premium"}, sid="cs_tab1",
              status="open", payment_status="unpaid"),
        RuntimeError("page 2 failed")], seen)
    r = _post({"email": E})
    assert r.status_code == 200
    assert cap.get("expired") == ["cs_tab1"]


def test_no_expiry_when_the_new_checkout_did_not_open(monkeypatch, tmp_path):
    cap, db = _setup(monkeypatch, tmp_path)
    _prepare_in_portal(db)
    import dashboard.stripe_pay as _sp
    _stripe(monkeypatch, [_paid({"kind": "biofield", "email": E, "tier": "premium"},
                                sid="cs_tab1", status="open", payment_status="unpaid")])
    monkeypatch.setattr(_sp, "create_checkout_session", lambda *a, **k: {"id": "cs_x", "url": ""})
    monkeypatch.setattr(appmod.stripe_pay, "create_checkout_session",
                        lambda *a, **k: {"id": "cs_x", "url": ""}, raising=False)
    _post({"email": E})
    assert "expired" not in cap
