"""Live session part 2b on the server: POST /author/<tid>/handsfree-segment, and the
handsfree flag on the row routes. Ruling 7 (Glen, 14 November 2026): every row from the
hands-free recording goes in unconfirmed, highlighted for review, even when matched.

The last test plays a whole spoken layer through the routes in the order the page
sends them (tests/test_layer_handsfree_js.py checks the page sends that order)."""
import json
import sqlite3

from biofield_local_app import create_app
from dashboard.biofield_authoring import add_chain_row, authored_report
from tests.test_biofield_local_app import _seed, _species_known, _no_console_gate  # noqa: F401


def _db(tmp_path):
    db = str(tmp_path / "chat_log.db")
    _seed(db)
    with sqlite3.connect(db) as cx:
        cx.execute("CREATE TABLE fmp_snap_products(id_pk TEXT,product_name TEXT,dosage TEXT,"
                   "dosage_freq TEXT,dosage_timing TEXT)")
        cx.execute("INSERT INTO fmp_snap_products VALUES('1','Sulfur Syntropy','1 capsule',"
                   "'twice a day','with food, or as guided.')")
        cx.commit()
    return db


def _app(db):
    client = create_app(db, interpret_complete=lambda s, u: json.dumps({"layers": []})).test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    return client, tid


def _rows(db, tid):
    with sqlite3.connect(db) as cx:
        return authored_report(cx, tid)["layers"]


def _marks(db, rid):
    with sqlite3.connect(db) as cx:
        c, u = cx.execute("SELECT confirmed, unmatched_fields FROM biofield_auth_chain "
                          "WHERE id=?", (rid,)).fetchone()
    return c, json.loads(u or "[]")


def test_segment_route_returns_matched_events_and_writes_nothing(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    j = client.post(f"/author/{tid}/handsfree-segment", json={
        "text": "Remedy sulfur centropy. Two drops daily. Balanced. Next layer. Head."}).get_json()
    assert j["ok"] is True
    rem, bal, nxt = j["events"]
    assert rem["field"] == "remedy" and rem["match"]["value"] == "Sulfur Syntropy"
    assert rem["match"]["dosing"]["dosage"] == "2 drops"
    assert bal == {"balanced": True} and nxt == {"next_layer": True}
    assert j["pending"] == "head" and j["after_remedy"] is False
    assert _rows(db, tid) == []
    # The open field comes back with the next utterance.
    j = client.post(f"/author/{tid}/handsfree-segment",
                    json={"text": "zzqx florp", "pending": "head"}).get_json()
    assert j["events"][0]["field"] == "head"
    assert j["events"][0]["match"]["matched"] is False


def test_segment_route_sits_behind_the_console_gate(tmp_path, monkeypatch):
    db = _db(tmp_path)
    import dashboard as _d
    monkeypatch.setenv("CONSOLE_SECRET", "s3cret-test")
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "s3cret-test", raising=False)
    client = create_app(db).test_client()
    assert client.post("/author/a1/handsfree-segment", json={"text": "head liver"}).status_code == 401


