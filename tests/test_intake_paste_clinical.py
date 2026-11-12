# tests/test_intake_paste_clinical.py
"""Pasted notes also offer conditions and medicines (Glen, 2026-11-04).

Each is stored as a pending finding beside those read from records, marked as from
pasted notes. Nothing joins the Clinical summary until Glen accepts it. Items the intake
already has are skipped. The notes themselves are never stored. No model is called:
the completion is a stub in every test."""
import json
import shutil
import sqlite3
import subprocess

import pytest

from biofield_local_app import create_app
from dashboard import biofield_records as br

MARKER = "ZQX-PASTE-CLIN-4417"
EMAIL = "j@x.com"
_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _e4l(db, people):
    path = db + ".e4l.db"
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS e4l_clients (name TEXT, email TEXT)")
        cx.executemany("INSERT INTO e4l_clients VALUES (?,?)", list(people))
    return path


def _f(kind, text, page=1):
    return {"kind": kind, "text": text, "page": page, "date": ""}


def _app(db, findings=(), stresses=(), people=(), profile=None, calls=None, raises=None):
    def complete(system, user):
        if system == br.SYSTEM:
            if calls is not None:
                calls.append(user)
            if raises:
                raise raises
            return json.dumps({"findings": list(findings)})
        return json.dumps({"stresses": list(stresses)})
    return create_app(db, scan_lookup=lambda e, client_id=None: _NONE,
                      fetch_profile=lambda e: dict(profile or {}),
                      fetch_recent_comms=lambda e: {}, interpret_complete=complete,
                      e4l_db=_e4l(db, people))


def _new(client, email=EMAIL):
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "J", "email": email,
                                                "date": "2026-06-25"})
    return tid


def _paste(client, tid, text="notes"):
    return client.post(f"/author/{tid}/mine-paste", json={"text": text}).get_json()


def _rows(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT kind, text, status, filename FROM client_record_findings "
                          "ORDER BY id").fetchall()


def _accept(client, tid, label, status="accepted"):
    assert client.post(f"/author/{tid}/clinical-proposals",
                       json={"label": label, "status": status}).get_json()["ok"]


def test_conditions_and_medicines_wait_for_review(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Hypothyroidism"),
                       _f("medication", "Levothyroxine 50 mcg daily")]).test_client()
    tid = _new(client)
    j = _paste(client, tid)
    assert j["offered"] == 2 and j["already"] == 0
    assert _rows(db) == [("diagnosis", "Hypothyroidism", "pending", "Pasted notes"),
                         ("medication", "Levothyroxine 50 mcg daily", "pending",
                          "Pasted notes")]
    rec = client.get(f"/author/{tid}/records").get_json()
    assert [f["pasted"] for f in rec["pending"]] == [True, True]
    assert all(f["added"] for f in rec["pending"]) and rec["approved"] == []
    # Nothing joins the summary until Glen accepts it.
    row = 'data-label="Hypothyroidism"'
    assert row not in client.get(f"/author/{tid}").get_data(as_text=True)
    fid = rec["pending"][0]["id"]
    assert client.post(f"/author/{tid}/records/{fid}", json={"decision": "approve"}
                       ).get_json()["ok"]
    assert row in client.get(f"/author/{tid}").get_data(as_text=True)


def test_a_hand_added_item_blocks_its_base_name(tmp_path):
    """Marvin's "Glaucoma (3 eye drops)" blocks a pasted "Glaucoma", and the medicine
    named inside an item is known too."""
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Glaucoma"), _f("diagnosis", "glaucoma."),
                       _f("medication", "Latanoprost 0.005% one drop nightly"),
                       _f("diagnosis", "Cataract")]).test_client()
    tid = _new(client)
    _accept(client, tid, "Glaucoma (3 eye drops)")
    _accept(client, tid, "Dry eye (latanoprost irritation)")
    j = _paste(client, tid)
    assert (j["offered"], j["already"]) == (1, 3)
    assert [r[1] for r in _rows(db)] == ["Cataract"]


