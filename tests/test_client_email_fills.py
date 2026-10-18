"""Missing FileMaker client emails are filled only when unambiguous (Glen, 2026-10-16).

A client's FileMaker row had no email, so the Biofield tool's "Previously
dispensed" panel found none of her three legacy invoices. Glen: "Add other clients'
emails always when missing and when possible", and he approved the rule: exactly one
FileMaker client with that first and last name, one email from every source that knows
the name, and an email no other client already has.
"""
import sqlite3

import pytest

from dashboard import biofield_dispensed as disp
from dashboard import client_email_fills as cef
from dashboard import fmp_orders as fo

JOSIE = ("9001", "Josephine\nJosie", "Testerson", "")


def cand(source, name, email):
    return {"source": source, "key": cef.full_name_key(name), "email": email}


def test_josie_is_filled_from_e4l_through_her_second_first_name():
    out = cef.plan([JOSIE], [cand("e4l", "Josie Testerson", "josie.test@example.com")])
    assert out["fills"] == [{"fmp_client_id": "9001", "email": "josie.test@example.com", "source": "e4l"}]
    assert out["counts"]["from_e4l"] == 1


def test_a_surname_alone_never_fills():
    """A FileMaker client shares a surname with pet and family rows."""
    out = cef.plan([JOSIE, ("6001", "Lola", "Testerson", "")],
                   [cand("e4l", "Lola Smith", "x@y.com"), cand("people", "Testerson", "z@y.com")])
    assert out["fills"] == []


def test_a_name_two_filemaker_clients_share_is_ambiguous():
    clients = [("1", "Ann", "Lee", ""), ("2", "Ann", "Lee", "")]
    out = cef.plan(clients, [cand("people", "Ann Lee", "ann@x.com")])
    assert out["fills"] == [] and {a["fmp_client_id"] for a in out["ambiguous"]} == {"1", "2"}


def test_a_shared_name_is_ambiguous_even_when_only_one_row_lacks_an_email():
    """The other Ann Lee has her own email; the found one may be hers or a third person's."""
    clients = [("1", "Ann", "Lee", ""), ("2", "Ann", "Lee", "ann2@x.com")]
    out = cef.plan(clients, [cand("people", "Ann Lee", "ann@x.com")])
    assert out["fills"] == []
    assert out["ambiguous"] == [{"fmp_client_id": "1", "reason": "name shared by another FileMaker client"}]


def test_sources_that_disagree_are_ambiguous():
    out = cef.plan([JOSIE], [cand("e4l", "Josie Testerson", "a@x.com"),
                             cand("people", "Josie Testerson", "b@x.com")])
    assert out["fills"] == [] and out["ambiguous"][0]["reason"] == "sources give different emails"


def test_two_people_with_the_name_in_one_source_is_ambiguous():
    out = cef.plan([JOSIE], [cand("people", "Josie Testerson", "a@x.com"),
                             cand("people", "Josie Testerson", "b@x.com")])
    assert out["fills"] == []


def test_an_email_already_on_another_client_is_not_reused():
    out = cef.plan([JOSIE, ("7", "Sue", "Rae", "JOSIE.TEST@example.com")],
                   [cand("e4l", "Josie Testerson", "josie.test@example.com")])
    assert out["fills"] == [] and out["taken"][0]["fmp_client_id"] == "9001"


def test_one_email_found_for_two_clients_fills_neither():
    clients = [("1", "Ann", "Lee", ""), ("2", "Bob", "Lee", "")]
    out = cef.plan(clients, [cand("people", "Ann Lee", "lee@x.com"),
                             cand("people", "Bob Lee", "lee@x.com")])
    assert out["fills"] == [] and len(out["ambiguous"]) == 2


def test_a_client_with_an_email_or_already_filled_is_left_alone():
    out = cef.plan([("1", "Ann", "Lee", "ann@x.com"), JOSIE],
                   [cand("people", "Ann Lee", "other@x.com"),
                    cand("e4l", "Josie Testerson", "josie.test@example.com")],
                   filled={"9001": "josie.test@example.com"})
    assert out["fills"] == [] and out["counts"]["missing"] == 0


def test_an_invalid_email_is_never_a_candidate():
    out = cef.plan([JOSIE], [cand("e4l", "Josie Testerson", "not an email")])
    assert out["fills"] == []


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "f.db"))
    fo.ensure_tables(cx)
    cx.execute("INSERT INTO fmp_clients(id_pk,name_first,name_last,email) VALUES(?,?,?,?)", JOSIE)
    cx.execute("INSERT INTO fmp_invoices(id_pk,id_fk_client,invoice_date,status) "
               "VALUES('i1','9001','2014-09-12','Historical')")
    cx.commit()
    return cx


