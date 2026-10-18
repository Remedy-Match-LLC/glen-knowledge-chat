"""Links Glen adds to one client's portal Library (Glen, 2026-10-13).

Linda Maher's Constitutional Hydrotherapy and Titration protocol pages were the first.
"""
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dashboard import portal_library as lib

URL = "https://example.com/protocols/constitutional-hydrotherapy"


def _cx():
    cx = sqlite3.connect(":memory:")
    lib.init_links_table(cx)
    return cx


@pytest.mark.parametrize("bad", ["http://example.com/x", "javascript:alert(1)",
                                 "https://", "example.com/x", 'https://e.com/"x',
                                 "https://e.com/<x>", ""])
def test_only_a_full_https_address_is_stored(bad):
    with pytest.raises(ValueError):
        lib.add_link(_cx(), "l@x.com", bad, "Titration")


def test_a_link_needs_a_title():
    with pytest.raises(ValueError):
        lib.add_link(_cx(), "l@x.com", URL, "   ")


def test_adding_twice_keeps_one_and_remove_takes_it_away():
    cx = _cx()
    assert lib.add_link(cx, "L@X.com", URL, " Constitutional  Hydrotherapy ") is True
    assert lib.add_link(cx, "l@x.com", URL, "Constitutional Hydrotherapy") is False
    assert [l["title"] for l in lib.links_for_email(cx, "l@x.com")] == ["Constitutional Hydrotherapy"]
    assert lib.links_for_email(cx, "other@x.com") == []
    assert lib.remove_link(cx, "l@x.com", URL) is True
    assert lib.links_for_email(cx, "l@x.com") == []


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_HUB_ENABLED", "1")
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "s3cret")
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    tok = cp.ensure_token(cx, "l@x.com", "L")
    cx.commit()
    cx.close()
    return appmod, appmod.app.test_client(), tok


def test_staff_add_a_link_and_it_shows_in_her_library(tmp_path, monkeypatch):
    appmod, c, tok = _app(tmp_path, monkeypatch)
    body = {"email": "l@x.com", "url": URL, "title": "Constitutional Hydrotherapy"}
    assert c.post("/api/console/portal-library-links", json=body).status_code == 401
    r = c.post("/api/console/portal-library-links", json=body,
               headers={"X-Console-Key": "s3cret"})
    assert r.status_code == 200 and r.get_json()["changed"] is True
    items = c.get(f"/api/portal/{tok}/library").get_json()["items"]
    assert {"kind": "link", "title": "Constitutional Hydrotherapy", "url": URL} == {
        k: items[0][k] for k in ("kind", "title", "url")}


def test_a_bad_link_is_refused_with_its_reason(tmp_path, monkeypatch):
    appmod, c, tok = _app(tmp_path, monkeypatch)
    r = c.post("/api/console/portal-library-links", headers={"X-Console-Key": "s3cret"},
               json={"email": "l@x.com", "url": "javascript:alert(1)", "title": "x"})
    assert r.status_code == 400 and "https" in r.get_json()["error"]


