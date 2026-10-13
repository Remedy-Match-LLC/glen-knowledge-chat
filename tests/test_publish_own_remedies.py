"""A client's own remedy can be published once Glen confirms it (Glen, 2026-10-13).

Linda Maher's report carries "Passion Flower Capsule", her own remedy, which the store
does not sell. Publishing refused the whole report, so her portal kept the old program.
A bare "48" also built one client's PDF and another client's portal content.
"""
import sqlite3

import biofield_local_app
from dashboard import biofield_portal_publish as bpp
from dashboard.biofield_authoring import create_test, add_chain_row


def _setup(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    cx = sqlite3.connect(db)
    aid = create_test(cx, "Linda", "l@example.com", "2026-10-12")
    tid = aid.lstrip("a")
    add_chain_row(cx, aid, layer=1, head="ED3", most_affected="Circ",
                  remedy="Vitality", dosage="1 cap", frequency="daily", timing="")
    add_chain_row(cx, aid, layer=2, head="Liver", most_affected="Liver",
                  remedy="Passion Flower Capsule", dosage="", frequency="", timing="")
    cx.commit()
    cx.close()
    sent = []
    monkeypatch.setattr(bpp, "load_catalog", lambda: {"vitality": {"name": "Vitality"}})
    monkeypatch.setattr(bpp, "upload_asset",
                        lambda data, name, **kw: f"https://illtowell.com/portal-asset/{name}")
    monkeypatch.setattr(bpp, "publish_to_portal",
                        lambda payload, **kw: sent.append((payload, kw)) or
                        {"ok": True, "updated": True, "note": "existing portal updated"})
    monkeypatch.setattr(biofield_local_app, "report_pdf_bytes", lambda html: b"%PDF-FAKE")
    monkeypatch.setenv("PORTAL_PUBLISH_BASE_URL", "https://illtowell.com")
    monkeypatch.setenv("CONSOLE_SECRET", "")
    app = biofield_local_app.create_app(db_path=db)
    return app.test_client(), aid, tid, sent


def test_an_unsold_remedy_still_refuses_without_confirmation(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    r = c.post(f"/test/{aid}/publish-portal", json={})
    assert r.status_code == 409
    assert r.get_json()["unresolved"] == ["Passion Flower Capsule"]
    assert sent == []


def test_a_confirmed_own_remedy_shows_on_its_layer_with_no_cart_line(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    r = c.post(f"/test/{aid}/publish-portal",
               json={"own_remedies": ["Passion Flower Capsule"]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["own_remedies"] == ["Passion Flower Capsule"]
    content = sent[0][0]["content"]
    assert "Passion Flower Capsule" in [L["remedy"] for L in content["layers"]]
    assert [i["slug"] for i in content["reorder_items"]] == ["vitality"]


def test_confirming_one_name_does_not_pass_another(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    r = c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower"]})
    assert r.status_code == 409 and sent == []


def test_a_confirmation_is_remembered_for_later_silent_publishes(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower Capsule"]})
    r = c.post(f"/test/{aid}/publish-portal", json={})
    assert r.status_code == 200 and len(sent) == 2


def test_a_confirmation_belongs_to_one_report(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower Capsule"]})
    db = str(tmp_path / "t.db")
    cx = sqlite3.connect(db)
    other = create_test(cx, "Steve", "s@example.com", "2026-10-12")
    add_chain_row(cx, other, layer=1, head="Liver", most_affected="Liver",
                  remedy="Passion Flower Capsule", dosage="", frequency="", timing="")
    cx.commit()
    cx.close()
    assert c.post(f"/test/{other}/publish-portal", json={}).status_code == 409


def test_a_bare_number_is_never_published(tmp_path, monkeypatch):
    c, aid, tid, sent = _setup(tmp_path, monkeypatch)
    r = c.post(f"/test/{tid}/publish-portal",
               json={"own_remedies": ["Passion Flower Capsule"]})
    assert r.status_code == 400 and sent == []


def test_own_remedies_must_be_a_list_of_names(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    r = c.post(f"/test/{aid}/publish-portal", json={"own_remedies": "Passion Flower Capsule"})
    assert r.status_code == 400 and sent == []


def _button_run(confirm_answer):
    import json
    import subprocess
    from tests.test_blank_courtesy_price import _node_js
    harness = """
var el={textContent:'',appendChild:function(n){el.textContent+=n;}}, posts=[], asked=[];
var document={getElementById:function(){return el;},createTextNode:function(t){return t;}};
function prompt(){return '';}
function confirm(m){asked.push(m);return ANSWER;}
async function fetch(u,o){var b=JSON.parse(o.body);posts.push(b.own_remedies);
  return {json:async function(){
    return (b.own_remedies.length ? {ok:true,url:'',note:'Portal updated.',own_remedies:b.own_remedies}
                                  : {ok:false,unresolved:['Passion Flower Capsule']});}};}
""".replace("ANSWER", confirm_answer)
    js = harness + _node_js() + (
        "\npublishPortal().then(function(){console.log(JSON.stringify("
        "{posts:posts,asked:asked.length,msg:el.textContent}));});")
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True,
                                     check=True).stdout)


def test_the_button_names_the_remedy_and_republishes_after_ok():
    out = _button_run("true")
    assert out["posts"] == [[], ["Passion Flower Capsule"]] and out["asked"] == 1
    assert "Shown with no buy button: Passion Flower Capsule" in out["msg"]


def test_cancel_publishes_nothing_more():
    out = _button_run("false")
    assert out["posts"] == [[]]
    assert out["msg"].startswith("Not published.") and "Passion Flower Capsule" in out["msg"]