def test_the_order_lookup_finds_a_filled_client_by_email(tmp_path):
    cx = _db(tmp_path)
    assert fo.client_order_history(cx, email="josie.test@example.com", fills=False) == []
    cef.ensure_table(cx)
    assert cef.apply(cx, [{"fmp_client_id": "9001", "email": "josie.test@example.com", "source": "e4l"}]) == 1
    got = fo.client_order_history(cx, email="JOSIE.TEST@example.com", fills=True)
    assert [r["client"]["id"] for r in got] == ["9001"]
    assert [o["date"] for o in got[0]["orders"]] == ["2014-09-12"]
    # Applying again writes nothing.
    assert cef.apply(cx, [{"fmp_client_id": "9001", "email": "x@y.com", "source": "e4l"}]) == 0


def test_the_dispensed_filter_accepts_either_first_name_and_nothing_looser():
    a = "Josephine\nJosie Testerson"
    assert disp._same_person(a, "Josie Testerson") and disp._same_person(a, "josephine  testerson")
    for b in ("Testerson", "Josie", "Lola Testerson", ""):
        assert not disp._same_person(a, b)
    assert disp._same_person("Debra Herndon", "debra herndon")
    assert not disp._same_person("Debra Herndon", "Amy Herndon")


# ── The route ────────────────────────────────────────────────────────────────

@pytest.fixture()
def routed(tmp_path, monkeypatch):
    import app
    from dashboard import rbac
    path = str(tmp_path / "chat_log.db")
    cx = _db(tmp_path)
    cx.close()
    import shutil
    shutil.copy(str(tmp_path / "f.db"), path)
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, email TEXT, "
                  "first_name TEXT, last_name TEXT, name TEXT)")
        c.commit()
    monkeypatch.setattr(app, "LOG_DB", path)
    monkeypatch.setattr(app, "_bos_actor", lambda: rbac.Actor(role="owner", name="glen"))
    app.app.config["TESTING"] = True
    return app, app.app.test_client(), path


def test_the_route_is_a_dry_run_unless_committed(routed):
    app, client, path = routed
    body = {"candidates": [{"source": "e4l", "name": "Josie Testerson", "email": "josie.test@example.com"}]}
    r = client.post("/api/console/client-email-fills", json=body).get_json()
    assert r["committed"] is False and r["written"] == 0 and r["counts"]["fills"] == 1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM client_email_fills").fetchone()[0] == 0
    r = client.post("/api/console/client-email-fills?commit=1", json=body).get_json()
    assert r["written"] == 1
    got = client.get("/api/console/fmp-orders?email=josie.test@example.com").get_json()["results"]
    assert [x["client"]["id"] for x in got] == ["9001"]


def test_the_route_reads_the_people_table(routed):
    app, client, path = routed
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO people(email,first_name,last_name,name) VALUES(?,?,?,?)",
                  ("josie.test@example.com", "Josie", "Testerson", "Josie Testerson"))
        c.commit()
    r = client.post("/api/console/client-email-fills", json={}).get_json()
    assert r["fills"] == [{"fmp_client_id": "9001", "source": "people"}]   # no address back


def test_the_route_refuses_anyone_but_an_owner(routed, monkeypatch):
    app, client, _ = routed
    from dashboard import rbac
    monkeypatch.setattr(app, "_bos_actor", lambda: rbac.Actor(role="va", name="shaira"))
    assert client.post("/api/console/client-email-fills", json={}).status_code == 401


def test_a_posted_people_candidate_is_ignored(routed):
    """Only e4l and gmail may be posted; people is read on the server."""
    app, client, _ = routed
    body = {"candidates": [{"source": "people", "name": "Josie Testerson", "email": "a@x.com"}]}
    assert client.post("/api/console/client-email-fills", json=body).get_json()["fills"] == []


# ── The local Biofield tool reads prod's history ─────────────────────────────

def _author_app(tmp_path, monkeypatch, fetch):
    import dashboard as _d
    from biofield_local_app import create_app
    from dashboard import biofield_invoice as bi
    from dashboard.biofield_authoring import create_test, init_auth_tables
    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Josie Testerson", email="josie.test@example.com", date="2026-10-16")
    app = create_app(db_path=db, client_orders=lambda e: bi._OrdersResult([]),
                     fetch_fmp_history=fetch)
    app.testing = True
    return app.test_client(), tid


