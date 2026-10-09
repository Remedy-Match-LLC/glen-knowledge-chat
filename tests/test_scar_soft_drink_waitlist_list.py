"""The second waiting list: Scar Soft Drink (release 1, 2026-10-09).

Spec: production/05 Formulations/_store-updates/2026-10-09-scar-reduction-program.md,
section 2. Glen approved the five Scar texts ("other 5 are fine") and the consent ("yes"),
and renamed the program "Scar Support Program" the same day. Retina Renew keeps its words
except the consent ("fix Retina Renew also"), and rows stored before the release keep the
consent they agreed to.
"""
import json
import sqlite3

import pytest

import app
from dashboard import ghl_queue as gq
from dashboard import product_waitlist as pw

SCAR, SCAR_TAG = "scar-soft-drink", "scar-soft-drink-waitlist"
RETINA, RETINA_TAG = "neuro-magnesium", "retina-renew-waitlist"
SCAR_WORDS = {
    "consent": ("We will email you when Scar Soft Drink and the new Scar Support Program bundles "
                "are ready to order. Joining this list does not add you to any other mailing list."),
    "intro": ("Scar Soft Drink is not ready to order yet. Join the waiting list and we will email "
              "you when Scar Soft Drink and the new Scar Support Program bundles are ready. "
              "No card, no charge."),
    "subject": "Confirm your Scar Soft Drink email",
    "body_lead": ("Please confirm you'd like an email when Scar Soft Drink and the new Scar Support "
                  "Program bundles are ready to order:"),
    "page_title": "Confirm your Scar Soft Drink email",
    "success": ("We'll email you when Scar Soft Drink and the new Scar Support Program bundles "
                "are ready."),
}
OLD_RETINA_CONSENT = "We will email you when Retina Renew launches, and nothing else unless you ask."


def _people(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "email TEXT UNIQUE, name TEXT DEFAULT '', phone TEXT DEFAULT '', "
               "source TEXT DEFAULT '', tags TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT)")


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _people(c)
    pw.init_table(c)
    gq.init_ghl_queue_table(c)
    return c


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app._init_people_table()
    from dashboard.chat_limits import VelocityLimiter
    monkeypatch.setattr(app, "_waitlist_velocity", VelocityLimiter())
    # The founding presale is OFF: Scar's list must not depend on it.
    monkeypatch.setattr(app, "_founding_enabled", lambda: False)
    products = app._PRODUCTS.setdefault("products", {})
    rec = dict(products.get(SCAR) or {"name": "Scar Soft Drink", "price_cents": 7000})
    rec["waitlist_only"] = True
    monkeypatch.setitem(products, SCAR, rec)
    sent = []
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda to, subject, body, **k: sent.append((to, subject, body)) or {"ok": True})
    app.app.config["TESTING"] = True
    return app.app.test_client(), sent


def test_the_scar_texts_are_glens_words_verbatim():
    t = pw.TEXTS[SCAR]
    for k, v in SCAR_WORDS.items():
        assert t[k] == v, k
    assert t["reserve_line"] == ""
    assert pw.LISTS[SCAR] == SCAR_TAG


def test_no_scar_text_names_another_product_or_the_old_program_name():
    for v in pw.TEXTS[SCAR].values():
        assert "Retina" not in v and "Scar Reduction" not in v


# ── The page ──────────────────────────────────────────────────────────────────

def test_the_scar_page_data_sells_nothing_and_carries_its_list(client):
    c, _ = client
    d = c.get(f"/begin/product-page-data/{SCAR}").get_json()
    assert d["waitlist_only"] is True
    assert d["price"] == "" and d["price_cents"] is None
    assert "cta_url" not in d and "qty_pricing" not in d
    assert not any(s.get("id") == "cta" for s in d.get("sections") or [])
    assert d["waitlist"] == {"slug": SCAR, "intro": SCAR_WORDS["intro"],
                             "consent": SCAR_WORDS["consent"], "reserve_line": "",
                             "success": SCAR_WORDS["success"]}
    assert "Retina Renew" not in json.dumps(d)


def test_the_product_data_endpoint_sells_nothing(client):
    c, _ = client
    d = c.get(f"/begin/product-data/{SCAR}").get_json()
    assert d["waitlist_only"] is True and d["price"] == "" and d.get("qty_pricing") is None
    assert d["waitlist"]["intro"] == SCAR_WORDS["intro"]
    assert "Retina Renew" not in json.dumps(d)


def test_the_page_hides_the_cart_for_a_waitlist_only_product():
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / "static" / "begin-product.html").read_text()
    assert "if (!data.waitlist_only) { renderCartControl(slug); }" in page
    assert "data.waitlist_only ? '#product-waitlist'" in page


def test_the_retina_list_still_needs_its_presale(client, monkeypatch):
    c, _ = client
    assert c.post(f"/api/waitlist/{RETINA}", json={"email": "r@x.com"}).status_code == 404


