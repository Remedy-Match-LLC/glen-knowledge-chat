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


def test_printing_later_publishes_silently_with_the_remembered_confirmation(
        tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    assert c.post(f"/test/{aid}/publish-portal",
                  json={"own_remedies": ["Passion Flower Capsule"]}).status_code == 200
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(biofield_local_app, "save_report_pdf",
                        lambda html, out: open(out, "wb").write(b"%PDF-FAKE"))
    assert c.get(f"/test/{aid}/report.pdf").status_code == 200
    assert len(sent) == 2
    assert sent[1][1]["send"] is False and sent[1][1]["send_if_new"] is True


def test_a_remedy_added_while_publishing_is_not_sent(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)
    real = bpp.build_portal_content
    calls = {"n": 0}

    def changing(*a, **kw):
        calls["n"] += 1
        out = real(*a, **kw)
        if calls["n"] == 2:            # the report was edited after the first check
            out["unresolved"] = out["unresolved"] + ["Vitalty"]
        return out
    monkeypatch.setattr(bpp, "build_portal_content", changing)
    r = c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower Capsule"]})
    assert r.status_code == 409 and r.get_json()["unresolved"] == ["Vitalty"] and sent == []


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


def test_printing_a_bare_number_never_reaches_the_portal(tmp_path, monkeypatch):
    c, aid, tid, sent = _setup(tmp_path, monkeypatch)
    c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower Capsule"]})
    # Confirm it under the bare key too, so only the id guard can stop this publish.
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    cx.execute("INSERT INTO biofield_own_remedies VALUES(?,?,?)",
               (tid, "Passion Flower Capsule", "now"))
    cx.commit()
    cx.close()
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(biofield_local_app, "save_report_pdf",
                        lambda html, out: open(out, "wb").write(b"%PDF-FAKE"))
    monkeypatch.setattr(biofield_local_app, "causal_chain_report",
                        lambda cx, t: {"client": {}, "layers": [], "date": ""}, raising=False)
    monkeypatch.setattr(biofield_local_app, "render_present", lambda rep, narr, intro=(): "<html></html>")
    assert c.get(f"/test/{tid}/report.pdf").status_code == 200   # the print itself works
    assert len(sent) == 1, "the bare number must not publish"


def test_the_confirmation_is_kept_when_the_publish_fails(tmp_path, monkeypatch):
    c, aid, _, sent = _setup(tmp_path, monkeypatch)

    def down(payload, **kw):
        raise RuntimeError("portal down")
    monkeypatch.setattr(bpp, "publish_to_portal", down)
    r = c.post(f"/test/{aid}/publish-portal", json={"own_remedies": ["Passion Flower Capsule"]})
    assert r.status_code == 502
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    assert cx.execute("SELECT remedy FROM biofield_own_remedies").fetchall() == [
        ("Passion Flower Capsule",)]


def test_the_button_asks_only_once():
    import json
    import subprocess
    from tests.test_blank_courtesy_price import _node_js
    harness = """
var el={textContent:''}, posts=[], asked=0;
var document={getElementById:function(){return el;}};
function prompt(){return '';}
function confirm(){asked++;return true;}
async function fetch(u,o){posts.push(1);
  return {json:async function(){return {ok:false,unresolved:['Passion Flower Capsule']};}};}
"""
    js = harness + _node_js() + (
        "\npublishPortal().then(function(){console.log(JSON.stringify("
        "{posts:posts.length,asked:asked,msg:el.textContent}));});")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True,
                                    check=True).stdout)
    assert out == {"posts": 2, "asked": 1, "msg": out["msg"]}
    assert out["msg"].startswith("Not published.")
