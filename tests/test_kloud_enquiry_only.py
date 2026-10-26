"""The Kloud mats are enquiry only (Glen, 2026-10-25).

Glen: "enquiry-only. they must be purchased on our distributor store site with Centropix.
We do want to know when someone is interested, and be sure to give them instructions on
how to order, as well as the option of becoming a distributor. We want to follow up with
them in their process and provide support."

Before this, /begin/buy/kloud-pemf-mini took payment of $2,890. An `enquiry_only`
product is refused by the same predicate as `waitlist_only` and `out_of_stock`
(practitioner_portal.waitlist_block), so every buying path that refuses those refuses
it. Its page takes an enquiry instead, which is kept, tells Glen and Rae, and emails the
enquirer how to order on Centropix. Each test reads the real records from
data/products.json and has an orderable control.
"""
import json
import os
import re
import sqlite3

import pytest

import app
from dashboard import cart_store as CS
from dashboard import kloud_enquiry as KE
from dashboard import practitioner_portal as pp
from dashboard import sequences as SEQ

MINI, MAXI = "kloud-pemf-mini", "kloud-pemf-maxi"
KLOUD = (MINI, MAXI)
CONTROL = "scar-silk"
BUNDLE = "test-kloud-bundle"
PAGE = open(os.path.join(os.path.dirname(__file__), "..", "static",
                         "begin-product.html")).read()


def _real():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                       "products.json")))["products"]


def _line(slug):
    return f"{_real()[slug]['name']} is ordered from our Centropix store. Ask about it on its page."


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    products = app._PRODUCTS.setdefault("products", {})
    real = _real()
    for s in (*KLOUD, CONTROL):
        monkeypatch.setitem(products, s, dict(real[s]))
    monkeypatch.setitem(products, BUNDLE, {
        "name": "Test Kloud Bundle", "bundle": True, "price_cents": 300000,
        "bundle_component_slugs": [{"slug": CONTROL, "qty": 1}, {"slug": MINI, "qty": 1}]})
    monkeypatch.setattr(pp.pricing, "_load_catalog", lambda: products)
    return products


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.fixture()
def logdb(monkeypatch, tmp_path):
    path = str(tmp_path / "chat_log.db")
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_PORTAL_CART_ENABLED", True)
    cx = sqlite3.connect(path)
    CS.init_cart_tables(cx)
    cx.close()
    return path


def _refused(r, slug=MINI):
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json() or {}
    assert body.get("error") == _line(slug), body
    assert body.get("url") == f"/begin/product/{slug}", body


# ── No path takes payment ─────────────────────────────────────────────────────

def test_the_catalog_file_marks_both_mats_enquiry_only():
    real = _real()
    assert [s for s, q in real.items() if isinstance(q, dict) and q.get("enquiry_only")] \
        == list(KLOUD)
    for s in KLOUD:
        assert not real[s].get("inactive") and not real[s].get("info_only")


@pytest.mark.parametrize("slug", KLOUD)
def test_the_predicate_refuses_it_with_the_one_line(catalog, slug):
    assert pp.is_orderable(slug, catalog) is False
    msg = pp.waitlist_refusal(pp.waitlist_block(slug, catalog))
    assert msg == _line(slug) and msg.url == f"/begin/product/{slug}"


def test_a_bundle_holding_one_is_refused(catalog):
    assert pp.is_orderable(BUNDLE, catalog) is False
    assert pp.waitlist_refusal(pp.waitlist_block(BUNDLE, catalog)) == _line(MINI)


def test_the_control_is_still_orderable(catalog):
    assert pp.is_orderable(CONTROL, catalog) is True


def test_without_the_flag_it_would_be_sold(catalog, monkeypatch):
    monkeypatch.setitem(catalog, MINI, {k: v for k, v in catalog[MINI].items()
                                        if k != "enquiry_only"})
    assert pp.is_orderable(MINI, catalog) is True


@pytest.mark.parametrize("slug", KLOUD)
def test_price_cart_refuses_it(slug):
    with pytest.raises(app.CheckoutError) as e:
        app._price_cart([{"slug": CONTROL, "qty": 1}, {"slug": slug, "qty": 1}],
                        ship={"country": "US"})
    assert str(e.value) == _line(slug)


def test_a_console_or_invoice_line_is_refused():
    assert app._cart_waitlist_refusal([{"slug": MAXI, "qty": 1}]) == _line(MAXI)


