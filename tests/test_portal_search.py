"""Portal header search, step 1 (plan 2026-11-06-portal-header-site-search).

The privacy tests seed two clients whose orders, reports and messages all share the
search word, then prove each portal token sees only its own client's records."""
import json
import sqlite3
from pathlib import Path

import pytest

from dashboard import portal_search as ps

WORD = "zephyrine"


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_PORTAL_SEARCH_ENABLED", True)
    monkeypatch.setattr(appmod, "_portal_biofield_unlocked", lambda email: True)
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": {}})
    monkeypatch.setattr(appmod, "_get_product", lambda slug: None)
    monkeypatch.setattr(appmod, "_invoice_order_for_token", lambda tok: _order_by_token(appmod, tok))
    monkeypatch.setattr(ps, "CORPUS_TTL_S", 0)     # caching has its own test
    appmod._init_auth_tables()
    ps.reset_caches()
    appmod.app.config["TESTING"] = True
    yield appmod.app.test_client(), appmod
    ps.reset_caches()


def _order_by_token(appmod, tok):
    """Stands in for the invoice link lookup: the order holding this invoice_token."""
    from dashboard import orders as o
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    try:
        row = cx.execute("SELECT id FROM orders WHERE invoice_token=?", (tok,)).fetchone()
        return o.get_order(cx, row[0]) if row else None
    finally:
        cx.close()


def _add_order(appmod, email, ref, item, *, paid=True, token=None, published=0,
               status="new", total=4500):
    from dashboard import orders as o
    cx = sqlite3.connect(appmod.LOG_DB)
    o.init_orders_table(cx)
    try:
        cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    oid = o.upsert_order(cx, source="test", external_ref=ref, email=email,
                         items=[{"slug": "x", "name": item, "qty": 1}], total_cents=total)
    cx.execute("UPDATE orders SET pay_status=?, paid_at=?, invoice_token=?, portal_published=?, "
               "status=? WHERE id=?",
               ("paid" if paid else "unpaid", "2026-10-01" if paid else None, token,
                published, status, oid))
    cx.commit()
    cx.close()


def _seed_client(appmod, email, tag):
    """A portal, a paid order, a confirmed report, a draft report, a held report and a
    message, every one carrying WORD and the client's own `tag`."""
    from dashboard import client_portal as cp
    from dashboard import portal_biofield_reports as pbr
    from dashboard import portal_chat as pc
    from dashboard import report_holds as rh
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, f"Client {tag}", {"greeting": "hi"})
    cx.commit()
    cx.close()
    _add_order(appmod, email, f"ord-{tag}", f"{WORD} {tag} drops", token=f"tok-{tag}")
    cx = sqlite3.connect(appmod.LOG_DB)
    pbr.init_table(cx)
    pbr.upsert_report(cx, email, "2026-10-02", "s1",
                      {"greeting": f"{WORD} {tag} greeting",
                       "layers": [{"n": 1, "title": "Layer", "meaning": "m",
                                   "remedy": f"{tag}remedy"}]},
                      "confirmed", report_kind=pbr.REMEDY_MATCH)
    pbr.upsert_report(cx, email, "2026-10-03", "s2",
                      {"greeting": f"{WORD} {tag} draftonly"}, "ai_draft")
    pbr.upsert_report(cx, email, "2026-10-04", "s3",
                      {"greeting": f"{WORD} {tag} heldonly"}, "confirmed")
    rh.init_table(cx)
    rh.hold(cx, email, "2026-10-04")
    pc.add_message(cx, email, pc.CLIENT, f"my {WORD} {tag} question", author=f"Client {tag}")
    cx.close()
    return token


def _search(client, token, q=WORD, page=1):
    return client.get(f"/api/portal/{token}/site-search?q={q}&page={page}")


def _records(payload):
    return [r for r in payload["results"] if r["source"] == "records"]


def _text(r):
    return r["title"] + " " + r["snippet_html"]


# ── privacy ──────────────────────────────────────────────────────────────────

