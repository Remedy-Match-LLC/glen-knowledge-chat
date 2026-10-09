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
# Glen, 2026-10-09 ("fix Retina Renew also"): the consent no longer promises "nothing else
# unless you ask", which was untrue for people already on the mailing list.
CONSENT = ("We will email you when Retina Renew launches. Joining this list does not add you "
           "to any other mailing list.")


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


def _join(cx, email, **kw):
    """Sign up AND click the confirmation link, for tests that need a confirmed member."""
    status, token = pw.sign_up(cx, SLUG, email, **kw)
    if token:
        assert pw.confirm(cx, token) == SLUG
    return status


def _queued(cx):
    return [(r["email"], json.loads(r["payload_json"]))
            for r in cx.execute("SELECT email, payload_json FROM ghl_write_queue WHERE op='tag_add'")]


def test_the_consent_line_is_glens_approved_wording():
    assert pw.CONSENT_TEXT == CONSENT
    assert pw.LISTS[SLUG] == TAG


def test_nothing_is_tagged_or_mirrored_until_the_link_is_clicked(cx):
    status, token = pw.sign_up(cx, SLUG, " Ann@X.com ", first_name="Ann")
    assert status == "new" and token
    assert _tags(cx, "ann@x.com") is None and _queued(cx) == []
    assert pw.waiters_to_email(cx, SLUG) == []
    assert pw.confirm(cx, token) == SLUG
    row = cx.execute("SELECT * FROM product_waitlist").fetchone()
    assert (row["email"], row["first_name"], row["consent_text"]) == ("ann@x.com", "Ann", CONSENT)
    assert TAG in _tags(cx, "ann@x.com")
    assert "consent:opted-in" not in _tags(cx, "ann@x.com")      # the launch email only
    assert _queued(cx) == [("ann@x.com", {"tags": [TAG]})]


def test_signing_up_twice_is_one_sign_up_and_keeps_other_tags(cx):
    cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES "
               "('bo@x.com', '[\"vip\"]', 't', 't')")
    _join(cx, "bo@x.com")
    assert pw.sign_up(cx, SLUG, "BO@x.com") == ("existing", None)
    assert cx.execute("SELECT COUNT(*) FROM product_waitlist").fetchone()[0] == 1
    assert _tags(cx, "bo@x.com") == ["vip", TAG]
    assert len(_queued(cx)) == 1


def test_a_reserver_is_left_out_of_the_launch_email(cx):
    subs.init_subscriptions_table(cx)
    subs.migrate_add_founding_columns(cx)
    for e in ("wait@x.com", "res@x.com", "gone@x.com"):
        _join(cx, e)
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
    _join(cx, "a@x.com")
    _join(cx, "b@x.com")
    pw.sign_up(cx, SLUG, "c@x.com")                  # not confirmed
    assert pw.counts(cx, SLUG) == {"signed_up": 3, "confirmed": 2, "tagged_in_house": 2,
                                   "ghl_queued": 2, "emailed": 0}


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
    monkeypatch.setattr(app, "_founding_enabled", lambda: True)       # the presale is running
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
    assert c.get(f"/begin/product-page-data/{SLUG}").get_json().get("waitlist") == {
        "slug": SLUG, "intro": WORDS[1], "consent": CONSENT, "reserve_line": WORDS[0],
        "success": WORDS[4]}
    assert "waitlist" not in c.get("/begin/product-page-data/vitality").get_json()


# ── The page ──────────────────────────────────────────────────────────────────
# Retina Renew's words, unchanged except the consent (2026-10-09). The reserve line, intro,
# consent and success note now travel in page-data per list; the rest stay in the page.
WORDS = [
    "Not ready to reserve? Join the waiting list.",
    "Leave your email and we will send you one email when Retina Renew launches. No card, no charge.",
    "Join the waiting list",
    CONSENT,
    "You're on the list. We'll email you when Retina Renew is ready.",
    "Almost done. We've sent you an email. Click the link in it to confirm.",
]


