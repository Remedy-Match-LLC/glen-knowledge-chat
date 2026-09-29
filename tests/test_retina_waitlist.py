"""A free waiting list for Retina Renew on the Neuro-Magnesium presale page.

Production's brief (production/05 Formulations/retina-renew-neuro-magnesium/2026-09-28/
waitlist-brief.md). Glen, 2026-09-28: a free sign-up with no card, below the Founding Batch
reservation; "yes in GHL for now, but make sure we are mirroring that in house". The form's
words are Glen's (approved 2026-09-29, "when" rather than "once, when"). The sign-up consents to
the launch email only: it never adds the general opt-in. Reservers are left out at send time."""
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dashboard import product_waitlist as pw
from dashboard import ghl_queue as gq
from dashboard import subscriptions as subs

ROOT = Path(__file__).resolve().parent.parent
SLUG, TAG = "neuro-magnesium", "retina-renew-waitlist"
CONSENT = "We will email you when Retina Renew launches, and nothing else unless you ask."


def _people(cx):
    cx.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, "
               "name TEXT DEFAULT '', phone TEXT DEFAULT '', source TEXT DEFAULT '', tags TEXT DEFAULT '[]', "
               "created_at TEXT, updated_at TEXT)")


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _people(c)
    pw.init_table(c)
    gq.init_ghl_queue_table(c)
    return c


def _tags(cx, email):
    row = cx.execute("SELECT tags FROM people WHERE email=?", (email,)).fetchone()
    return json.loads(row[0] or "[]") if row else None


def _queued(cx):
    return [(r["email"], json.loads(r["payload_json"]))
            for r in cx.execute("SELECT email, payload_json FROM ghl_write_queue WHERE op='tag_add'")]


def test_the_consent_line_is_glens_approved_wording():
    assert pw.CONSENT_TEXT == CONSENT
    assert pw.LISTS == {SLUG: TAG}


def test_a_sign_up_is_kept_in_house_tagged_and_mirrored_once(cx):
    assert pw.sign_up(cx, SLUG, " Ann@X.com ", first_name="Ann") == "new"
    row = cx.execute("SELECT * FROM product_waitlist").fetchone()
    assert (row["email"], row["first_name"], row["consent_text"]) == ("ann@x.com", "Ann", CONSENT)
    assert TAG in _tags(cx, "ann@x.com")
    assert "consent:opted-in" not in _tags(cx, "ann@x.com")      # the launch email only
    assert _queued(cx) == [("ann@x.com", {"tags": [TAG]})]


def test_signing_up_twice_is_one_sign_up_and_keeps_other_tags(cx):
    cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES "
               "('bo@x.com', '[\"vip\"]', 't', 't')")
    pw.sign_up(cx, SLUG, "bo@x.com")
    assert pw.sign_up(cx, SLUG, "BO@x.com") == "existing"
    assert cx.execute("SELECT COUNT(*) FROM product_waitlist").fetchone()[0] == 1
    assert _tags(cx, "bo@x.com") == ["vip", TAG]
    assert len(_queued(cx)) == 1


def test_a_reserver_is_left_out_of_the_launch_email(cx):
    subs.init_subscriptions_table(cx)
    subs.migrate_add_founding_columns(cx)
    for e in ("wait@x.com", "res@x.com", "gone@x.com"):
        pw.sign_up(cx, SLUG, e)
    subs.create_founding_reservation(cx, email="res@x.com", stripe_customer_id="c",
                                     stripe_payment_method_id="p", items=[], ship_address={},
                                     founding_slug=SLUG)
    rid = subs.create_founding_reservation(cx, email="gone@x.com", stripe_customer_id="c2",
                                           stripe_payment_method_id="p2", items=[], ship_address={},
                                           founding_slug=SLUG)
    cx.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (rid,))
    cx.commit()
    assert sorted(w["email"] for w in pw.waiters_to_email(cx, SLUG)) == ["gone@x.com", "wait@x.com"]
    pw.mark_emailed(cx, SLUG, "wait@x.com")
    assert [w["email"] for w in pw.waiters_to_email(cx, SLUG)] == ["gone@x.com"]


def test_the_counts_show_in_house_and_queued_side_by_side(cx):
    pw.sign_up(cx, SLUG, "a@x.com")
    pw.sign_up(cx, SLUG, "b@x.com")
    assert pw.counts(cx, SLUG) == {"signed_up": 2, "tagged_in_house": 2, "ghl_queued": 2,
                                   "emailed": 0}


def test_an_unknown_list_is_refused(cx):
    with pytest.raises(ValueError):
        pw.sign_up(cx, "vitality", "a@x.com")


# ── The route ─────────────────────────────────────────────────────────────────
def _app():
    import importlib
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("app")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"app not importable in this env: {e}")


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = _app()
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    app._init_people_table()
    from dashboard.chat_limits import VelocityLimiter
    monkeypatch.setattr(app, "_waitlist_velocity", VelocityLimiter())
    app.app.config["TESTING"] = True
    return app.app.test_client(), app


def test_the_route_signs_up(client):
    c, app = client
    r = c.post(f"/api/waitlist/{SLUG}", json={"email": "cy@x.com", "first_name": "Cy"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    with sqlite3.connect(app.LOG_DB) as cx:
        assert cx.execute("SELECT consent_text FROM product_waitlist WHERE email='cy@x.com'").fetchone()[0] == CONSENT


def test_the_route_drops_a_bot_and_refuses_bad_input(client):
    c, app = client
    assert c.post(f"/api/waitlist/{SLUG}", json={"email": "bot@x.com", "company": "x"}).status_code == 200
    assert c.post(f"/api/waitlist/{SLUG}", json={"email": "not-an-email"}).status_code == 400
    assert c.post("/api/waitlist/vitality", json={"email": "d@x.com"}).status_code == 404
    with sqlite3.connect(app.LOG_DB) as cx:
        try:
            n = cx.execute("SELECT COUNT(*) FROM product_waitlist").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
    assert n == 0


def test_the_route_limits_one_visitor(client):
    c, app = client
    codes = [c.post(f"/api/waitlist/{SLUG}", json={"email": f"e{i}@x.com"}).status_code
             for i in range(8)]
    assert 429 in codes and codes[0] == 200


def test_the_page_data_offers_the_list_only_on_its_product(client):
    c, app = client
    assert c.get(f"/begin/product-page-data/{SLUG}").get_json().get("waitlist") == {"slug": SLUG}
    assert "waitlist" not in c.get("/begin/product-page-data/vitality").get_json()


# ── The page ──────────────────────────────────────────────────────────────────
WORDS = [
    "Not ready to reserve? Join the waiting list.",
    "Leave your email and we will send you one email when Retina Renew launches. No card, no charge.",
    "Join the waiting list",
    "We will email you when Retina Renew launches, and nothing else unless you ask.",
    "You're on the list. We'll email you when Retina Renew is ready.",
]


def test_the_page_carries_the_approved_words_and_a_hidden_trap():
    page = (ROOT / "static" / "begin-product.html").read_text()
    for w in WORDS:
        assert w in page, w
    assert "once, when" not in page
    a, b = page.find('id="retina-waitlist"'), page.find('id="sp-sections"')
    assert -1 < page.find('id="founding-soldout"') < a < b       # below the reservation
    assert 'name="company"' in page[a:b]


def test_the_seed_names_retina_renew():
    seed = (ROOT / "data" / "condition_programs_seed.json").read_text()
    assert "Retina Renew Neuro-Magnesium" in seed
    assert "Retina Restore" not in seed and "mapped to Neuro-Magnesium" not in seed