def test_each_client_sees_only_their_own_records(env):
    client, appmod = env
    tok_a = _seed_client(appmod, "alpha@example.com", "alphaonly")
    tok_b = _seed_client(appmod, "bravo@example.com", "bravoonly")

    for tok, mine, theirs in ((tok_a, "alphaonly", "bravoonly"),
                              (tok_b, "bravoonly", "alphaonly")):
        r = _search(client, tok)
        assert r.status_code == 200
        recs = _records(r.get_json())
        # One order, one confirmed report, one message. The draft and held reports stay out.
        assert sorted(x["kind"] for x in recs) == ["message", "order", "report"]
        assert all(mine in _text(x) for x in recs)
        assert not any(theirs in _text(x) for x in recs)
        assert "draftonly" not in json.dumps(r.get_json())
        assert "heldonly" not in json.dumps(r.get_json())


def test_another_clients_unique_word_finds_nothing(env):
    client, appmod = env
    tok_a = _seed_client(appmod, "alpha@example.com", "alphaonly")
    _seed_client(appmod, "bravo@example.com", "bravoonly")
    assert _search(client, tok_a, q="bravoonly").get_json()["total"] == 0
    assert _search(client, tok_a, q="alphaonly").get_json()["total"] == 3


def test_query_string_cannot_name_another_client(env):
    client, appmod = env
    tok_a = _seed_client(appmod, "alpha@example.com", "alphaonly")
    _seed_client(appmod, "bravo@example.com", "bravoonly")
    r = client.get(f"/api/portal/{tok_a}/site-search?q={WORD}&email=bravo@example.com"
                   f"&member=bravo@example.com")
    assert "bravoonly" not in json.dumps(r.get_json())


def test_bad_token_gets_404_and_no_results(env):
    client, appmod = env
    _seed_client(appmod, "alpha@example.com", "alphaonly")
    r = _search(client, "not-a-real-token")
    assert r.status_code == 404
    body = r.get_json()
    assert "results" not in body and "alphaonly" not in json.dumps(body)