# ── Sign-up, email and confirmation ───────────────────────────────────────────

def test_a_scar_sign_up_sends_the_scar_email(client):
    c, sent = client
    assert c.post(f"/api/waitlist/{SCAR}", json={"email": "s@x.com", "first_name": "Sam"}).status_code == 200
    to, subject, body = sent[0]
    assert subject == SCAR_WORDS["subject"]
    assert body.startswith(f"Hi Sam,\n\n{SCAR_WORDS['body_lead']}\n")
    assert body.rstrip().endswith(
        "If you didn't ask for this, ignore this email and nothing more will be sent.\n\n"
        "Dr. Glen Swartwout")
    assert "Retina" not in subject + body
    with sqlite3.connect(app.LOG_DB) as db:
        assert db.execute("SELECT consent_text FROM product_waitlist WHERE email='s@x.com'"
                          ).fetchone()[0] == SCAR_WORDS["consent"]


def test_a_double_submit_sends_one_email(client):
    c, sent = client
    for _ in range(2):
        c.post(f"/api/waitlist/{SCAR}", json={"email": "d@x.com"})
    assert len(sent) == 1


def test_the_confirm_page_names_the_tokens_product(client):
    c, sent = client
    c.post(f"/api/waitlist/{SCAR}", json={"email": "p@x.com"})
    token = [ln for ln in sent[0][2].splitlines() if "/begin/waitlist/confirm/" in ln][0].rsplit("/", 1)[1]
    page = c.get(f"/begin/waitlist/confirm/{token}").get_data(as_text=True)
    assert f"<title>{SCAR_WORDS['page_title']}</title>" in page
    assert f"<h1>{SCAR_WORDS['page_title']}</h1>" in page
    assert "Retina" not in page
    r = c.post(f"/begin/waitlist/confirm/{token}")
    assert r.headers["Location"].endswith(f"/begin/product/{SCAR}?waitlist=confirmed")


@pytest.mark.parametrize("method", ["get", "post"])
def test_an_unknown_token_names_no_product(client, method):
    c, _ = client
    r = getattr(c, method)("/begin/waitlist/confirm/not-a-real-token")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "That link has expired or is not valid" in page and 'href="/"' in page
    assert "Retina" not in page and "Scar" not in page


# ── Two lists, kept apart ─────────────────────────────────────────────────────

def test_one_address_on_both_lists_keeps_two_rows_with_their_own_consent(cx):
    pw.sign_up(cx, RETINA, "both@x.com")
    pw.sign_up(cx, SCAR, "both@x.com")
    rows = dict(cx.execute("SELECT product_slug, consent_text FROM product_waitlist "
                           "WHERE email='both@x.com'").fetchall())
    assert rows == {RETINA: pw.TEXTS[RETINA]["consent"], SCAR: SCAR_WORDS["consent"]}


def test_a_scar_token_confirms_only_the_scar_row_and_queues_only_its_tag(cx):
    _, rt = pw.sign_up(cx, RETINA, "both@x.com")
    _, st = pw.sign_up(cx, SCAR, "both@x.com")
    assert pw.confirm(cx, st) == SCAR
    confirmed = dict(cx.execute("SELECT product_slug, COALESCE(confirmed_at,'') "
                                "FROM product_waitlist").fetchall())
    assert confirmed[SCAR] != "" and confirmed[RETINA] == ""
    payloads = [json.loads(r[0]) for r in cx.execute(
        "SELECT payload_json FROM ghl_write_queue WHERE op='tag_add'")]
    assert payloads == [{"tags": [SCAR_TAG]}]


def test_a_retina_row_from_before_the_release_keeps_its_consent_and_confirms(cx):
    _, token = pw.sign_up(cx, RETINA, "early@x.com")
    cx.execute("UPDATE product_waitlist SET consent_text=? WHERE email='early@x.com'",
               (OLD_RETINA_CONSENT,))
    cx.commit()
    assert pw.confirm(cx, token) == RETINA
    assert cx.execute("SELECT consent_text FROM product_waitlist WHERE email='early@x.com'"
                      ).fetchone()[0] == OLD_RETINA_CONSENT
    assert pw.TEXTS[RETINA]["consent"] != OLD_RETINA_CONSENT


def test_scar_sign_ups_do_not_use_retinas_daily_allowance(cx, monkeypatch):
    monkeypatch.setattr(pw, "DAILY_SENDS", 2)
    assert pw.sign_up(cx, SCAR, "a1@x.com")[0] == "new"
    assert pw.sign_up(cx, SCAR, "a2@x.com")[0] == "new"
    assert pw.sign_up(cx, SCAR, "a3@x.com") == ("capped", None)
    assert pw.sign_up(cx, RETINA, "b1@x.com")[0] == "new"