def test_a_matched_handsfree_row_goes_in_unconfirmed(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    rid = client.post(f"/author/{tid}/row", json={
        "layer": 1, "head": "Liver", "remedy": "Sulfur Syntropy",
        "voice": {"remedy": True}, "handsfree": True}).get_json()["rid"]
    typed = client.post(f"/author/{tid}/row", json={"layer": 2, "head": "Lung",
                                                     "remedy": "TMG"}).get_json()["rid"]
    assert _marks(db, rid) == (0, ["handsfree"])
    assert _marks(db, typed) == (1, [])


def test_a_handsfree_head_on_a_confirmed_card_unconfirms_it(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    rid = client.post(f"/author/{tid}/row", json={"layer": 1, "head": "Liver",
                                                   "remedy": "TMG"}).get_json()["rid"]
    assert _marks(db, rid)[0] == 1
    client.post(f"/author/{tid}/row/{rid}", json={"head": "Kidney", "most_affected": "",
                                                  "voice": {"head": True}, "handsfree": True})
    assert _marks(db, rid) == (0, ["handsfree"])
    assert _rows(db, tid)[0]["head"] == "Kidney"


def test_a_later_matched_short_recording_still_leaves_it_for_review(tmp_path):
    """Only Confirm clears the hands-free mark."""
    db = _db(tmp_path)
    client, tid = _app(db)
    rid = client.post(f"/author/{tid}/row", json={
        "layer": 1, "head": "Zzqx", "remedy": "TMG", "voice": {"head": False},
        "handsfree": True}).get_json()["rid"]
    assert _marks(db, rid) == (0, ["handsfree", "head"])
    client.post(f"/author/{tid}/row/{rid}", json={"head": "Liver", "most_affected": "",
                                                  "voice": {"head": True}})
    assert _marks(db, rid) == (0, ["handsfree"])
    client.post(f"/author/{tid}/row/{rid}/confirm", json={})
    assert _marks(db, rid) == (1, [])


def test_a_handsfree_remedy_filling_an_anchor_goes_in_unconfirmed(tmp_path):
    """A new layer's Head starts a row with no remedy; the remedy then fills it."""
    db = _db(tmp_path)
    client, tid = _app(db)
    head = client.post(f"/author/{tid}/row", json={
        "layer": 1, "head": "Liver", "most_affected": "", "remedy": "",
        "voice": {"head": True}, "handsfree": True}).get_json()["rid"]
    with sqlite3.connect(db) as cx:      # a confirmed state the fill must not keep
        cx.execute("UPDATE biofield_auth_chain SET confirmed=1, unmatched_fields=NULL")
        cx.commit()
    j = client.post(f"/author/{tid}/row", json={
        "layer": 1, "anchor_rid": head, "head": "Liver", "most_affected": "",
        "remedy": "Sulfur Syntropy", "voice": {"remedy": True}, "handsfree": True}).get_json()
    assert j["rid"] == head and j["filled_anchor"] is True
    assert _marks(db, head) == (0, ["handsfree"])


def test_a_handsfree_dose_updates_the_last_remedy(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    with sqlite3.connect(db) as cx:
        rid = add_chain_row(cx, tid, 1, "Liver", "", "Sulfur Syntropy", "1 capsule", "daily")
    client.post(f"/author/{tid}/row/{rid}", json={"dosage": "2 drops",
                                                  "frequency": "twice a day", "handsfree": True})
    r = _rows(db, tid)[0]
    assert (r["dosage"], r["frequency"]) == ("2 drops", "twice a day")
    assert _marks(db, rid) == (0, ["handsfree"])


def test_a_spoken_session_through_the_routes(tmp_path):
    """Head, tail, remedy, balanced on layer 1; then next layer and a head: a new layer.
    The calls are the ones the page makes, in its order."""
    db = _db(tmp_path)
    client, tid = _app(db)
    seg = client.post(f"/author/{tid}/handsfree-segment", json={
        "text": "Head liver. Tail gut. Remedy sulfur syntropy. Balanced. Next layer. "
                "Head kidney."}).get_json()
    kinds = [e.get("field") or next(iter(e)) for e in seg["events"]]
    assert kinds == ["head", "tail", "remedy", "balanced", "next_layer", "head"]
    rid = client.post(f"/author/{tid}/row", json={
        "layer": "1", "head": "Liver", "most_affected": "", "remedy": "",
        "voice": {"head": False}, "handsfree": True}).get_json()["rid"]
    client.post(f"/author/{tid}/row/{rid}", json={"head": "Liver", "most_affected": "Gut",
                                                  "voice": {"most_affected": False},
                                                  "handsfree": True})
    client.post(f"/author/{tid}/row", json={
        "layer": "1", "anchor_rid": rid, "head": "Liver", "most_affected": "Gut",
        "remedy": "Sulfur Syntropy", "dosage": "1 capsule", "frequency": "twice a day",
        "timing": "", "voice": {"remedy": True}, "handsfree": True})
    assert client.post(f"/author/{tid}/layer-balanced",
                       json={"rids": [rid], "balanced": True}).get_json()["updated"] == 1
    rid2 = client.post(f"/author/{tid}/row", json={
        "layer": "2", "head": "Kidney", "most_affected": "", "remedy": "",
        "voice": {"head": False}, "handsfree": True}).get_json()["rid"]
    rows = {r["rid"]: r for r in _rows(db, tid)}
    assert set(rows) == {rid, rid2}
    one, two = rows[rid], rows[rid2]
    assert (one["layer"], one["head"], one["most_affected"], one["remedy"]) == (
        1, "Liver", "Gut", "Sulfur Syntropy")
    assert one["layer_balanced"] == 1 and one["confirmed"] == 0
    assert (two["layer"], two["head"], two["layer_balanced"], two["confirmed"]) == (
        2, "Kidney", 0, 0)


def test_page_has_the_handsfree_toggle_and_status_line(tmp_path):
    db = _db(tmp_path)
    client, tid = _app(db)
    h = client.get(f"/author/{tid}").get_data(as_text=True)
    assert "<button id=hfBtn type=button class='btn ghost'" in h
    assert "onclick=handsFree()>Hands-free</button>" in h
    assert "<div id=hfStat class=food></div>" in h
    # The whole-session Record and the transcript are still there.
    assert "onclick=recStart()" in h and "id=sessText" in h
    i = h.index("id=hfBtn")
    visible = h[i:h.index("</button>", i)]
    assert "—" not in visible and "&mdash;" not in visible