PROD = [{"client": {"id": "9001", "name": "Josephine\nJosie Testerson"},
         "orders": [{"id": "i1", "date": "2014-09-12", "status": "Historical",
                     "items": [{"description": "Adrenal Syntropy", "qty": 2}]}]}]


def test_the_panel_shows_prods_history_for_a_filled_client(tmp_path, monkeypatch):
    seen = []
    client, tid = _author_app(tmp_path, monkeypatch, lambda e: seen.append(e) or PROD)
    html = client.get(f"/author/{tid}").get_data(as_text=True)
    assert seen == ["josie.test@example.com"]
    assert "Adrenal Syntropy" in html
    assert "No order history for this client yet" not in html
    assert "History unavailable" not in html


def test_a_failed_prod_lookup_says_so_and_uses_the_local_copy(tmp_path, monkeypatch, capsys):
    client, tid = _author_app(tmp_path, monkeypatch, lambda e: None)
    html = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "History unavailable" in html
    line = [ln for ln in capsys.readouterr().out.splitlines() if "prod FileMaker history" in ln]
    assert line and f"test {tid}" in line[0] and "josie.test" not in line[0]


def test_the_default_fetcher_sends_the_key_in_the_header_and_returns_none_on_failure(monkeypatch):
    import io
    import json
    import biofield_local_app as bla
    seen = []

    def fake(req, timeout=None):
        seen.append(req)
        return io.BytesIO(json.dumps({"ok": True, "results": PROD}).encode())

    class _Opener:
        def __init__(self, fn):
            self.open = fn

    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr("urllib.request.build_opener", lambda *h: _Opener(fake))
    assert bla._default_fetch_fmp_history("JOSIE.TEST@example.com") == PROD
    assert "fake-key" not in seen[0].full_url and seen[0].get_header("X-console-key") == "fake-key"
    assert "email=josie.test%40example.com" in seen[0].full_url
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda *h: _Opener(lambda *a, **k: (_ for _ in ()).throw(OSError("down"))))
    assert bla._default_fetch_fmp_history("josie.test@example.com") is None


# ── Review rounds 1 and 2: a wrong email must never be attached ─────────────

@pytest.mark.parametrize("fmp,source_name", [
    (("1", "John", "Smith Jr.", ""), "John Brown Jr"),       # a suffix is not a surname
    (("1", "Ann", "Smith-Jones", ""), "Ann Jones"),          # half a double surname
    (("1", "Ann", "de Silva", ""), "Ann Silva"),
    (("1", "Mary", "Smith", ""), "Mary Beth Smith"),         # a middle name
    (("1", "Ann Lee", "Smith", ""), "Ann Smith"),
])
def test_only_the_whole_first_and_last_name_match(fmp, source_name):
    assert cef.plan([fmp], [cand("people", source_name, "x@y.com")])["fills"] == []


def test_a_hyphenated_surname_matches_itself():
    out = cef.plan([("1", "Ann", "Smith-Jones", "")], [cand("e4l", "Ann Smith-Jones", "a@x.com")])
    assert [f["fmp_client_id"] for f in out["fills"]] == ["1"]


def test_a_badly_typed_email_counts_as_on_file_and_its_addresses_as_taken():
    clients = [("1", "Ann", "Lee", "ann@x.com; b@y.com"), JOSIE]
    out = cef.plan(clients, [cand("people", "Ann Lee", "c@x.com"),
                             cand("e4l", "Josie Testerson", "b@y.com")])
    assert out["fills"] == [] and out["taken"][0]["fmp_client_id"] == "9001"


def test_a_later_source_that_contradicts_a_fill_is_reported_not_applied():
    out = cef.plan([JOSIE], [cand("people", "Josie Testerson", "other@x.com")],
                   filled={"9001": "josie.test@example.com"})
    assert out["fills"] == []
    assert out["conflicts"] == [{"fmp_client_id": "9001",
                                 "reason": "a source now gives a different email"}]


def test_the_database_refuses_one_email_for_two_clients(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "u.db"))
    cef.ensure_table(cx)
    assert cef.apply(cx, [{"fmp_client_id": "1", "email": "a@x.com", "source": "e4l"}]) == 1
    assert cef.apply(cx, [{"fmp_client_id": "2", "email": "A@x.com", "source": "e4l"}]) == 0


