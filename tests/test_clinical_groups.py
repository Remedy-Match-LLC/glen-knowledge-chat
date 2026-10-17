"""Named groups of stress factors, each one a Clinical Summary item (Glen, 2026-10-16).

"Make a process to add proposed stress factors to a Clinical Summary Item: giving the
summary item a name, and being able to add more than one factor into the same summary
pattern." Then: "Propose those groupings and names for approval or editing." The
factors stay a list of their own: he answered "separate".
"""
import json
import sqlite3

import pytest

from dashboard import biofield_clinical_groups as cg
from dashboard.biofield_clinical_proposals import accepted_labels
from dashboard.biofield_stress import add_stress, init_stress_tables

T = "a7"


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    init_stress_tables(c)
    cg.ensure_schema(c)
    return c


def _stress(cx, label, source="comm"):
    add_stress(cx, T, label, source=source)


def _reply(*groups):
    return lambda system, user: json.dumps({"groups": [
        {"name": n, "factors": f, "why": "shared"} for n, f in groups]})


def test_factors_are_the_mind_stresses_then_pending_records(cx):
    _stress(cx, "cataract")
    _stress(cx, "dry eyes", "tag")
    _stress(cx, "Liver toxicity", "scan")       # Body: not a Clinical Summary factor
    _stress(cx, "grief", "voice")               # Spirit: not one either
    records = [{"kind": "result", "text": "Ferritin 447 ng/mL High"},
               {"kind": "procedure", "text": "Dilated Exam"},
               {"kind": "diagnosis", "text": "Cataract"}]   # same key as the stress
    got = cg.factors_for(cx, T, records)
    assert got == [{"factor": "cataract", "kind": "stress"},
                   {"factor": "dry eyes", "kind": "stress"},
                   {"factor": "Ferritin 447 ng/mL High", "kind": "record"}]


def test_a_shared_address_contributes_no_records(cx):
    _stress(cx, "cataract")
    assert [f["factor"] for f in cg.factors_for(cx, T, None)] == ["cataract"]


def test_the_reply_may_only_cite_factors_it_was_shown():
    factors = [{"factor": "a", "kind": "stress"}, {"factor": "b", "kind": "stress"}]
    raw = json.dumps({"groups": [
        {"name": "One", "factors": [1, 1, 9, True, "2"]},
        {"name": "Two", "factors": [1, 2]},
        {"name": "  ", "factors": [2]},
        {"name": "Empty", "factors": [7]}]})
    got = cg.parse_reply(raw, factors)
    assert [(g["name"], [f["factor"] for f in g["factors"]]) for g in got] == [
        ("One", ["a"]), ("Two", ["b"])]
    assert cg.parse_reply(json.dumps({"groups": []}), factors) == []
    for raw in ("not json", json.dumps({"groups": "x"}), json.dumps([1])):
        with pytest.raises(cg.ModelReplyError):
            cg.parse_reply(raw, factors)


def test_propose_makes_open_proposals_and_approves_nothing(cx):
    _stress(cx, "cataract")
    _stress(cx, "cloudy vision")
    _stress(cx, "fatty liver")
    out = cg.propose(cx, T, cg.factors_for(cx, T), _reply(("Cataract and vision", [1, 2])))
    assert out == {"proposed": 1, "ungrouped": 1}
    [g] = cg.groups(cx, T)
    assert g["status"] == "proposed" and g["name"] == "Cataract and vision"
    assert accepted_labels(cx, T) == []


def test_approve_takes_the_edited_name_and_factors(cx):
    for s in ("cataract", "cloudy vision", "fatty liver"):
        _stress(cx, s)
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Eyes", [1, 2]), ("Liver", [3])))
    eyes, liver = cg.groups(cx, T)
    # Glen renames it, unticks one factor and moves the liver factor in.
    gid = cg.approve(cx, T, factors, "Cataract and vision",
                     ["cataract", "fatty liver"], group_id=eyes["id"])
    assert gid is not None
    assert cg.item_factors(cx, T) == {"cataract and vision": ["cataract", "fatty liver"]}
    assert accepted_labels(cx, T) == ["Cataract and vision"]
    # The liver proposal lost its only factor, so it is gone. The unticked factor and
    # the old eyes proposal are free again.
    assert [g["status"] for g in cg.groups(cx, T)] == ["approved"]
    assert [f["factor"] for f in cg.ungrouped(cx, T, factors)] == ["cloudy vision"]