def _render(items):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = Path(__file__).resolve().parent.parent / "static" / "js" / "portal-library.js"
    out = subprocess.run(["node", "-e", "const m=require(%s);console.log(JSON.stringify(m.renderLibrary(%s)))"
                          % (json.dumps(str(js)), json.dumps(items))],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_library_renders_a_link_that_opens_in_a_new_tab():
    html = _render([{"kind": "link", "title": "Titration <b>", "url": URL}])
    assert '<a class="lib-read" href="%s" target="_blank" rel="noopener noreferrer">Open</a>' % URL in html
    assert "Titration &lt;b&gt;" in html


def test_a_non_https_link_is_never_rendered_as_an_anchor():
    html = _render([{"kind": "link", "title": "x", "url": "javascript:alert(1)"}])
    assert "href" not in html and "javascript" not in html


def test_an_uppercase_scheme_is_stored_lowercase_so_the_page_shows_it():
    cx = _cx()
    lib.add_link(cx, "l@x.com", "HTTPS://example.com/Titration", "Titration")
    assert lib.links_for_email(cx, "l@x.com")[0]["url"] == "https://example.com/Titration"


@pytest.mark.parametrize("body", [[1, 2], {"email": 5, "url": URL, "title": "x"},
                                  {"email": "l@x.com", "url": URL, "title": 7}])
def test_a_malformed_body_is_refused_not_crashed(tmp_path, monkeypatch, body):
    appmod, c, tok = _app(tmp_path, monkeypatch)
    r = c.post("/api/console/portal-library-links", json=body,
               headers={"X-Console-Key": "s3cret"})
    assert r.status_code == 400


def test_a_broken_link_store_still_serves_the_library(tmp_path, monkeypatch):
    appmod, c, tok = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(lib, "links_for_email", lambda *a: (_ for _ in ()).throw(RuntimeError("db")))
    r = c.get(f"/api/portal/{tok}/library")
    assert r.status_code == 200 and r.get_json()["items"] == []


def test_staff_can_list_and_remove_a_link_by_mixed_case_email(tmp_path, monkeypatch):
    appmod, c, tok = _app(tmp_path, monkeypatch)
    h = {"X-Console-Key": "s3cret"}
    c.post("/api/console/portal-library-links", headers=h,
           json={"email": " L@X.com ", "url": URL, "title": "Titration"})
    got = c.get("/api/console/portal-library-links?email=l@x.com", headers=h).get_json()
    assert [l["url"] for l in got["links"]] == [URL]
    r = c.post("/api/console/portal-library-links", headers=h,
               json={"email": "l@x.com", "url": URL, "action": "remove"})
    assert r.get_json()["changed"] is True
    assert c.get(f"/api/portal/{tok}/library").get_json()["items"] == []


def test_a_link_failure_comes_after_every_other_library_query(tmp_path, monkeypatch):
    """On Postgres a failed query poisons the transaction, so the link lookup must be
    the last query the Library route makes."""
    appmod, c, tok = _app(tmp_path, monkeypatch)
    from dashboard import course_entitlements as ce
    order = []
    real_links, real_paid = lib.links_for_email, ce.paid_level_for
    monkeypatch.setattr(lib, "links_for_email", lambda *a: order.append("links") or real_links(*a))
    monkeypatch.setattr(ce, "paid_level_for", lambda *a: order.append("course") or real_paid(*a))
    c.get(f"/api/portal/{tok}/library")
    assert order == ["course", "links"]


def test_remove_matches_an_uppercase_scheme():
    cx = _cx()
    lib.add_link(cx, "l@x.com", URL, "Titration")
    assert lib.remove_link(cx, "l@x.com", URL.replace("https", "HTTPS", 1)) is True


def test_a_client_merge_moves_her_links_and_keeps_one_of_a_shared_link(tmp_path):
    # CI: a new table with an address column must be classified for client merges.
    from dashboard import person_aliases as pa
    from dashboard import person_merge as pm
    from tests.test_person_merge_engine import AOL, DDL, GMAIL, _fields
    cx = sqlite3.connect(str(tmp_path / "m.db"))
    cx.execute(DDL[0])                                   # people only
    cx.execute("INSERT INTO people VALUES (1, ?, 'Mel', '[]')", (AOL,))
    cx.execute("INSERT INTO people VALUES (2, ?, 'Mel', '[]')", (GMAIL,))
    pa.init_tables(cx)
    lib.init_links_table(cx)
    lib.add_link(cx, AOL, URL, "Constitutional Hydrotherapy")
    lib.add_link(cx, AOL, "https://example.com/titration", "Titration")
    lib.add_link(cx, GMAIL, URL, "Hydrotherapy, survivor's copy")
    cx.commit()

    def titles(email):
        return sorted(l["title"] for l in lib.links_for_email(cx, email))
    before = (titles(AOL), titles(GMAIL))
    mid = pm.apply(cx, survivor_id=2, merged_id=1, mail_old="stop", evidence={}, suggestion={},
                   applied_by="test", merge_people_fields=_fields)
    cx.commit()
    assert titles(AOL) == []
    assert titles(GMAIL) == ["Hydrotherapy, survivor's copy", "Titration"]   # review round 1
    report = pm.undo(cx, mid, "test")
    cx.commit()
    assert report["skipped"] == [] and (titles(AOL), titles(GMAIL)) == before