def test_a_dismissed_item_is_not_offered_again(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Insomnia")]).test_client()
    tid = _new(client)
    _accept(client, tid, "Insomnia", status="dismissed")
    assert _paste(client, tid)["offered"] == 0 and _rows(db) == []


def test_a_profile_condition_is_not_offered_again(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Macular degeneration")],
                  profile={"conditions": ["Macular Degeneration"]}).test_client()
    tid = _new(client)
    assert _paste(client, tid)["already"] == 1 and _rows(db) == []


def test_a_finding_rejected_once_is_not_offered_on_the_next_paste(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Migraine")]).test_client()
    tid = _new(client)
    assert _paste(client, tid, "first notes")["offered"] == 1
    fid = client.get(f"/author/{tid}/records").get_json()["pending"][0]["id"]
    assert client.post(f"/author/{tid}/records/{fid}", json={"decision": "reject"}
                       ).get_json()["ok"]
    j = _paste(client, tid, "other notes")
    assert (j["offered"], j["already"]) == (0, 1) and len(_rows(db)) == 1


def test_only_conditions_and_medicines_are_kept(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("result", "TSH 6.2 mIU/L high"), _f("procedure", "MRI brain"),
                       _f("diagnosis", "x" * 161), _f("diagnosis", "y" * 160)]).test_client()
    tid = _new(client)
    assert _paste(client, tid)["offered"] == 1
    assert [r[1] for r in _rows(db)] == ["y" * 160]


@pytest.mark.parametrize("people", [[("J Smith", EMAIL), ("K Smith", EMAIL)]])
def test_a_shared_address_is_offered_nothing(tmp_path, people):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Asthma")], stresses=["Poor sleep"],
                  people=people).test_client()
    tid = _new(client)
    j = _paste(client, tid)
    assert j["added"] == 1 and j["offered"] == 0 and "shared" in j["clinical_note"]
    with sqlite3.connect(db) as cx:
        br.ensure_tables(cx)
    assert _rows(db) == []


def test_an_unreadable_e4l_is_offered_nothing(tmp_path):
    db = str(tmp_path / "p.db")
    client = create_app(db, scan_lookup=lambda e, client_id=None: _NONE, fetch_profile=lambda e: {},
                        fetch_recent_comms=lambda e: {},
                        interpret_complete=lambda s, u: json.dumps(
                            {"findings": [_f("diagnosis", "Asthma")], "stresses": []}),
                        e4l_db=str(tmp_path / "missing" / "e4l.db")).test_client()
    tid = _new(client)
    j = _paste(client, tid)
    assert j["offered"] == 0 and "could not be checked" in j["clinical_note"]


def test_the_notes_are_never_stored_or_echoed(tmp_path):
    db = str(tmp_path / "p.db")
    calls = []
    client = _app(db, [_f("diagnosis", "Asthma")], calls=calls).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": f"{MARKER} wheezes"})
    assert r.get_json()["offered"] == 1 and calls and MARKER in calls[0]
    assert MARKER not in r.get_data(as_text=True)
    with sqlite3.connect(db) as cx:
        for (t,) in cx.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for c in [c[1] for c in cx.execute(f'PRAGMA table_info("{t}")')]:
                assert not cx.execute(f'SELECT 1 FROM "{t}" WHERE CAST("{c}" AS TEXT) '
                                      'LIKE ?', (f"%{MARKER}%",)).fetchone(), (t, c)


def test_a_model_failure_keeps_the_stresses_and_echoes_nothing(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, stresses=["Poor sleep"],
                  raises=RuntimeError(f"bad reply near {MARKER}")).test_client()
    tid = _new(client)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": f"{MARKER} notes"})
    j = r.get_json()
    assert j["added"] == 1 and "error" not in j and j["offered"] == 0
    assert j["clinical_note"] == "Conditions and medicines could not be read from the notes."
    assert MARKER not in r.get_data(as_text=True)


