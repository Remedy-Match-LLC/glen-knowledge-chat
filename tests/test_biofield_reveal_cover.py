"""Import Reveal adds only the reveal layers no Clinical layer covers (Glen, 2026-10-18).

Covered: the same condition heading, the same remedy, or one of the scan's
recommended infoceuticals already ticked in a Clinical layer. The preview Glen
confirms is the set imported: the synthesis is a model call and is never re-run."""
import sqlite3

import pytest

from biofield_local_app import create_app
import dashboard.biofield_reveal_import as RI
from dashboard import biofield_reveal_cover as rc

JOSIE = ["Cataract and vision", "Liver and gallbladder", "Gut",
         "Connective tissue autoimmunity", "Vertigo", "Blood sugar", "Hypothyroid",
         "Infections", "Family history", "Blood and circulation"]


@pytest.mark.parametrize("title,expected", [
    # Glen's examples, 2026-10-18.
    ("Liver Detoxification and Support", ("Liver and gallbladder", "liver")),
    ("Digestive Health and Blood Sugar Regulation", ("Blood sugar", "blood sugar")),
    ("Gallbladder Function and Blood Regulation", ("Liver and gallbladder", "gallbladder")),
    ("Kidney Health and Detoxification", None),
    ("Heart and Circulatory Support", None),
])
def test_heading_rule_on_glens_examples(title, expected):
    assert rc.heading_match(title, JOSIE) == expected


def test_blood_alone_never_matches_blood_sugar_and_the_longer_part_wins():
    # "Blood sugar doesn't match Blood Regulation."
    assert rc.heading_match("Blood Regulation", ["Blood sugar"]) is None
    assert rc.heading_match("Blood Regulation", ["Blood and circulation"]) == (
        "Blood and circulation", "blood")
    # "'blood' alone doesn't functionally match Blood Sugar Regulation."
    assert rc.heading_match("Blood Sugar Regulation", ["Blood and circulation"],
                            vocabulary=["blood sugar"]) is None
    assert rc.heading_match("Blood Sugar Regulation",
                            ["Blood and circulation", "Blood sugar"]) == ("Blood sugar",
                                                                          "blood sugar")


def test_generic_words_never_match():
    assert rc.heading_match("Immune System Support", ["Nervous system support"]) is None
    for generic in ("Regulation", "Stress", "Support", "Health"):
        assert rc.heading_match("Visual Stress Relief and Blood Regulation",
                                [generic]) is None, generic


def _plan(layers, rows, recommended=frozenset(), ticked=()):
    return rc.plan(layers, rows, recommended, ticked)


def test_the_same_remedy_is_covered():
    out = _plan([{"n": 1, "title": "Kidney Health", "remedy_name": "Alkalize Powder"}],
                [{"head": "Gut", "remedy": "Alkalize Powder"}])
    assert out["add"] == [] and out["skip"][0]["rule"] == "same remedy"


def test_a_resolved_remedy_name_is_compared():
    out = rc.plan([{"n": 1, "title": "Kidney Health", "remedy_name": "alkalize"}],
                  [{"head": "Gut", "remedy": "Alkalize Powder"}], set(), [],
                  resolve=lambda n: "Alkalize Powder")
    assert out["skip"][0]["rule"] == "same remedy"


INFO = {"n": 1, "title": "Kidney Health", "remedy_name": "Kidney Kare",
        "codes": ["ED12", "MR3"]}


def test_a_recommended_infoceutical_ticked_in_a_clinical_layer_is_covered():
    out = _plan([INFO], [{"head": "Gut", "remedy": ""}], {"ED12"},
                ["ED12 Kidney Energetic Driver Infoceutical"])
    assert out["skip"][0]["rule"] == "recommended infoceutical already ticked"
    assert out["skip"][0]["why"] == "ED12"


def test_an_infoceutical_on_the_chain_counts_as_ticked():
    out = _plan([INFO], [{"head": "Gut",
                          "remedy": "ED12 Kidney Energetic Driver Infoceutical"}], {"ED12"})
    assert out["skip"][0]["rule"] == "recommended infoceutical already ticked"


@pytest.mark.parametrize("recommended,ticked", [
    ({"ED12"}, []),                                              # recommended, not ticked
    (set(), ["ED12 Kidney Energetic Driver Infoceutical"]),      # ticked, none recommended
    ({"ES1"}, ["ED12 Kidney Energetic Driver Infoceutical"]),    # ticked, another recommended
    (None, ["ED12 Kidney Energetic Driver Infoceutical"]),       # list unread
])
def test_the_infoceutical_rule_needs_both_halves(recommended, ticked):
    out = _plan([INFO], [{"head": "Gut", "remedy": ""}], recommended, ticked)
    assert [L["n"] for L in out["add"]] == [1]
    assert out["recommended_unread"] is (recommended is None)