@pytest.mark.parametrize("slug", KLOUD)
def test_begin_checkout_is_refused_before_any_payment(client, monkeypatch, slug):
    calls = []
    for name in ("_stripe_checkout_url_for_reorder", "_stripe_checkout_url_for_order",
                 "_ingest_order"):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, lambda *a, _n=name, **k: calls.append(_n) or "")
    _refused(client.post(f"/begin/checkout/{slug}", json={"email": "a@x.com",
                                                           "method": "card"}), slug)
    assert calls == []


@pytest.mark.parametrize("slug", KLOUD)
def test_the_buy_page_sends_the_buyer_to_the_product_page(client, slug):
    r = client.get(f"/begin/buy/{slug}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/begin/product/{slug}")


def test_cart_add_is_refused(client, logdb):
    _refused(client.post("/api/cart/add", json={"slug": MINI}))


def test_a_biofield_report_never_invoices_or_carts_a_mat():
    from dashboard.biofield_invoice import RECOMMEND_ONLY_SLUGS
    assert set(KLOUD) <= RECOMMEND_ONLY_SLUGS


# ── The page takes an enquiry ─────────────────────────────────────────────────

@pytest.mark.parametrize("slug,model", [(MINI, "mini"), (MAXI, "maxi")])
def test_the_page_data_carries_the_enquiry_and_nothing_that_sells(client, slug, model):
    d = client.get(f"/begin/product-page-data/{slug}").get_json()
    assert d["enquiry"] == {"line": _line(slug), "model": model, "guide_url": KE.GUIDE_URL}
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d and d["autoship_eligible"] is False
    assert not any(isinstance(s, dict) and s.get("id") == "cta" for s in d.get("sections") or [])
    assert "out_of_stock_line" not in d and "waitlist_only" not in d


def test_an_ordinary_page_carries_no_enquiry(client):
    d = client.get(f"/begin/product-page-data/{CONTROL}").get_json()
    assert "enquiry" not in d and d["price_cents"]


def test_the_page_shows_the_form_and_points_every_buy_link_at_it():
    assert 'id="product-enquiry"' in PAGE
    assert re.search(r"var ctaUrl = data\.enquiry \? '#product-enquiry'", PAGE)
    assert "renderEnquiry(data);" in PAGE
    assert "fetch('/api/enquiry/' + encodeURIComponent(slug)" in PAGE
    i = PAGE.index('<form id="product-enquiry"')
    form = PAGE[i:PAGE.index("</form>", i)]
    for field in ("name", "email", "phone", "model", "message", "distributor", "hp_extra"):
        assert f'name="{field}"' in form
    assert "—" not in form


# ── The enquiry ───────────────────────────────────────────────────────────────

@pytest.fixture()
def sent(monkeypatch, logdb):
    out = []
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda to, subject, body, **k: out.append((to, subject, body))
                        or {"id": "m1"})
    app._kloud_enquiry_velocity = app.VelocityLimiter()
    return out


def _post(client, slug=MINI, **kw):
    body = {"name": "Pat Example", "email": "pat@example.com", "phone": "808 555 0100",
            "model": "mini", "message": "Is the Mini enough for two?", "distributor": False}
    body.update(kw)
    return client.post(f"/api/enquiry/{slug}", json=body)


def _rows(logdb):
    cx = sqlite3.connect(logdb)
    cx.row_factory = sqlite3.Row
    KE.init_table(cx)
    try:
        return [dict(r) for r in cx.execute("SELECT * FROM kloud_enquiries ORDER BY id")]
    finally:
        cx.close()


def test_an_enquiry_is_kept_and_emails_the_enquirer_and_both_staff(client, sent, logdb):
    r = _post(client)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    [row] = _rows(logdb)
    assert row["email"] == "pat@example.com" and row["model"] == "mini"
    assert row["replied"] == 1 and row["distributor"] == 0
    assert [t for t, _s, _b in sent] == ["pat@example.com", *KE.NOTIFY]
    _to, subject, body = sent[0]
    assert subject == "How to order your Kloud PEMF mat"
    assert body.startswith("Aloha Pat,")
    assert KE.STORE_URL in body and KE.GUIDE_URL in body and KE.FINANCING_URL in body
    assert "$2,890" in body and "$3,590" in body
    assert "If that interests you, reply" in body
    for _t, s, b in sent[1:]:
        assert s == "Kloud enquiry: Pat Example (Kloud Mini)"
        assert "Is the Mini enough for two?" in b and "808 555 0100" in b
        assert "They were emailed the ordering steps." in b
        assert "not switched on yet" in b