def test_long_notes_are_read_to_the_end(tmp_path):
    """ask() cuts a page at ASK_MAX, so the paste is split first and nothing is lost."""
    db = str(tmp_path / "p.db")
    calls = []
    client = _app(db, [_f("diagnosis", "Asthma")], calls=calls).test_client()
    tid = _new(client)
    text = ("line of notes\n" * 2600) + "END-" + MARKER
    assert len(text) > br.ASK_MAX
    _paste(client, tid, text)
    assert len(calls) == 2 and MARKER in calls[-1]


def test_paste_pages_keep_every_character():
    text = ("a" * 100 + "\n") * 700 + "b" * (br.ASK_MAX + 5)
    pages = br._paste_pages(text)
    assert "".join(pages) == text and all(len(p) <= br.ASK_MAX for p in pages)


def test_the_panel_marks_pasted_findings():
    from dashboard.biofield_report_html import _RECORDS_HTML
    if not shutil.which("node"):
        pytest.skip("needs node")
    js = _RECORDS_HTML.split("<script>")[1].split("</script>")[0]
    row = {"id": 1, "kind": "diagnosis", "kind_label": "Diagnosis", "text": "Asthma",
           "date": "", "filename": "Pasted notes", "page": None, "email_date": "",
           "subject": "", "pasted": True, "added": "2026-11-04"}
    reply = {"ok": True, "held": False, "unread": 0, "reader": False, "read_count": 0,
             "failed": [], "notes": [], "pending": [row], "approved": []}
    harness = ("var box={innerHTML:'',addEventListener:function(){}},"
               "pbox={innerHTML:'',addEventListener:function(){}};"
               "global.document={getElementById:function(id){"
               "return id==='recordProposals'?pbox:box}};"
               "global.location={pathname:'/author/a1'};"
               "global.fetch=async function(){return {json:async function(){return "
               + json.dumps(reply) + "}}};" + js +
               ";setTimeout(function(){process.stdout.write(pbox.innerHTML)},50);")
    html = subprocess.run(["node", "-e", harness], capture_output=True, text=True,
                          timeout=20).stdout
    assert "Proposed from pasted notes" in html
    assert "From pasted notes &middot; added 2026-11-04" in html and "email" not in html


def _run_paste_js(reply):
    """Runs the page's own minePaste in node against a server reply."""
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a7", "client": {"name": "J", "email": EMAIL}, "date": "",
           "layers": [], "schedule": []}
    h = render_author_html(rep, [], "")
    start = h.index("function pstat")
    body = h[start:h.index("\nasync function loadClinicalProposals", start)]
    harness = ("var stat={textContent:''},box={value:'notes'},refreshed=0;"
               "global.document={getElementById:function(id){return id==='pastestat'?stat:box}};"
               "global.window={refreshRecords:function(){refreshed++}};"
               "global.loadStress=function(){};"
               "global.post=async function(){return " + json.dumps(reply) + "};"
               + body + ";minePaste(null).then(function(){"
               "process.stdout.write(JSON.stringify({s:stat.textContent,r:refreshed,b:box.value}))});")
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_paste_status_names_what_was_offered():
    if not shutil.which("node"):
        pytest.skip("needs node")
    got = _run_paste_js({"added": 1, "skipped": 0, "offered": 2, "already": 1})
    assert got["r"] == 1
    assert got["s"] == ("Added 1 stress(es) from the pasted notes. Offered 2 condition(s) "
                        "or medicine(s) for review under the Clinical Summary. "
                        "Already listed: 1.")
    got = _run_paste_js({"added": 0, "skipped": 0, "offered": 0,
                         "clinical_note": "Conditions and medicines need the client's "
                                          "email in the header."})
    assert got["r"] == 0 and got["s"].endswith("need the client's email in the header.")


# --- Review round 1 fixes ----------------------------------------------------------