def test_a_fill_stops_counting_once_an_ingest_brings_a_real_email(tmp_path):
    cx = _db(tmp_path)
    cef.ensure_table(cx)
    cef.apply(cx, [{"fmp_client_id": "9001", "email": "josie.test@example.com", "source": "e4l"}])
    cx.execute("UPDATE fmp_clients SET email='josie@new.com' WHERE id_pk='9001'")
    assert fo.client_order_history(cx, email="josie.test@example.com", fills=True) == []
    cx.execute("UPDATE fmp_clients SET email='' WHERE id_pk='9001'")
    cx.execute("INSERT INTO fmp_clients(id_pk,name_first,name_last,email) "
               "VALUES('9','Other','Person','josie.test@example.com')")
    got = fo.client_order_history(cx, email="josie.test@example.com", fills=True)
    assert [r["client"]["id"] for r in got] == ["9"]


def test_a_two_person_line_builds_no_new_name():
    assert not disp._same_person("Ann Lee\nBob Smith", "Ann Smith")


def test_the_default_fetcher_never_sends_the_key_over_plain_http(monkeypatch):
    import biofield_local_app as bla
    called = []
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://example.com")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: called.append(a))
    assert bla._default_fetch_fmp_history("a@x.com") is None and called == []


def test_the_default_fetcher_does_not_follow_a_redirect(monkeypatch):
    import http.server
    import threading
    import biofield_local_app as bla

    class H(http.server.BaseHTTPRequestHandler):
        hits = []

        def do_GET(self):
            H.hits.append((self.path, self.headers.get("X-Console-Key")))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
        monkeypatch.setenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
        assert bla._default_fetch_fmp_history("a@x.com") is None
        assert [p for p, _ in H.hits if p.startswith("/elsewhere")] == []
    finally:
        srv.shutdown()


def test_tests_never_reach_prod_even_with_a_key_and_no_ci(tmp_path, monkeypatch):
    import biofield_local_app as bla
    import dashboard as _d
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)   # the page gate only
    called = []
    monkeypatch.setattr(bla, "_default_fetch_fmp_history", lambda e: called.append(e) or [])
    from dashboard import biofield_invoice as bi
    from dashboard.biofield_authoring import create_test, init_auth_tables
    db = str(tmp_path / "t.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, name="Josie Testerson", email="josie.test@example.com", date="2026-10-16")
    app = bla.create_app(db_path=db, client_orders=lambda e: bi._OrdersResult([]))
    app.testing = True
    c = app.test_client()
    r = c.get(f"/author/{tid}", headers={"X-Console-Key": "fake-key"})
    if r.status_code == 401:
        r = c.get(f"/author/{tid}?key=fake-key")
    assert r.status_code == 200 and called == []


@pytest.mark.parametrize("fmp,source_name", [
    (("1", "John", "Smith Sr", ""), "John Smith"),       # father and son (review round 3)
    (("1", "John", "Smith", ""), "John Smith Jr"),
    (("1", "John", "Smith", ""), "Mrs John Smith"),      # his wife's address
    (("1", "Dr", "Smith", ""), "Dr Smith"),
])
def test_generations_and_titles_never_fill(fmp, source_name):
    assert cef.plan([fmp], [cand("people", source_name, "x@y.com")])["fills"] == []


def test_a_degree_on_the_filemaker_surname_is_ignored():
    out = cef.plan([("1", "John", "Smith MD", "")], [cand("people", "John Smith", "js@x.com")])
    assert [f["fmp_client_id"] for f in out["fills"]] == ["1"]


def test_an_older_people_table_with_one_name_field_still_works():
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE people(email TEXT, name TEXT)")
    cx.execute("INSERT INTO people VALUES('a@x.com','Ann Lee')")
    assert cef.people_candidates(cx) == [{"source": "people", "key": ("ann", "lee"),
                                          "email": "a@x.com"}]


def test_a_lookalike_localhost_never_gets_the_key(monkeypatch):
    import biofield_local_app as bla
    called = []
    monkeypatch.setenv("CONSOLE_SECRET", "fake-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1.evil.com")
    monkeypatch.setattr("urllib.request.build_opener", lambda *h: called.append(h))
    assert bla._default_fetch_fmp_history("a@x.com") is None and called == []


def test_a_two_word_line_is_never_glued_to_the_surname():
    assert not disp._same_person("Ann Lee\nBob Smith", "Ann Lee Smith")