def test_the_page_carries_the_approved_words_and_a_hidden_trap():
    page = (ROOT / "static" / "begin-product.html").read_text()
    t = pw.TEXTS[SLUG]
    assert [t["reserve_line"], t["intro"], t["consent"], t["success"]] == [
        WORDS[0], WORDS[1], WORDS[3], WORDS[4]]
    for w in (WORDS[2], WORDS[5]):
        assert w in page, w
    assert "Retina Renew" not in page                 # the page names no list's product itself
    assert "once, when" not in page
    a, b = page.find('id="product-waitlist"'), page.find('id="sp-sections"')
    assert -1 < page.find('id="founding-soldout"') < a < b       # below the reservation
    assert 'name="company"' in page[a:b]


def test_the_seed_names_retina_renew():
    seed = (ROOT / "data" / "condition_programs_seed.json").read_text()
    assert "Retina Renew Neuro-Magnesium" in seed
    assert "Retina Restore" not in seed and "mapped to Neuro-Magnesium" not in seed


# ── Review rounds 1 and 2 ────────────────────────────────────────────────────
def test_a_merged_address_tags_the_surviving_person(cx):
    from dashboard import person_aliases as pa
    pa.init_tables(cx)
    cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES ('new@x.com','[]','t','t')")
    cx.execute("INSERT INTO email_aliases (alias_email, canonical_email, merge_id, created_at) "
               "VALUES ('old@x.com', 'new@x.com', 1, 't')")
    cx.commit()
    _join(cx, "old@x.com")
    assert cx.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    assert TAG in _tags(cx, "new@x.com")
    assert _queued(cx) == [("new@x.com", {"tags": [TAG]})]


def test_a_reservation_lookup_that_fails_stops_the_list(cx, monkeypatch):
    """Round 2: failing open would email every reserver."""
    subs.init_subscriptions_table(cx)          # the table exists but has no founding columns
    _join(cx, "a@x.com")
    with pytest.raises(Exception):
        pw.waiters_to_email(cx, SLUG)


def test_no_reservations_table_means_no_reservers(cx):
    _join(cx, "a@x.com")
    assert [w["email"] for w in pw.waiters_to_email(cx, SLUG)] == ["a@x.com"]


def test_suppressed_and_unsubscribed_addresses_are_left_out(cx):
    from dashboard import email_suppression as es
    es.init_table(cx) if hasattr(es, "init_table") else None
    cx.execute("CREATE TABLE IF NOT EXISTS email_suppression (email TEXT PRIMARY KEY, bounce_type TEXT, "
               "reason TEXT, source TEXT, created_at TEXT)")
    for e in ("ok@x.com", "bounce@x.com", "unsub@x.com"):
        _join(cx, e)
    cx.execute("INSERT INTO email_suppression (email, reason) VALUES ('bounce@x.com', 'hard')")
    cx.execute("UPDATE people SET tags=? WHERE email='unsub@x.com'",
               (json.dumps([TAG, "consent:unsubscribed"]),))
    cx.commit()
    assert [w["email"] for w in pw.waiters_to_email(cx, SLUG)] == ["ok@x.com"]


def test_a_tag_removed_meanwhile_is_never_written_back(cx, monkeypatch):
    """Round 2: the read-modify-write could restore consent:opted-in removed by another worker."""
    cx.execute("INSERT INTO people (email, tags, created_at, updated_at) VALUES "
               "('c@x.com', '[\"consent:opted-in\"]', 't', 't')")
    cx.commit()
    real = pw._pe.set_person_tags

    def meanwhile(current, add=None, remove=None):
        if "consent:opted-in" in current:
            cx.execute("UPDATE people SET tags='[]' WHERE email='c@x.com'")   # another worker
        return real(current, add=add, remove=remove)
    monkeypatch.setattr(pw._pe, "set_person_tags", meanwhile)
    _join(cx, "c@x.com")
    assert _tags(cx, "c@x.com") == [TAG]