def test_a_condition_does_not_hide_a_medicine_of_the_same_words(tmp_path):
    """M1: "Vitamin D deficiency" is a condition, not a medicine. A pasted
    "Vitamin D3 2000 IU" is still offered, and B12 keeps its number."""
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("medication", "Vitamin D3 2000 IU"),
                       _f("medication", "Vitamin B12 1000 mcg"),
                       _f("medication", "Iron 25 mg daily")]).test_client()
    tid = _new(client)
    _accept(client, tid, "Vitamin D deficiency")
    _accept(client, tid, "Vitamin B deficiency")
    _accept(client, tid, "Iron overload")
    j = _paste(client, tid)
    assert (j["offered"], j["already"]) == (3, 0)
    assert br._medicine_name("Vitamin B12 1000 mcg") == "vitamin b12"
    assert br._medicine_name("Latanoprost 0.005% one drop") == "latanoprost"


def test_a_medicine_already_offered_is_not_offered_at_a_new_dose(tmp_path):
    db = str(tmp_path / "p.db")
    found = [[_f("medication", "Vitamin D3 1000 IU")], [_f("medication", "Vitamin D3 2000 IU")]]

    def complete(system, user):
        if system == br.SYSTEM:
            return json.dumps({"findings": found.pop(0)})
        return json.dumps({"stresses": []})
    client = create_app(db, scan_lookup=lambda e, client_id=None: _NONE,
                        fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                        interpret_complete=complete, e4l_db=_e4l(db, [])).test_client()
    tid = _new(client)
    assert _paste(client, tid, "first")["offered"] == 1
    j = _paste(client, tid, "second")
    assert (j["offered"], j["already"]) == (0, 1)


def test_pasted_suggestions_are_not_group_factors_until_added(tmp_path):
    """M2: a pending pasted finding stays out of the clinical groups. A pending
    finding read from a record still reaches them, as before."""
    from dashboard.biofield_clinical_groups import factors_for
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Asthma")]).test_client()
    tid = _new(client)
    assert _paste(client, tid)["offered"] == 1
    with sqlite3.connect(db) as cx:
        cx.execute("INSERT INTO client_record_findings(client_email, message_id, part, "
                   "filename, page, kind, text, finding_date, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (EMAIL, "gmail-1", 0, "labs.pdf", 1,
                                                 "diagnosis", "Hypothyroidism", "", "x"))
        pending = br.findings(cx, EMAIL)["pending"]
        assert {f["text"] for f in pending} == {"Asthma", "Hypothyroidism"}
        got = [f["factor"] for f in factors_for(cx, tid, pending)]
    assert got == ["Hypothyroidism"]


def test_one_failed_part_keeps_the_rest_of_a_long_paste(tmp_path):
    """L: the first part of a long paste fails, the second is still read."""
    db = str(tmp_path / "p.db")
    seen = []

    def complete(system, user):
        if system == br.SYSTEM:
            seen.append(user)
            if len(seen) == 1:
                raise RuntimeError("model down")
            return json.dumps({"findings": [_f("diagnosis", "Asthma")]})
        return json.dumps({"stresses": []})
    client = create_app(db, scan_lookup=lambda e, client_id=None: _NONE,
                        fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                        interpret_complete=complete, e4l_db=_e4l(db, [])).test_client()
    tid = _new(client)
    text = ("line of notes\n" * 2600) + "END"
    j = _paste(client, tid, text)
    assert j["offered"] == 1 and "1 of 2 parts" in j["clinical_note"]


def test_each_paste_gets_its_own_random_id(tmp_path):
    """L: the id holds no trace of the notes, and the same notes pasted twice differ."""
    import hashlib
    db = str(tmp_path / "p.db")
    found = [[_f("diagnosis", "Asthma")], [_f("diagnosis", "Eczema")]]

    def complete(system, user):
        if system == br.SYSTEM:
            return json.dumps({"findings": found.pop(0)})
        return json.dumps({"stresses": []})
    client = create_app(db, scan_lookup=lambda e, client_id=None: _NONE,
                        fetch_profile=lambda e: {}, fetch_recent_comms=lambda e: {},
                        interpret_complete=complete, e4l_db=_e4l(db, [])).test_client()
    tid = _new(client)
    _paste(client, tid, "same notes")
    _paste(client, tid, "same notes")
    digest = hashlib.sha256(b"same notes").hexdigest()[:16]
    with sqlite3.connect(db) as cx:
        ids = [r[0] for r in cx.execute("SELECT message_id FROM client_record_findings")]
    assert len(set(ids)) == 2 and not any(digest in i for i in ids)