def test_response_is_not_cacheable(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    assert "no-store" in _search(client, tok).headers["Cache-Control"]


def test_remedy_names_follow_the_portal_blur(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    assert _search(client, tok, q="alphaonlyremedy").get_json()["total"] == 1
    monkeypatch.setattr(appmod, "_portal_biofield_unlocked", lambda email: False)
    assert _search(client, tok, q="alphaonlyremedy").get_json()["total"] == 0


# ── flag ─────────────────────────────────────────────────────────────────────

def test_flag_off_route_404s(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    monkeypatch.setattr(appmod, "_PORTAL_SEARCH_ENABLED", False)
    assert _search(client, tok).status_code == 404


def test_flag_off_portal_page_has_no_icon_and_flag_on_only_adds_the_script(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    monkeypatch.setattr(appmod, "_PORTAL_SEARCH_ENABLED", False)
    off = client.get(f"/portal/{tok}").get_data(as_text=True)
    monkeypatch.setattr(appmod, "_PORTAL_SEARCH_ENABLED", True)
    on = client.get(f"/portal/{tok}").get_data(as_text=True)
    assert "portal-search" not in off
    assert ps.SCRIPT_TAG in on
    assert on.replace(ps.SCRIPT_TAG + "\n", "", 1) == off


def test_script_is_not_injected_on_public_pages(env):
    client, appmod = env
    assert "portal-search" not in client.get("/terms").get_data(as_text=True)


def test_is_portal_page():
    assert ps.is_portal_page("/portal/abc123")
    assert ps.is_portal_page("/portal/me")
    assert ps.is_portal_page("/portal/abc123/program")
    for p in ("/portal/login", "/portal/password-reset", "/portal/claim",
              "/portal/abc/analyze", "/portal/abc/bodymap", "/begin/match", "/portal/auth/google/start"):
        assert not ps.is_portal_page(p), p


# ── store ────────────────────────────────────────────────────────────────────

def test_products_follow_store_rules_and_orderability(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    products = {
        "ok-one": {"name": f"{WORD} Drops", "price_cents": 2500},
        "waiting": {"name": f"{WORD} Waiting", "price_cents": 3000, "waitlist_only": True},
        "gone": {"name": f"{WORD} Gone", "inactive": True},
        "info": {"name": f"{WORD} Info", "info_only": True},
        "fungifuge": {"name": f"{WORD} Fungifuge", "price_cents": 100},
        "retired": {"name": f"{WORD} Group", "description": "Join us on Skool", "price_cents": 100},
    }
    for slug, p in products.items():
        p["slug"] = slug
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": products})
    monkeypatch.setattr(appmod, "_get_product", lambda slug: products.get(slug))
    store = {r["url"]: r for r in _search(client, tok).get_json()["results"]
             if r["source"] == "store"}
    # Fungifuge is on the do-not-recommend list and the store lists it, so search does
    # too. Glen, 6 Nov 2026: "it can list them."
    assert set(store) == {"/begin/product/ok-one", "/begin/product/waiting",
                          "/begin/product/fungifuge"}
    assert store["/begin/product/ok-one"]["orderable"] is True
    assert store["/begin/product/ok-one"]["price"] == "$25.00"
    assert store["/begin/product/waiting"]["orderable"] is False
    assert store["/begin/product/waiting"]["price"] == ""


def test_store_orderability_uses_the_apps_predicate(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    products = {"ok-one": {"slug": "ok-one", "name": f"{WORD} Drops", "price_cents": 2500}}
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": products})
    monkeypatch.setattr(appmod, "_get_product", lambda slug: products.get(slug))
    monkeypatch.setattr(appmod, "_is_orderable", lambda slug, p=None: False)
    store = [r for r in _search(client, tok).get_json()["results"] if r["source"] == "store"]
    assert store and store[0]["orderable"] is False


# ── pages ────────────────────────────────────────────────────────────────────

def test_every_indexed_page_is_a_real_route_serving_its_file(env, monkeypatch):
    client, appmod = env
    monkeypatch.setattr(appmod, "_SHOP_ENABLED", True)
    monkeypatch.setenv("SUPPLEMENT_REVIEW_ENABLED", "1")
    static = Path(appmod.STATIC)
    for pg in ps.PAGES:
        r = client.get(pg["path"])
        assert r.status_code == 200, pg
        served = r.get_data(as_text=True)
        own = (static / pg["file"]).read_text(encoding="utf-8")
        # The app injects shell and theme tags into <head>; the body is the file's own.
        assert own.split("<body", 1)[1] in served, pg


def test_page_index_titles_text_and_gates():
    import app as appmod
    idx = ps.build_page_index(appmod.STATIC, {"shop": lambda: False, "product_review": lambda: True})
    urls = {d["url"] for d in idx}
    assert "/terms" in urls and "/product-review" in urls and "/shop" not in urls
    terms = next(d for d in idx if d["url"] == "/terms")
    assert terms["title"].startswith("Terms")
    assert "<" not in terms["body"] and "function(" not in terms["body"]
    assert not any(ps.names_retired(d["title"] + d["body"]) for d in idx)


def test_page_naming_a_retired_destination_is_left_out(tmp_path):
    (tmp_path / "a.html").write_text("<title>A</title><body>Find us on Practice Better</body>")
    (tmp_path / "b.html").write_text("<title>B</title><body>Plain page</body>")
    idx = ps.build_page_index(tmp_path, pages=({"path": "/a", "file": "a.html"},
                                               {"path": "/b", "file": "b.html"}))
    assert [d["url"] for d in idx] == ["/b"]


def test_pages_are_searched_through_the_route(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    res = _search(client, tok, q="privacy policy").get_json()["results"]
    assert any(r["source"] == "page" and r["url"] == "/privacy" for r in res)


# ── snippets, ranking, paging ────────────────────────────────────────────────

def test_snippet_escapes_before_bolding():
    out = ps.snippet_html('<img src=x onerror=alert(1)> zephyr & co', ["zephyr"])
    assert "<img" not in out and "&lt;img" in out
    assert "<b>zephyr</b>" in out and "&amp; co" in out
    # A term that is also an entity name never breaks the escaping.
    assert ps.bold_html("a & b", ["amp"]) == "a &amp; b"
    assert ps.bold_html("<b>", ["b"]) == "&lt;<b>b</b>&gt;"


def test_message_markup_comes_back_escaped(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    from dashboard import portal_chat as pc
    cx = sqlite3.connect(appmod.LOG_DB)
    pc.add_message(cx, "alpha@example.com", pc.CLIENT, f'<script>x()</script> {WORD}')
    cx.close()
    body = json.dumps(_search(client, tok).get_json())
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_short_query_returns_nothing(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    assert _search(client, tok, q="z").get_json()["total"] == 0


def test_paging_reaches_every_match_once():
    docs = [{"source": ps.PAGE, "kind": "page", "title": f"Page {i}",
             "body": f"{WORD} body {i}", "url": f"/p{i}"} for i in range(25)]
    seen = []
    first = ps.search(docs, [WORD], page=1)
    assert first["total"] == 25 and first["pages"] == 3
    for n in (1, 2, 3):
        got = ps.search(docs, [WORD], page=n)["results"]
        assert len(got) == (10 if n < 3 else 5)
        seen += [r["url"] for r in got]
    assert sorted(seen) == sorted(d["url"] for d in docs)
    assert ps.search(docs, [WORD], page=99)["page"] == 3


def test_ranking_puts_title_matches_first_and_all_words_are_required():
    docs = [{"source": ps.PAGE, "kind": "page", "title": "Other", "body": "zephyr once",
             "url": "/b"},
            {"source": ps.STORE, "kind": "product", "title": "Zephyr Drops", "body": "",
             "url": "/a"},
            {"source": ps.PAGE, "kind": "page", "title": "Other", "body": "zephyr only",
             "url": "/c"}]
    res = ps.search(docs, ["zephyr"])["results"]
    assert res[0]["url"] == "/a"
    assert ps.search(docs, ["zephyr", "once"])["total"] == 1


def test_retired_destinations_never_returned_from_any_source():
    docs = [{"source": ps.RECORDS, "kind": "message", "title": "Message",
             "body": "zephyr see ClientClub", "url": "#chat"},
            {"source": ps.RECORDS, "kind": "message", "title": "Message",
             "body": "zephyr fine", "url": "#chat"}]
    assert ps.search(docs, ["zephyr"])["total"] == 1


# ── review rounds 1 and 2 ────────────────────────────────────────────────────

def _legacy_portal(appmod, email, content):
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, "Legacy", content)
    cx.close()
    return token


_LEGACY = {"layers": [{"n": 1, "title": "Layer", "meaning": f"{WORD} legacyonly"}],
           "current_scan_date": "2026-09-01"}


def test_legacy_portal_report_is_found_when_confirmed(env):
    client, appmod = env
    tok = _legacy_portal(appmod, "leg@example.com", dict(_LEGACY))
    assert _search(client, tok, q="legacyonly").get_json()["total"] == 1


def test_legacy_portal_report_on_hold_is_never_found(env):
    client, appmod = env
    from dashboard import report_holds as rh
    tok = _legacy_portal(appmod, "leg@example.com", dict(_LEGACY))
    cx = sqlite3.connect(appmod.LOG_DB)
    rh.init_table(cx)
    rh.hold(cx, "leg@example.com", "2026-09-01")
    cx.close()
    assert _search(client, tok, q="legacyonly").get_json()["total"] == 0


def test_legacy_portal_report_in_draft_is_never_found(env):
    client, appmod = env
    tok = _legacy_portal(appmod, "leg@example.com", {**_LEGACY, "biofield_status": "ai_draft"})
    assert _search(client, tok, q="legacyonly").get_json()["total"] == 0


def test_orders_follow_the_orders_tab(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    em = "alpha@example.com"
    _add_order(appmod, em, "draft", "draftitem", paid=False, token="t-d")          # not published
    _add_order(appmod, em, "done", "doneitem", paid=False, token="t-x", published=1,
               status="delivered")                                               # tab hides it
    _add_order(appmod, em, "due", "dueitem", paid=False, token="t-due", published=1)
    _add_order(appmod, em, "nolink", "nolinkitem", total=1234)                    # paid, no link
    _add_order(appmod, em, "gone", "goneitem", token="t-expired")
    appmod_lookup = appmod._invoice_order_for_token
    appmod._invoice_order_for_token = lambda t: None if t == "t-expired" else appmod_lookup(t)
    try:
        def n(q):
            return _search(client, tok, q=q).get_json()["total"]
        assert n("draftitem") == 0 and n("doneitem") == 0
        assert n("dueitem") == 1
        assert n("nolinkitem") == 0          # the tab shows no items for it
        assert n("12.34") == 1               # its amount and date are on the tab
        assert n("goneitem") == 0            # the invoice link no longer opens
    finally:
        appmod._invoice_order_for_token = appmod_lookup


def test_invoice_items_of_another_email_are_not_shown(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    monkeypatch.setattr(appmod, "_invoice_order_for_token",
                        lambda t: {"email": "bravo@example.com", "items": [{"name": "bravoitem"}]})
    assert _search(client, tok, q="bravoitem").get_json()["total"] == 0


def test_messages_are_the_ones_the_messages_card_shows(env):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    from dashboard import portal_chat as pc
    cx = sqlite3.connect(appmod.LOG_DB)
    pc.add_message(cx, "alpha@example.com", pc.CLIENT, "oldestmessage")
    for i in range(100):
        pc.add_message(cx, "alpha@example.com", pc.CLIENT, f"filler {i}")
    cx.close()
    assert len(appmod._portal_chat_thread("alpha@example.com")) == 100
    assert _search(client, tok, q="oldestmessage").get_json()["total"] == 0


def test_do_not_recommend_names_are_kept_in_records_and_pages(env):
    """Glen, 6 Nov 2026: "it can list them." The client's own orders and messages, and
    the pages, keep a do-not-recommend name as written."""
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    from dashboard import portal_chat as pc
    _add_order(appmod, "alpha@example.com", "dnr", f"Fungifuge {WORD}", token="t-dnr")
    cx = sqlite3.connect(appmod.LOG_DB)
    pc.add_message(cx, "alpha@example.com", pc.PRACTITIONER, f"Try Electrolyte Mineral Manna {WORD}")
    cx.close()
    assert _search(client, tok, q="fungifuge").get_json()["total"] == 1
    assert _search(client, tok, q="manna").get_json()["total"] == 1
    docs = [{"source": ps.PAGE, "kind": "page", "title": "AllerFree guide",
             "body": f"{WORD} about Food Tolerance", "url": "/p"}]
    out = ps.search(docs, [WORD])
    assert "AllerFree" in out["results"][0]["title"]
    assert "Food Tolerance" in out["results"][0]["snippet_html"]


def test_store_lists_do_not_recommend_product_the_store_lists(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    products = {"electrolyte-mineral-manna": {"slug": "electrolyte-mineral-manna",
                                              "name": "Electrolyte Mineral Manna",
                                              "price_cents": 3000},
                "fungifuge": {"slug": "fungifuge", "name": "Fungifuge", "inactive": True}}
    monkeypatch.setattr(appmod, "_PRODUCTS", {"products": products})
    monkeypatch.setattr(appmod, "_get_product", lambda slug: products.get(slug))
    from dashboard import shop_catalog as sc
    shop = {c["slug"] for c in sc.search(list(products), products.get, "")}
    found = {r["url"].rsplit("/", 1)[-1] for r in
             _search(client, tok, q="manna").get_json()["results"] if r["source"] == "store"}
    assert found == shop == {"electrolyte-mineral-manna"}
    assert _search(client, tok, q="fungifuge").get_json()["total"] == 0   # store hides it


def test_no_connection_is_open_while_unlock_or_chat_runs(env, monkeypatch):
    client, appmod = env
    tok = _seed_client(appmod, "alpha@example.com", "alphaonly")
    real_connect, open_now = appmod.db.connect, [0]

    class _Counted:
        def __init__(self, *a, **k):
            self.cx = real_connect(*a, **k)
        def __enter__(self):
            open_now[0] += 1
            return self.cx.__enter__()
        def __exit__(self, *a):
            open_now[0] -= 1
            return self.cx.__exit__(*a)

    seen = []
    real_chat = appmod._portal_chat_thread
    monkeypatch.setattr(appmod.db, "connect", _Counted)
    monkeypatch.setattr(appmod, "_portal_biofield_unlocked",
                        lambda e: seen.append(("unlock", open_now[0])) or True)
    monkeypatch.setattr(appmod, "_portal_chat_thread",
                        lambda e: seen.append(("chat", open_now[0])) or real_chat(e))
    assert _search(client, tok).status_code == 200
    assert seen == [("unlock", 0), ("chat", 0)]


def test_corpus_is_cached_per_email(env, monkeypatch):
    client, appmod = env
    tok_a = _seed_client(appmod, "alpha@example.com", "alphaonly")
    tok_b = _seed_client(appmod, "bravo@example.com", "bravoonly")
    monkeypatch.setattr(ps, "CORPUS_TTL_S", 60)
    calls = []
    real = appmod._portal_search_records
    monkeypatch.setattr(appmod, "_portal_search_records",
                        lambda e, c: calls.append(e) or real(e, c))
    for q in ("zep", "zeph", WORD):
        _search(client, tok_a, q=q)
    _search(client, tok_a, page=2)
    assert calls == ["alpha@example.com"]
    b = _search(client, tok_b).get_json()
    assert calls == ["alpha@example.com", "bravo@example.com"]
    assert "alphaonly" not in json.dumps(b)


def test_portal_me_session_sees_only_its_own_records(env, monkeypatch):
    client, appmod = env
    from dashboard import portal_identity as pi
    _seed_client(appmod, "alpha@example.com", "alphaonly")
    _seed_client(appmod, "bravo@example.com", "bravoonly")
    monkeypatch.setattr(appmod, "_client_login_enabled", lambda: True)
    cx = sqlite3.connect(appmod.LOG_DB)
    pi._ensure_people_table(cx)
    cx.execute("INSERT OR IGNORE INTO people (email, name, roles, created_at, updated_at) "
               "VALUES (?,?,?,?,?)", ("bravo@example.com", "B", '["client"]', "t", "t"))
    pid = cx.execute("SELECT id FROM people WHERE email=?", ("bravo@example.com",)).fetchone()[0]
    sess = pi.create_client_session(cx, pid, "bravo@example.com")
    cx.commit()
    cx.close()
    assert _search(client, "me").status_code == 404           # no session yet
    client.set_cookie("rm_portal_session", sess)
    r = _search(client, "me")
    assert r.status_code == 200
    recs = _records(r.get_json())
    assert len(recs) == 3 and all("bravoonly" in _text(x) for x in recs)
    assert "alphaonly" not in json.dumps(r.get_json())


def test_household_member_param_does_not_widen_search(env, monkeypatch):
    client, appmod = env
    from dashboard import household as hh
    tok_a = _seed_client(appmod, "alpha@example.com", "alphaonly")
    _seed_client(appmod, "bravo@example.com", "bravoonly")
    monkeypatch.setattr(appmod, "_household_view_enabled", lambda: True)
    cx = sqlite3.connect(appmod.LOG_DB)
    hh.init_household_tables(cx)
    hh.add_member(cx, "alpha@example.com", "bravo@example.com", relationship="child")
    assert hh.can_view(cx, "alpha@example.com", "bravo@example.com")
    cx.close()
    r = client.get(f"/api/portal/{tok_a}/site-search?q={WORD}&member=bravo@example.com")
    recs = _records(r.get_json())
    assert len(recs) == 3 and all("alphaonly" in _text(x) for x in recs)
    assert "bravoonly" not in json.dumps(r.get_json())