def test_the_prices_come_from_the_catalog(client, sent, catalog, monkeypatch):
    monkeypatch.setitem(catalog, MAXI, dict(catalog[MAXI], price_cents=370000))
    _post(client)
    assert "$3,700" in sent[0][2] and "$3,590" not in sent[0][2]


def test_a_distributor_enquiry_is_promised_a_reply(client, sent):
    _post(client, distributor=True)
    assert "I will write to you about how it works." in sent[0][2]
    assert "Interested in becoming a distributor: Yes" in sent[1][2]


def test_a_repeat_within_a_day_tells_staff_but_does_not_email_again(client, sent, logdb):
    _post(client)
    sent.clear()
    _post(client, message="One more question.")
    assert [t for t, _s, _b in sent] == list(KE.NOTIFY)
    assert "They were not emailed again" in sent[0][2]
    assert [r["replied"] for r in _rows(logdb)] == [1, 0]


def test_a_failed_ordering_email_is_reported_and_can_be_sent_later(client, logdb, monkeypatch):
    out = []

    def send(to, subject, body, **k):
        if to == "pat@example.com":
            raise RuntimeError("gmail down")
        out.append((to, body))
        return {"id": "m1"}
    monkeypatch.setattr(app._inbox, "send_email", send)
    app._kloud_enquiry_velocity = app.VelocityLimiter()
    assert _post(client).status_code == 200
    assert all("The ordering email to them was not sent." in b for _t, b in out)
    assert len(out) == 2
    assert _rows(logdb)[0]["replied"] == 0


def test_follow_ups_enroll_only_while_the_sequence_is_on(client, sent, logdb):
    cx = sqlite3.connect(logdb)
    SEQ.init_tables(cx)
    cx.execute("INSERT INTO sequences(slug,name,trigger_kind,active) VALUES (?,?,?,0)",
               (KE.SEQUENCE_SLUG, "Kloud Enquiry Follow-Up", "manual"))
    cx.commit()
    _post(client)
    assert cx.execute("SELECT COUNT(*) FROM sequence_enrollments").fetchone()[0] == 0
    cx.execute("UPDATE sequences SET active=1 WHERE slug=?", (KE.SEQUENCE_SLUG,))
    cx.commit()
    sent.clear()
    _post(client, email="lee@example.com", name="Lee")
    assert cx.execute("SELECT slug, email FROM sequence_enrollments").fetchall() == [
        (KE.SEQUENCE_SLUG, "lee@example.com")]
    assert "Follow-up emails go to them on days 3 and 10." in sent[1][2]
    cx.close()


@pytest.mark.parametrize("kw,code", [({"email": "nope"}, 400),
                                     ({"email": "a@x.com,b@y.com"}, 400),
                                     ({"name": "  "}, 400)])
def test_a_bad_enquiry_is_refused_and_sends_nothing(client, sent, logdb, kw, code):
    assert _post(client, **kw).status_code == code
    assert sent == []


def test_a_bot_is_dropped_quietly(client, sent, logdb):
    assert _post(client, hp_extra="Acme").status_code == 200
    assert sent == [] and _rows(logdb) == []


def test_only_an_enquiry_only_product_takes_one(client, sent, logdb):
    assert _post(client, slug=CONTROL).status_code == 404
    assert sent == []