# The route.

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import dashboard.biofield_e4l as _be
    import dashboard as _d
    monkeypatch.setattr(_be, "species_from_e4l", lambda e4l_db, email: "Human")
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    import dashboard.e4l_scan_marks as _m
    monkeypatch.setattr(_m, "infoceutical_marks", lambda cx, db, scan: {"recommended": set()})


_NONE = {"found": False}


def _reveal(*layers):
    return {"found": True, "scan_id": 9, "scan_date": "2026-10-18", "days_ago": 0,
            "fresh": True, "layers": [dict(L, most_affected="", summary="") for L in layers]}


def _client(tmp_path, monkeypatch, result):
    calls = []

    def synth(*a, **k):
        calls.append(1)
        return result
    monkeypatch.setattr(RI, "synthesize_reveal_layers", synth)
    db = str(tmp_path / "chat_log.db")
    client = create_app(db, scan_lookup=lambda e: _NONE).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "Jo", "email": "jo@x.com",
                                               "date": "2026-10-18"})
    return client, tid, db, calls


def _chain(db):
    cx = sqlite3.connect(db)
    rows = cx.execute("SELECT layer,head,remedy FROM biofield_auth_chain ORDER BY layer,id").fetchall()
    cx.close()
    return rows


def test_preview_then_import_adds_only_uncovered_layers_and_reports_the_skipped(
        tmp_path, monkeypatch):
    first = _reveal({"n": 1, "title": "Liver and gallbladder", "remedy_name": "Stone Solvent"})
    client, tid, db, calls = _client(tmp_path, monkeypatch, first)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: (
        calls.append(1), _reveal(
            {"n": 1, "title": "Liver Detoxification and Support", "remedy_name": "Liver Care"},
            {"n": 2, "title": "Kidney Health and Detoxification", "remedy_name": "Kidney Kare"},
            {"n": 3, "title": "Mineral need", "remedy_name": "Stone Solvent"}))[1])
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["needs_confirm"] and _chain(db) == [(1, "Liver and gallbladder", "Stone Solvent")]
    assert [L["title"] for L in j["preview"]["add"]] == ["Kidney Health and Detoxification"]
    assert [(L["n"], L["rule"]) for L in j["preview"]["skip"]] == [
        (1, "same heading"), (3, "same remedy")]
    runs = len(calls)
    j2 = client.post(f"/author/{tid}/e4l/import-reveal",
                     json={"force": True, "token": j["token"]}).get_json()
    assert len(calls) == runs, "the confirm step must not run the synthesis again"
    assert j2["ok"] and j2["imported"] == 1
    assert [s["rule"] for s in j2["skipped"]] == ["same heading", "same remedy"]
    # The one added layer follows the existing one, with no gap.
    assert _chain(db)[1] == (2, "Kidney Health and Detoxification", "Kidney Kare")


def test_add_anyway_imports_a_skipped_layer(tmp_path, monkeypatch):
    layer = _reveal({"n": 1, "title": "Gut", "remedy_name": ""})
    client, tid, db, _ = _client(tmp_path, monkeypatch, layer)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert [L["rule"] for L in j["preview"]["skip"]] == ["same heading"]
    j2 = client.post(f"/author/{tid}/e4l/import-reveal",
                     json={"force": True, "token": j["token"], "add_anyway": [1]}).get_json()
    assert j2["imported"] == 1 and j2["skipped"] == [] and len(_chain(db)) == 2


def test_force_without_the_preview_token_only_previews(tmp_path, monkeypatch):
    layer = _reveal({"n": 1, "title": "Gut", "remedy_name": ""})
    client, tid, db, _ = _client(tmp_path, monkeypatch, layer)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={"force": True}).get_json()
    assert j["needs_confirm"] and len(_chain(db)) == 1


def test_a_wrong_or_used_token_writes_nothing(tmp_path, monkeypatch):
    layer = _reveal({"n": 1, "title": "Kidney", "remedy_name": ""})
    client, tid, db, _ = _client(tmp_path, monkeypatch, layer)
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    bad = client.post(f"/author/{tid}/e4l/import-reveal",
                      json={"force": True, "token": "forged"}).get_json()
    assert bad["ok"] is False and "expired" in bad["reason"] and len(_chain(db)) == 1
    client.post(f"/author/{tid}/e4l/import-reveal",
                json={"force": True, "token": j["token"], "add_anyway": [1]})
    again = client.post(f"/author/{tid}/e4l/import-reveal",
                        json={"force": True, "token": j["token"]}).get_json()
    assert again["ok"] is False and len(_chain(db)) == 2