def test_a_simultaneous_duplicate_is_still_one_sign_up(cx, monkeypatch):
    """Round 2: two workers both see no row; the second insert must not fail the request."""
    _join(cx, "d@x.com")
    monkeypatch.setattr(pw, "_listed_row", lambda *a, **k: None)
    assert pw.sign_up(cx, SLUG, "d@x.com") == ("existing", None)
    assert len(_queued(cx)) == 1
    assert cx.execute("SELECT COUNT(*) FROM product_waitlist").fetchone()[0] == 1


def test_the_list_shows_only_while_the_presale_runs(client, monkeypatch):
    c, app = client
    monkeypatch.setattr(app, "_founding_enabled", lambda: False)
    assert "waitlist" not in c.get(f"/begin/product-page-data/{SLUG}").get_json()


def test_the_rate_limit_keys_on_the_trusted_address(client, monkeypatch):
    """Round 1: the first X-Forwarded-For hop is written by the caller."""
    c, app = client
    seen = []
    real = app._client_address.client_address

    def spy(*a, **k):
        seen.append(a)
        return real(*a, **k)
    monkeypatch.setattr(app._client_address, "client_address", spy)
    c.post(f"/api/waitlist/{SLUG}", json={"email": "f@x.com"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert seen


def test_the_sold_out_box_points_at_the_form():
    page = (ROOT / "static" / "begin-product.html").read_text()
    assert 'id="founding-waitlist-link" href="#product-waitlist"' in page


HIDE_JS = r"""
const assert = require('assert');
const f = {style: {display: 'block'}, onsubmit: () => 'old'};
global.document = {getElementById: id => id === 'product-waitlist' ? f : null};
global.location = {search: ''};
FN
renderWaitlist({});
assert.strictEqual(f.style.display, 'none');
assert.strictEqual(f.onsubmit, null);
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_form_hides_on_a_product_without_a_list(tmp_path):
    page = (ROOT / "static" / "begin-product.html").read_text()
    a = page.find("function renderWaitlist(")
    b = page.find("\n    }\n", a) + 7
    js = tmp_path / "h.js"
    js.write_text(HIDE_JS.replace("FN", page[a:b]))
    env = dict(os.environ, NODE_OPTIONS="")
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout



# ── Confirmation (Glen, 2026-09-29: "confirm email"; wording approved) ───────────
def test_clicking_the_link_twice_is_harmless(cx):
    _, token = pw.sign_up(cx, SLUG, "t@x.com")
    assert pw.confirm(cx, token) == SLUG and pw.confirm(cx, token) == SLUG
    assert len(_queued(cx)) == 1


def test_a_link_expires_after_30_days(cx):
    _, token = pw.sign_up(cx, SLUG, "old@x.com")
    cx.execute("UPDATE product_waitlist SET confirm_sent_at='2020-01-01T00:00:00+00:00'")
    cx.commit()
    assert pw.confirm(cx, token) is None
    assert _tags(cx, "old@x.com") is None and _queued(cx) == []
    assert pw.confirm(cx, "not-a-token") is None


def test_signing_up_again_resends_at_most_every_ten_minutes(cx):
    _, first = pw.sign_up(cx, SLUG, "r@x.com")
    assert pw.sign_up(cx, SLUG, "r@x.com") == ("throttled", None)
    cx.execute("UPDATE product_waitlist SET confirm_sent_at='2020-01-01T00:00:00+00:00'")
    cx.commit()
    status, second = pw.sign_up(cx, SLUG, "r@x.com")
    assert status == "resend" and second and second != first
    assert pw.confirm(cx, first) is None                       # only the newest link works
    cx.execute("UPDATE product_waitlist SET confirm_sent_at=?", (pw._now(),))
    assert pw.confirm(cx, second) == SLUG


def test_the_confirmation_email_carries_the_approved_words(client, monkeypatch):
    c, app = client
    sent = []
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda to, subject, body, **k: sent.append((to, subject, body)) or {"ok": True})
    r = c.post(f"/api/waitlist/{SLUG}", json={"email": "m@x.com", "first_name": "Mia"})
    assert r.status_code == 200
    (to, subject, body), = sent
    assert to == "m@x.com" and subject == "Confirm your Retina Renew launch email"
    assert body.startswith("Hi Mia,\n\nPlease confirm you'd like an email when Retina Renew launches:\n")
    assert "If you didn't ask for this, ignore this email and nothing more will be sent." in body
    assert body.rstrip().endswith("Dr. Glen Swartwout")
    link = [ln for ln in body.splitlines() if "/begin/waitlist/confirm/" in ln][0].strip()
    token = link.rsplit("/", 1)[1]
    r = c.post(f"/begin/waitlist/confirm/{token}")
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith(f"/begin/product/{SLUG}?waitlist=confirmed")
    with sqlite3.connect(app.LOG_DB) as cx:
        assert TAG in json.loads(cx.execute("SELECT tags FROM people WHERE email='m@x.com'").fetchone()[0])
    # An unknown token is no longer sent to the first list's page (2026-10-09).
    bad = c.post("/begin/waitlist/confirm/bad")
    assert bad.status_code == 200 and "That link has expired or is not valid" in bad.get_data(as_text=True)


def test_a_blank_first_name_greets_with_hi(client, monkeypatch):
    c, app = client
    sent = []
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda to, subject, body, **k: sent.append(body) or {"ok": True})
    c.post(f"/api/waitlist/{SLUG}", json={"email": "n@x.com"})
    assert sent[0].startswith("Hi,\n\nPlease confirm")


def test_the_page_answers_the_confirmation_link():
    page = (ROOT / "static" / "begin-product.html").read_text()
    assert "get('waitlist') === 'confirmed'" in page


# ── Review round 3 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["a@x.com,b@y.com", "a@x.com;b@y.com", "A <a@x.com>", "a@x",
                                 "a b@x.com", "a@x.com\nb@y.com", "@x.com"])
def test_only_one_plain_address_is_accepted(client, bad):
    """Round 3: a comma list passed, and one request emailed every address in it."""
    c, app = client
    assert c.post(f"/api/waitlist/{SLUG}", json={"email": bad}).status_code == 400


@pytest.mark.parametrize("raw,want", [("Mia", "Mia"), ("Anne-Marie O'Neil", "Anne-Marie O'Neil"),
                                      ("José", "José"), ("Claim your refund at evil.example/x", ""),
                                      ("<b>x</b>", ""), ("Mia 2", "")])
def test_a_first_name_is_letters_only_or_nothing(raw, want):
    """Round 3: the name goes into an email from Glen's Gmail; a link must not ride in it."""
    assert pw.clean_first_name(raw) == want


def test_one_address_gets_at_most_three_confirmation_emails(cx):
    for i in range(3):
        status, _ = pw.sign_up(cx, SLUG, "z@x.com")
        assert status in ("new", "resend")
        cx.execute("UPDATE product_waitlist SET confirm_sent_at='2020-01-01T00:00:00+00:00'")
    assert pw.sign_up(cx, SLUG, "z@x.com") == ("capped", None)


def test_a_daily_cap_stops_a_flood(cx, monkeypatch):
    monkeypatch.setattr(pw, "DAILY_SENDS", 2)
    assert pw.sign_up(cx, SLUG, "p1@x.com")[0] == "new"
    assert pw.sign_up(cx, SLUG, "p2@x.com")[0] == "new"
    assert pw.sign_up(cx, SLUG, "p3@x.com") == ("capped", None)


def test_a_merge_keeps_the_confirmed_sign_up():
    from dashboard import person_merge as pm
    assert pm.CLASH_RULES["product_waitlist"] == "prefer:confirmed_at,emailed_at"


def test_a_late_click_still_shows_its_message():
    page = (ROOT / "static" / "begin-product.html").read_text()
    a = page.find("function renderWaitlist(")
    body = page[a:page.find("\n    }\n", a)]
    assert -1 < body.find("get('waitlist')") < body.find("if (!w || !w.slug)")



def test_opening_the_link_shows_a_button_and_confirms_nothing(client, monkeypatch):
    """Mail scanners open every link on arrival; only the button confirms (Glen, 2026-09-29)."""
    c, app = client
    sent = []
    monkeypatch.setattr(app._inbox, "send_email",
                        lambda to, subject, body, **k: sent.append(body) or {"ok": True})
    c.post(f"/api/waitlist/{SLUG}", json={"email": "s@x.com"})
    token = [ln for ln in sent[0].splitlines() if "/begin/waitlist/confirm/" in ln][0].rsplit("/", 1)[1]
    for method in (c.get, c.head):
        r = method(f"/begin/waitlist/confirm/{token}")
        assert r.status_code == 200
    page = c.get(f"/begin/waitlist/confirm/{token}").get_data(as_text=True)
    assert "Confirm your Retina Renew launch email" in page
    assert '<button type="submit">Confirm</button>' in page and 'method="post"' in page
    assert 'name="robots" content="noindex' in page
    with sqlite3.connect(app.LOG_DB) as cx:
        assert cx.execute("SELECT COALESCE(confirmed_at,'') FROM product_waitlist "
                          "WHERE email='s@x.com'").fetchone()[0] == ""
        try:
            queued = cx.execute("SELECT COUNT(*) FROM ghl_write_queue").fetchone()[0]
        except sqlite3.OperationalError:      # nothing ever queued, so no table yet
            queued = 0
        assert queued == 0
    assert c.post(f"/begin/waitlist/confirm/{token}").status_code in (302, 303)
    with sqlite3.connect(app.LOG_DB) as cx:
        assert cx.execute("SELECT COALESCE(confirmed_at,'') FROM product_waitlist "
                          "WHERE email='s@x.com'").fetchone()[0] != ""


CONFIRMED_JS = r"""
const assert = require('assert');
function el(tag, id){ return {tagName: tag, id: id || '', style: {display: ''}, disabled: false, textContent: ''}; }
const parts = [el('P'), el('P'), el('INPUT'), el('INPUT'), el('INPUT'), el('BUTTON'), el('P')];
const note = el('P', 'product-waitlist-msg');
const f = {style: {display: 'none'}, onsubmit: null, email: parts[3], first_name: parts[2],
  querySelector: () => parts[5],
  querySelectorAll: () => parts};
global.document = {getElementById: id => id === 'product-waitlist' ? f : (id === 'product-waitlist-msg' ? note : null)};
global.location = {search: '?waitlist=confirmed'};
FN
renderWaitlist({waitlist: {slug: 'neuro-magnesium',
  success: "You're on the list. We'll email you when Retina Renew is ready."}});
assert.strictEqual(f.style.display, 'block');
assert.strictEqual(note.textContent, "You're on the list. We'll email you when Retina Renew is ready.");
assert(parts.every(p => p.style.display === 'none'), 'every other part of the form is hidden');
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_once_confirmed_only_the_confirmation_line_shows(tmp_path):
    """Glen, 2026-09-29: after confirming, the fields and the join button were still there
    (greyed out) under 'Leave your email', which is confusing for someone already on the list."""
    page = (ROOT / "static" / "begin-product.html").read_text()
    a = page.find("function renderWaitlist(")
    b = page.find("\n    }\n", a) + 7
    js = tmp_path / "c.js"
    js.write_text(CONFIRMED_JS.replace("FN", page[a:b]))
    env = dict(os.environ, NODE_OPTIONS="")
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