def test_the_added_day_is_local(monkeypatch):
    """L: 04:30 UTC on the 12th is the evening of the 11th in Hilo."""
    import time
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    time.tzset()
    try:
        assert br._local_day("2026-11-12T04:30:00+00:00") == "2026-11-11"
        assert br._local_day(None) == ""
    finally:
        monkeypatch.undo()
        time.tzset()


# --- Review round 2 fixes ----------------------------------------------------------

def test_a_condition_base_name_does_not_hide_a_medicine(tmp_path):
    """"Insulin (resistance)" is known as insulin for a condition, but the medicine
    Insulin is still offered. A hand-added medicine still blocks the same medicine."""
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("medication", "Insulin"), _f("diagnosis", "Insulin"),
                       _f("medication", "Latanoprost")]).test_client()
    tid = _new(client)
    _accept(client, tid, "Insulin (resistance)")
    _accept(client, tid, "Latanoprost")
    j = _paste(client, tid)
    assert (j["offered"], j["already"]) == (1, 2)
    assert [(r[0], r[1]) for r in _rows(db)] == [("medication", "Insulin")]


def test_a_failed_profile_check_is_said(tmp_path, monkeypatch):
    db = str(tmp_path / "p.db")
    monkeypatch.delenv("CI", raising=False)

    def broken(e):
        raise RuntimeError("profile down")
    client = create_app(db, scan_lookup=lambda e, client_id=None: _NONE,
                        fetch_profile=broken, fetch_recent_comms=lambda e: {},
                        interpret_complete=lambda s, u: json.dumps(
                            {"findings": [_f("diagnosis", "Asthma")], "stresses": []}),
                        e4l_db=_e4l(db, [])).test_client()
    tid = _new(client)
    j = _paste(client, tid)
    assert j["offered"] == 1 and "profile could not be checked" in j["clinical_note"]


def test_failed_reads_keep_the_notes_in_the_box(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, stresses=["Poor sleep"], raises=RuntimeError("down")).test_client()
    tid = _new(client)
    assert _paste(client, tid)["keep_notes"] is True
    if not shutil.which("node"):
        pytest.skip("needs node")
    kept = _run_paste_js({"added": 1, "skipped": 0, "offered": 0, "already": 0,
                          "clinical_note": "x", "keep_notes": True})
    cleared = _run_paste_js({"added": 1, "skipped": 0, "offered": 1, "already": 0})
    assert kept["b"] == "notes" and cleared["b"] == ""


# --- Review round 3 fixes ----------------------------------------------------------

def test_a_dose_cut_keeps_different_medicines_apart(tmp_path):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("medication", "Omega 3 1000 mg"), _f("medication", "Omega 6"),
                       _f("medication", "Thyroid (Armour) 60 mg")],
                  profile={"conditions": ["Thyroid"]}).test_client()
    tid = _new(client)
    j = _paste(client, tid)
    assert (j["offered"], j["already"]) == (3, 0)
    assert br._medicine_name("Vitamin D3 2000 IU") == "vitamin d3"
    assert br._medicine_name("Latanoprost 0.005% one drop") == "latanoprost"
    assert br._medicine_name("Vitamin B 6 50 mg") == "vitamin b 6"


def test_a_failure_before_reading_keeps_the_stresses(tmp_path, monkeypatch):
    db = str(tmp_path / "p.db")
    client = _app(db, [_f("diagnosis", "Asthma")], stresses=["Poor sleep"]).test_client()
    tid = _new(client)
    import dashboard.biofield_clinical_proposals as cp

    def boom(*a, **k):
        raise RuntimeError("decisions down")
    monkeypatch.setattr(cp, "decisions", boom)
    r = client.post(f"/author/{tid}/mine-paste", json={"text": "notes"})
    j = r.get_json()
    assert r.status_code == 200 and j["added"] == 1 and j["keep_notes"] is True