def test_an_expired_preview_is_refused(tmp_path):
    from datetime import datetime, timedelta, timezone
    from dashboard import biofield_reveal_preview as rp
    cx = sqlite3.connect(str(tmp_path / "x.db"))
    token = rp.save(cx, "a1", {"layers": []}, {"add": [], "skip": [], "recommended_unread": False})
    assert rp.load(cx, "a1", token)
    later = datetime.now(timezone.utc) + rp.TTL + timedelta(seconds=1)
    assert rp.load(cx, "a1", token, now=later) is None


def test_a_chain_changed_since_the_preview_is_refused(tmp_path, monkeypatch):
    layer = _reveal({"n": 1, "title": "Kidney Health", "remedy_name": "Kidney Kare"})
    client, tid, db, _ = _client(tmp_path, monkeypatch,
                                 _reveal({"n": 1, "title": "Gut", "remedy_name": ""}))
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: layer)
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert [L["title"] for L in j["preview"]["add"]] == ["Kidney Health"]
    cx = sqlite3.connect(db)
    cx.execute("UPDATE biofield_auth_chain SET remedy='Kidney Kare'")
    cx.commit()
    cx.close()
    j2 = client.post(f"/author/{tid}/e4l/import-reveal",
                     json={"force": True, "token": j["token"]}).get_json()
    assert j2["ok"] is False and "changed" in j2["reason"] and len(_chain(db)) == 1


def test_a_first_import_on_an_empty_chain_adds_every_layer(tmp_path, monkeypatch):
    client, tid, db, _ = _client(tmp_path, monkeypatch, _reveal(
        {"n": 1, "title": "Gut", "remedy_name": ""},
        {"n": 2, "title": "Gut", "remedy_name": ""}))
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j["ok"] and j["imported"] == 2 and j["skipped"] == []


def _run_preview(preview, clicks):
    import json as _json
    import shutil
    import subprocess
    import inspect
    from dashboard import biofield_report_html as rh
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = inspect.getsource(rh)
    start = src.index("function impEl(tag,text,cls)")
    script = src[start:src.index("async function importReveal0(){", start)]
    script = script.replace("\\\\'", "\\'").replace("\\\\u00b7", "\\u00b7")
    js = r"""
function node(tag){var n={tag:tag,children:[],className:'',textContent:'',value:'',
 checked:false,appendChild:function(c){this.children.push(c);return c},remove:function(){}};
 return n}
var made=[];var stat={textContent:''};var anchor={parentNode:{insertBefore:function(b){made.push(b)}},
 nextSibling:null};
var document={createElement:function(t){return node(t)},createTextNode:function(t){return {tag:'#text',textContent:t}},
 getElementById:function(id){return id==='impStat'?anchor:null}};
function impStat(t){stat.textContent=t}
SCRIPT
function walk(n,out){out.push(n);(n.children||[]).forEach(function(c){walk(c,out)});return out}
impPreview({existing:2,preview:PREVIEW}).then(function(r){
 var all=walk(made[0],[]);console.log(JSON.stringify({r:r,
  text:all.map(function(n){return n.textContent}).filter(Boolean)}))});
var all=walk(made[0],[]);var boxes=all.filter(function(n){return n.tag==='input'});
CLICKS
var btn=all.filter(function(n){return n.tag==='button'});
(BUTTON==='go'?btn[0]:btn[1]).onclick();
"""
    js = (js.replace("SCRIPT", script).replace("PREVIEW", _json.dumps(preview))
          .replace("CLICKS", ";".join(f"boxes[{i}].checked=true" for i in clicks["boxes"]))
          .replace("BUTTON", _json.dumps(clicks["button"])))
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return _json.loads(out.stdout)


PREVIEW = {"add": [{"n": 2, "title": "Kidney", "remedy": "Kidney Kare"}],
           "skip": [{"n": 1, "title": "<b>Liver</b>", "remedy": "", "rule": "same heading",
                     "why": "Liver and gallbladder (shares \"liver\")"},
                    {"n": 3, "title": "Mineral", "remedy": "Stone Solvent",
                     "rule": "same remedy", "why": "Stone Solvent"}],
           "recommended_unread": True}


def test_the_preview_lists_both_groups_and_returns_the_layers_ticked_to_add_anyway():
    got = _run_preview(PREVIEW, {"boxes": [1], "button": "go"})
    assert got["r"] == {"anyway": ["3"]}
    text = " | ".join(got["text"])
    assert "1 reveal layer(s) will be added, 2 skipped as covered" in text
    assert "same heading: Liver and gallbladder (shares \"liver\")" in text
    assert "could not be read" in text
    assert "<b>Liver</b>" in text   # a title stays text


def test_cancel_returns_nothing():
    assert _run_preview(PREVIEW, {"boxes": [0], "button": "no"})["r"] is None