def test_an_existing_item_name_gains_the_factors(cx):
    for s in ("cataract", "cloudy vision"):
        _stress(cx, s)
    factors = cg.factors_for(cx, T)
    first = cg.approve(cx, T, factors, "Cataract and vision", ["cataract"])
    again = cg.approve(cx, T, factors, "cataract AND vision", ["cloudy vision"])
    assert first == again
    assert cg.item_factors(cx, T) == {"cataract and vision": ["cataract", "cloudy vision"]}


def test_a_factor_lives_in_one_item(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.approve(cx, T, factors, "Blood", ["anemia"])
    cg.approve(cx, T, factors, "Circulation", ["anemia"])
    assert cg.item_factors(cx, T) == {"blood": [], "circulation": ["anemia"]}


def test_approve_refuses_what_is_not_this_intakes(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    assert cg.approve(cx, T, factors, "Blood", ["something invented"]) is None
    assert cg.approve(cx, T, factors, "  ", ["anemia"]) is None
    assert cg.approve(cx, T, factors, "Blood", ["anemia"], group_id=999) is None
    # Another intake's proposal cannot be approved from this one.
    cg.propose(cx, "a8", factors, _reply(("Blood", [1])))
    [other] = cg.groups(cx, "a8")
    assert cg.approve(cx, T, factors, "Blood", ["anemia"], group_id=other["id"]) is None
    assert accepted_labels(cx, T) == []


def test_a_dismissed_grouping_is_not_proposed_again(cx):
    for s in ("cardio failure", "diabetes", "anemia"):
        _stress(cx, s)
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Family history", [1, 2])))
    [g] = cg.groups(cx, T)
    assert cg.dismiss(cx, T, g["id"])
    seen = []
    cg.propose(cx, T, factors, lambda s, u: seen.append(u) or json.dumps({"groups": []}))
    assert seen == ["1. anemia"]


def test_propose_again_replaces_open_proposals_and_keeps_approved(cx):
    for s in ("cataract", "anemia"):
        _stress(cx, s)
    factors = cg.factors_for(cx, T)
    cg.approve(cx, T, factors, "Eyes", ["cataract"])
    cg.propose(cx, T, factors, _reply(("Blood", [1])))
    cg.propose(cx, T, factors, _reply(("Blood and circulation", [1])))
    assert [(g["name"], g["status"]) for g in cg.groups(cx, T)] == [
        ("Eyes", "approved"), ("Blood and circulation", "proposed")]


def test_nothing_left_to_group_asks_no_model(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.approve(cx, T, factors, "Blood", ["anemia"])

    def boom(s, u):
        raise AssertionError("asked the model")
    assert cg.propose(cx, T, factors, boom) == {"proposed": 0, "ungrouped": 0}


def test_remove_factor_frees_it(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.approve(cx, T, factors, "Blood", ["anemia"])
    assert cg.remove_factor(cx, T, "Blood", "ANEMIA")
    assert not cg.remove_factor(cx, T, "Blood", "anemia")
    assert [f["factor"] for f in cg.ungrouped(cx, T, factors)] == ["anemia"]


# --- the routes and the page ---------------------------------------------------------

from biofield_local_app import create_app  # noqa: E402

_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _complete(system, user):
    if "Clinical Summary" in system:
        return json.dumps({"groups": [{"name": "Cataract and vision",
                                       "factors": [1, 2], "why": "Both are the lens."}]})
    return json.dumps({"stresses": []})


def _app(tmp_path):
    db = str(tmp_path / "chat.db")
    e4l = tmp_path / "e4l.db"
    e = sqlite3.connect(str(e4l))
    e.execute("CREATE TABLE e4l_clients(client_id INTEGER, name TEXT, email TEXT)")
    e.commit(); e.close()
    app = create_app(db, scan_lookup=lambda e: _NONE, fetch_profile=lambda e: {},
                     fetch_recent_comms=lambda e: {}, e4l_db=str(e4l),
                     interpret_complete=_complete,
                     client_gmail=lambda cx, e: {"text": "", "held": 0, "error": None},
                     records_reader=None)
    c = app.test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    c.post(f"/author/{tid}/header", json={"name": "Pat Testerson",
                                          "email": "pat.test@example.com",
                                          "date": "2026-10-16"})
    with sqlite3.connect(db) as x:
        for s in ("cataract", "cloudy vision", "<img src=x onerror=alert(1)>"):
            add_stress(x, tid, s, source="comm")
    return c, tid


def test_propose_approve_and_the_item_shows_its_factors(tmp_path):
    c, tid = _app(tmp_path)
    j = c.get(f"/author/{tid}/clinical-groups").get_json()
    assert j["ok"] and j["proposed"] == [] and len(j["ungrouped"]) == 3
    j = c.post(f"/author/{tid}/clinical-groups/propose").get_json()
    assert j == {"ok": True, "proposed": 1, "ungrouped": 1}
    [g] = c.get(f"/author/{tid}/clinical-groups").get_json()["proposed"]
    j = c.post(f"/author/{tid}/clinical-groups/approve",
               json={"group_id": g["id"], "name": "Lens", "factors": ["cataract",
                                                                      "cloudy vision"]}).get_json()
    assert j["ok"]
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    row = page[page.index('data-label="Lens"'):]
    row = row[:row.index("clinical-remove")]
    assert "Factors: <span class=clinical-factor>cataract" in row
    assert "cloudy vision" in row
    # The free factor is offered, escaped.
    assert "&lt;img src=x onerror=alert(1)&gt;" in row
    assert "<img src=x" not in page


def test_routes_refuse_bad_input(tmp_path):
    c, tid = _app(tmp_path)
    assert c.get("/author/a999/clinical-groups").status_code == 404
    assert c.get("/author/zz/clinical-groups").status_code == 404
    r = c.post(f"/author/{tid}/clinical-groups/approve", json={"name": "X",
                                                               "factors": ["nope"]})
    assert r.status_code == 400
    r = c.post(f"/author/{tid}/clinical-groups/approve",
               json={"group_id": True, "name": "X", "factors": ["cataract"]})
    assert r.status_code == 400
    assert c.post(f"/author/{tid}/clinical-groups/5/dismiss").status_code == 400


def test_a_model_failure_is_reported_not_raised(tmp_path, monkeypatch):
    c, tid = _app(tmp_path)

    def boom(system, user):
        raise RuntimeError("down")
    import biofield_local_app as bla
    db = str(tmp_path / "x.db")
    app = bla.create_app(db, scan_lookup=lambda e: _NONE, fetch_profile=lambda e: {},
                         fetch_recent_comms=lambda e: {}, interpret_complete=boom,
                         client_gmail=None, records_reader=None)
    c2 = app.test_client()
    tid2 = c2.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    with sqlite3.connect(db) as x:
        add_stress(x, tid2, "anemia", source="comm")
    j = c2.post(f"/author/{tid2}/clinical-groups/propose").get_json()
    assert j["ok"] is False and "RuntimeError" in j["error"]


_FAKE_DOM = r"""
function Node(tag){this.tag=tag;this.children=[];this.attrs={};this.className='';
 this._text='';this.value='';this.checked=false;this.type='';this.disabled=false}
Node.prototype.appendChild=function(c){this.children.push(c);return c};
Node.prototype.setAttribute=function(k,v){this.attrs[k]=String(v)};
Node.prototype.getAttribute=function(k){return this.attrs[k]};
Object.defineProperty(Node.prototype,'textContent',{get:function(){return this._text},
 set:function(v){this._text=String(v);this.children=[]}});
Node.prototype.all=function(){var out=[];this.children.forEach(function(c){
 out.push(c);out=out.concat(c.all?c.all():[])});return out};
Node.prototype.querySelectorAll=function(sel){return this.all().filter(function(n){
 return sel==='input'&&n.tag==='input'})};
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function html(n){if(n.tag==='#text')return esc(n._text);
 return '<'+n.tag+(n.className?' class='+n.className:'')+'>'+esc(n._text)
  +n.children.map(html).join('')+'</'+n.tag+'>'}
var box=new Node('div'),sent=[];
global.document={getElementById:function(){return box},
 createElement:function(t){return new Node(t)},
 createTextNode:function(t){var n=new Node('#text');n._text=t;return n},
 querySelectorAll:function(){return []}};
global.location={pathname:'/author/a1',reload:function(){sent.push('reload')}};
global.alert=function(){};global.window=global;
"""


def test_the_box_renders_hostile_factors_as_text_and_sends_them_raw():
    import os
    import shutil
    import subprocess
    from dashboard.biofield_report_html import _GROUPS_HTML
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI for this test")
        pytest.skip("needs node")
    js = _GROUPS_HTML.split("<script>")[1].split("</script>")[0]
    bad = '"><img src=x onerror=alert(1)>'
    reply = {"ok": True,
             "proposed": [{"id": 4, "name": bad, "why": bad, "status": "proposed",
                           "factors": [{"factor": bad, "kind": "stress"}]}],
             "ungrouped": [{"factor": "anemia", "kind": "stress"}]}
    harness = (_FAKE_DOM +
               "global.fetch=async function(p,o){if(o)sent.push([p,JSON.parse(o.body)]);"
               "return {json:async function(){return o?{ok:true}:" + json.dumps(reply)
               + "}}};" + js +
               ";setTimeout(async function(){var b=box.all().filter(function(n){"
               "return n.tag==='button'&&n._text==='Approve'})[0];await b.onclick();"
               "var name=box.all().filter(function(n){return n.tag==='input'})[0];"
               "process.stdout.write(html(box)+'|||'+name.value+'|||'+JSON.stringify(sent))},50);")
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True,
                         timeout=20)
    page, name, sent = out.stdout.split("|||")
    assert "<img" not in page and "&lt;img src=x" in page
    assert "Proposed groupings" in page and "1 factor not in any group" in page
    assert name == bad                       # the name field holds the text, unparsed
    sent = json.loads(sent)
    assert sent[0] == ["/author/a1/clinical-groups/approve",
                       {"group_id": 4, "name": bad, "factors": [bad]}]
    assert sent[1] == "reload"


def test_only_an_open_proposal_can_be_approved_by_id(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Family history", [1])))
    [g] = cg.groups(cx, T)
    cg.dismiss(cx, T, g["id"])
    assert cg.approve(cx, T, factors, "Blood", ["anemia"], group_id=g["id"]) is None
    assert [x["status"] for x in cg.groups(cx, T)] == ["dismissed"]


def test_a_malformed_reply_keeps_the_open_proposals(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Blood", [1])))
    with pytest.raises(cg.ModelReplyError):
        cg.propose(cx, T, factors, lambda s, u: "Sorry, I cannot help.")
    assert [g["name"] for g in cg.groups(cx, T)] == ["Blood"]


def test_nothing_is_written_while_the_model_is_asked(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Blood", [1])))
    cx.commit()
    during = []

    def complete(system, user):
        during.append(cx.in_transaction)
        return json.dumps({"groups": [{"name": "Blood and circulation", "factors": [1]}]})
    cg.propose(cx, T, factors, complete)
    # The open proposal's factor is offered again, and replaced only after the answer.
    assert during == [False]
    assert [g["name"] for g in cg.groups(cx, T)] == ["Blood and circulation"]


def test_record_factors_are_hidden_when_the_address_is_shared_now(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T, [{"kind": "result", "text": "Ferritin 447 High"}])
    cg.approve(cx, T, factors, "Blood", ["anemia", "Ferritin 447 High"])
    assert cg.item_factors(cx, T) == {"blood": ["anemia", "Ferritin 447 High"]}
    assert cg.item_factors(cx, T, records_ok=False) == {"blood": ["anemia"]}


def test_a_long_finding_is_kept_whole():
    text = "Lesion seen on imaging " + "x" * 300 + " ruled out"
    import sqlite3 as _s
    c = _s.connect(":memory:")
    init_stress_tables(c)
    got = cg.factors_for(c, T, [{"kind": "diagnosis", "text": text}])
    assert got[0]["factor"].endswith("ruled out")


def test_the_page_and_box_hide_record_factors_on_a_shared_address(tmp_path):
    c, tid = _app(tmp_path)
    db = str(tmp_path / "chat.db")
    with sqlite3.connect(str(tmp_path / "e4l.db")) as e:
        e.execute("INSERT INTO e4l_clients VALUES(1,'Pat Testerson','pat.test@example.com')")
        e.execute("INSERT INTO e4l_clients VALUES(2,'Rex Testerson','pat.test@example.com')")
    with sqlite3.connect(db) as x:
        cg.ensure_schema(x)
        from dashboard.biofield_clinical_proposals import ensure_schema as _ps
        _ps(x)
        for status in ("approved", "proposed"):
            gid = x.execute("INSERT INTO biofield_clinical_groups(test_id,name,status) "
                            "VALUES(?,?,?)", (tid, "Lens " + status, status)).lastrowid
            x.execute("INSERT INTO biofield_clinical_group_factors VALUES(?,?,?,?)",
                      (gid, "ferritin 447", "Ferritin 447", "record"))
        x.execute("INSERT INTO biofield_clinical_proposals(test_id,item_key,label,status) "
                  "VALUES(?,?,?,?)", (tid, "lens approved", "Lens approved", "accepted"))
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert 'data-label="Lens approved"' in page and "Ferritin 447" not in page
    assert c.get(f"/author/{tid}/clinical-groups").get_json()["proposed"] == []


def test_a_display_name_reaches_the_combined_row(cx):
    from dashboard.biofield_clinical_checklist import set_display_label
    from dashboard.biofield_clinical_proposals import decide
    _stress(cx, "hot flashes")
    decide(cx, T, "Low progesterone", "accepted", "Manually added")
    set_display_label(cx, "Low progesterone", "Hormone balance")
    cg.approve(cx, T, cg.factors_for(cx, T), "Hormone balance", ["hot flashes"])
    assert accepted_labels(cx, T) == ["Low progesterone"]
    assert cg.item_factors(cx, T) == {"low progesterone": ["hot flashes"]}


def test_a_folded_name_reaches_the_survivor(cx):
    from dashboard.biofield_clinical_checklist import alias_condition
    _stress(cx, "night sweats")
    alias_condition(cx, "Low estrogen", "Adrenal Fatigue")
    cg.approve(cx, T, cg.factors_for(cx, T), "low estrogen", ["night sweats"])
    assert cg.item_factors(cx, T) == {"adrenal fatigue": ["night sweats"]}


def test_an_accepted_item_keeps_its_evidence(cx):
    from dashboard.biofield_clinical_proposals import decide, decisions
    _stress(cx, "anemia")
    decide(cx, T, "Blood", "accepted", "Manually added to clinical checklist")
    cg.approve(cx, T, cg.factors_for(cx, T), "Blood", ["anemia"])
    assert decisions(cx, T)["blood"]["evidence"] == "Manually added to clinical checklist"


def test_a_dismissed_factor_can_still_be_placed_by_hand(cx):
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Family history", [1])))
    [g] = cg.groups(cx, T)
    cg.dismiss(cx, T, g["id"])
    assert [f["factor"] for f in cg.ungrouped(cx, T, factors)] == ["anemia"]
    assert cg.approve(cx, T, factors, "Blood", ["anemia"]) is not None


def test_a_huge_group_id_is_refused_not_a_crash(tmp_path):
    c, tid = _app(tmp_path)
    r = c.post(f"/author/{tid}/clinical-groups/approve",
               json={"group_id": 10 ** 30, "name": "X", "factors": ["cataract"]})
    assert r.status_code == 400


def test_a_group_folded_later_shows_on_the_survivor_and_can_be_removed(cx):
    from dashboard.biofield_clinical_checklist import alias_condition
    _stress(cx, "poor night vision")
    factors = cg.factors_for(cx, T)
    cg.approve(cx, T, factors, "Night blindness", ["poor night vision"])
    alias_condition(cx, "Night blindness", "Dry AMD")
    assert cg.item_factors(cx, T) == {"dry amd": ["poor night vision"]}
    assert cg.remove_factor(cx, T, "Dry AMD", "poor night vision")
    assert [f["factor"] for f in cg.ungrouped(cx, T, factors)] == ["poor night vision"]


def test_a_long_row_name_is_matched_whole(cx):
    from dashboard.biofield_clinical_proposals import decide
    long = "Age-related nuclear cataract, bilateral, with posterior subcapsular change " \
           "noted at the last two examinations"
    assert len(long) > 80
    _stress(cx, "glare")
    decide(cx, T, long, "accepted", "From records")
    cg.approve(cx, T, cg.factors_for(cx, T), long, ["glare"])
    assert accepted_labels(cx, T) == [long]


def test_a_dismissal_survives_placing_and_removing_the_factor(cx):
    _stress(cx, "cardio failure")
    factors = cg.factors_for(cx, T)
    cg.propose(cx, T, factors, _reply(("Family history", [1])))
    [g] = cg.groups(cx, T)
    cg.dismiss(cx, T, g["id"])
    cg.approve(cx, T, factors, "Heart", ["cardio failure"])
    cg.remove_factor(cx, T, "Heart", "cardio failure")

    def boom(s, u):
        raise AssertionError("re-proposed a dismissed factor")
    assert cg.propose(cx, T, factors, boom)["proposed"] == 0


def test_record_wording_stays_out_of_the_decision_note(cx):
    from dashboard.biofield_clinical_proposals import decisions
    _stress(cx, "anemia")
    factors = cg.factors_for(cx, T, [{"kind": "result", "text": "Ferritin 447 High"}])
    cg.approve(cx, T, factors, "Blood", ["anemia", "Ferritin 447 High"])
    note = decisions(cx, T)["blood"]["evidence"]
    assert note == "Grouped: anemia, 1 record finding(s)" and "Ferritin" not in note


def test_a_hidden_rows_factors_are_offered_again(tmp_path):
    c, tid = _app(tmp_path)
    assert c.post(f"/author/{tid}/clinical-groups/approve",
                  json={"name": "Lens", "factors": ["cataract"]}).get_json()["ok"]
    assert c.post(f"/author/{tid}/clinical-items",
                  json={"action": "remove", "label": "Lens"}).get_json()["ok"]
    page = c.get(f"/author/{tid}").get_data(as_text=True)
    assert 'data-label="Lens"' not in page
    free = c.get(f"/author/{tid}/clinical-groups").get_json()["ungrouped"]
    assert {"factor": "cataract", "kind": "stress"} in free


def test_the_factor_row_sits_in_the_content_column():
    """Glen, 2026-10-16: items overlapped. With no column the factor row fell into the
    22px tick-box column and ran over the controls below it."""
    from dashboard.biofield_report_html import render_clinical_checklist
    html = render_clinical_checklist([{"label": "Blood", "factors": ["anemia"]}],
                                     free_factors=["a very long record finding " * 5])
    assert ".clinical-factors{grid-column:3;" in html
    assert ".clinical-factor-add{max-width:260px;" in html