def test_a_visitor_is_rate_limited(client, sent, logdb):
    codes = [_post(client, email=f"p{i}@example.com").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_a_value_cannot_add_a_line_to_the_notice():
    assert KE.clean("Pat\r\nBcc: x@y.com", 120) == "Pat Bcc: x@y.com"


def test_the_emails_have_no_dashes_or_capitals():
    for distributor in (True, False):
        _s, body = KE.reply_text(first_name="Pat", model="maxi", distributor=distributor,
                                 mini_cents=289000, maxi_cents=359000)
        assert "—" not in body and "\n\n\n" not in body
    _s, body = KE.notice_text(enquiry_id=1, email="a@x.com", name="", phone="",
                              model="unsure", distributor=False, message="", page_slug=MINI,
                              reply_status="failed", follow_ups_on=False)
    assert "—" not in body and "FAILED" not in body and "Not sure yet" in body


# ── Review round 1 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", KLOUD)
def test_the_chat_gives_the_ordering_steps_not_a_refusal(slug):
    hint = app._alias_price_hint(_real()[slug]["name"], slug)
    assert KE.GUIDE_URL in hint and "Centropix" in hint
    assert "cannot be ordered" not in hint and "no order step" not in hint


def test_the_chat_still_refuses_an_out_of_stock_product(catalog, monkeypatch):
    monkeypatch.setitem(catalog, CONTROL, dict(catalog[CONTROL], out_of_stock="Soon."))
    assert "cannot be ordered now" in app._alias_price_hint("Scar Silk", CONTROL)


@pytest.mark.parametrize("name,first", [
    ("Pat Example", "Pat"), ("José", "José"), ("Mary-Kay O'Neil", "Mary-Kay"),
    ("www.scam.example/claim", ""), ("http://x.y", ""), ("Pat123", ""),
    ("A" * 31, ""), ("", "")])
def test_the_greeting_echoes_only_a_plain_first_name(name, first):
    assert KE.greeting_name(name) == first


def test_a_web_address_as_a_name_is_not_echoed(client, sent):
    _post(client, name="www.scam.example/claim now")
    assert sent[0][2].startswith("Aloha,\n")
    assert "scam" not in sent[0][2]


def test_a_daily_cap_stops_ordering_emails_but_still_tells_staff(client, sent, logdb,
                                                                  monkeypatch):
    monkeypatch.setattr(KE, "DAILY_REPLY_CAP", 2)
    monkeypatch.setattr(app, "_kloud_enquiry_velocity", app.VelocityLimiter())
    _post(client, email="a1@example.com")
    _post(client, email="a2@example.com")
    sent.clear()
    monkeypatch.setattr(app, "_kloud_enquiry_velocity", app.VelocityLimiter())
    _post(client, email="a3@example.com")
    assert [t for t, _s, _b in sent] == list(KE.NOTIFY)
    assert "today's limit on ordering emails was reached" in sent[0][2]


def test_a_skipped_send_is_not_reported_as_sent(client, logdb, monkeypatch):
    out = []
    monkeypatch.setattr(app._inbox, "send_email", lambda to, s, b, **k: (
        out.append((to, b)) or ({"skipped": "suppressed"} if to == "pat@example.com"
                                else {"id": "m1"})))
    monkeypatch.setattr(app, "_kloud_enquiry_velocity", app.VelocityLimiter())
    _post(client)
    assert "was not sent" in out[1][1] and _rows(logdb)[0]["replied"] == 0


@pytest.mark.parametrize("slug", KLOUD)
def test_a_recommendation_list_shows_the_centropix_line_not_not_ready(slug):
    _keep, nr = app._split_not_ready([{"slug": slug, "qty": 1}])
    assert nr == [{"name": _real()[slug]["name"], "url": f"/begin/product/{slug}",
                   "line": _line(slug)}]


@pytest.mark.parametrize("kw", [{"email": "a" * 250 + "@x.co"}])
def test_an_overlong_email_is_refused(client, sent, kw):
    assert _post(client, **kw).status_code == 400 and sent == []


def test_an_inactive_mat_takes_no_enquiry(client, sent, catalog, monkeypatch):
    monkeypatch.setitem(catalog, MINI, dict(catalog[MINI], inactive=True))
    assert _post(client).status_code == 404 and sent == []


def test_an_unknown_model_falls_back_to_the_page_model(client, sent, logdb):
    _post(client, slug=MAXI, model="<script>")
    assert _rows(logdb)[0]["model"] == "maxi"


def test_only_a_true_distributor_tick_counts(client, sent, logdb):
    _post(client, distributor="yes")
    assert _rows(logdb)[0]["distributor"] == 0


def test_the_message_is_capped_and_stripped(client, sent, logdb):
    _post(client, message="hi\x00there" + "x" * 3000)
    m = _rows(logdb)[0]["message"]
    assert len(m) == KE.MAX_MESSAGE and "\x00" not in m


def test_the_name_is_cleaned_in_the_route(client, sent, logdb):
    _post(client, name="Pat\r\nBcc: x@y.com")
    assert _rows(logdb)[0]["name"] == "Pat Bcc: x@y.com"
