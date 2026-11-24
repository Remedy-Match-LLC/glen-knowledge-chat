"""FileMaker client and Biofield test notes on the console client page (Glen 2026-11-23, "console only")."""
import csv
import sqlite3

import pytest

import app as app_module
from dashboard import fmp_notes, rbac


def _csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return str(path)


@pytest.fixture
def export(tmp_path):
    clients = _csv(tmp_path / "clients.csv", [
        {"id_pk": "6057  ", "notes": "Prefers pickup.\nCall after 3.", "diagnose1": "SECRET-DX",
         "dob": "1950-01-01", "email": "a@b.com"},
        {"id_pk": "23.0", "notes": "   ", "diagnose1": "", "dob": "", "email": ""},
    ])
    tests = _csv(tmp_path / "tests.csv", [
        {"id_pk": "900", "id_fk_client": "6057", "date_test": "11/20/2026", "notes": "Retest liver."},
        {"id_pk": "901", "id_fk_client": "6057", "date_test": "01/05/2026", "notes": "First test."},
        {"id_pk": "902", "id_fk_client": "6057", "date_test": "02/05/2026", "notes": ""},
    ])
    return clients, tests


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fmp_clients (id_pk TEXT, email TEXT)")
    c.execute("INSERT INTO fmp_clients VALUES ('6057', ' A@B.com ')")
    c.execute("INSERT INTO fmp_clients VALUES ('77', '')")
    return c


def test_the_payload_carries_only_ids_a_date_and_the_note(export):
    p = fmp_notes.build_payload(*export)
    assert {tuple(sorted(n)) for n in p["notes"]} == {
        ("id_fk_client", "kind", "note_date", "notes", "source_id")}
    assert "SECRET-DX" not in str(p) and "1950" not in str(p) and "a@b.com" not in str(p)
    assert [(n["kind"], n["source_id"], n["note_date"]) for n in p["notes"]] == [
        ("client", "6057", ""), ("test", "900", "2026-11-20"), ("test", "901", "2026-01-05")]


def test_a_dry_run_writes_nothing_and_a_commit_merges(export, cx):
    p = fmp_notes.build_payload(*export)
    r = fmp_notes.ingest(cx, p)
    assert r["dry_run"] and r["new"] == 3 and r["client_has_email"] == 3
    assert cx.execute("SELECT COUNT(*) FROM fmp_notes").fetchone()[0] == 0
    fmp_notes.ingest(cx, p, commit=True)
    assert cx.execute("SELECT COUNT(*) FROM fmp_notes").fetchone()[0] == 3
    again = fmp_notes.ingest(cx, p, commit=True)
    assert (again["new"], again["changed"], again["unchanged"]) == (0, 0, 3)
    p["notes"][0]["notes"] = "Edited in FileMaker."
    r = fmp_notes.ingest(cx, p, commit=True)
    assert r["changed"] == 1
    assert cx.execute("SELECT COUNT(*) FROM fmp_notes").fetchone()[0] == 3
    assert fmp_notes.notes_for_email(cx, "a@b.com")[0]["text"] == "Edited in FileMaker."


def test_bad_rows_are_rejected(cx):
    r = fmp_notes.ingest(cx, {"notes": [{"kind": "diagnosis", "source_id": "1",
                                          "id_fk_client": "1", "notes": "x"}, "junk"]}, commit=True)
    assert r["rejected"] == 2 and r["received"] == 0


def test_notes_for_email_lists_client_notes_then_tests_newest_first(export, cx):
    fmp_notes.ingest(cx, fmp_notes.build_payload(*export), commit=True)
    got = fmp_notes.notes_for_email(cx, "  a@B.COM ")
    assert [(n["kind"], n["date"]) for n in got] == [
        ("client", ""), ("test", "2026-11-20"), ("test", "2026-01-05")]
    assert fmp_notes.notes_for_email(cx, "nobody@x.com") == []
    assert fmp_notes.notes_for_email(cx, "") == []


def test_no_fmp_tables_means_no_notes():
    assert fmp_notes.notes_for_email(sqlite3.connect(":memory:"), "a@b.com") == []


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _fake_360(monkeypatch, notes):
    fake = {"person": {"name": "Test"}, "clinical": {"active": [], "suggested": []}, "tests": [],
            "invoices": {"orders": [], "fmp": []}, "comms": [], "process": {"stages": []}}
    monkeypatch.setattr(app_module.client_360, "bundle", lambda cx, email, **k: dict(fake))
    monkeypatch.setattr(fmp_notes, "notes_for_email", lambda cx, email: notes)


@pytest.mark.parametrize("role,shown", [(rbac.OWNER, True), (rbac.VA, False), (rbac.OPS, False)])
def test_only_the_owner_sees_notes_on_the_client_page(client, monkeypatch, role, shown):
    monkeypatch.setattr(app_module, "_bos_actor", lambda: rbac.Actor(role=role, name="x"))
    _fake_360(monkeypatch, [{"kind": "client", "text": "private"}])
    data = client.get("/api/console/client-360?email=a@b.com").get_json()
    assert ("fmp_notes" in data) is shown


def test_the_portal_bundle_never_carries_notes():
    import inspect
    from dashboard import client_360, client_portal, fmp_orders
    for mod in (client_360, client_portal, fmp_orders):
        assert "fmp_notes" not in inspect.getsource(mod), mod.__name__


@pytest.mark.parametrize("role,code", [(rbac.OWNER, 200), (rbac.VA, 401)])
def test_only_the_owner_may_ingest(client, monkeypatch, role, code):
    monkeypatch.setattr(app_module, "_bos_actor", lambda: rbac.Actor(role=role, name="x"))
    monkeypatch.setattr(fmp_notes, "ingest", lambda cx, p, commit=False, **k: {"dry_run": not commit})
    r = client.post("/api/console/fmp-notes-ingest", json={"notes": []})
    assert r.status_code == code
    if code == 200:
        assert r.get_json()["dry_run"] is True


def _n(kind="test", sid="900", cid="6057", date="2026-11-20", text="Retest liver."):
    return {"kind": kind, "source_id": sid, "id_fk_client": cid, "note_date": date, "notes": text}


def test_a_moved_or_redated_note_is_a_change(cx):
    fmp_notes.ingest(cx, {"notes": [_n()]}, commit=True)
    r = fmp_notes.ingest(cx, {"notes": [_n(cid="77")]}, commit=True)
    assert r["changed"] == 1
    assert fmp_notes.notes_for_email(cx, "a@b.com") == []
    r = fmp_notes.ingest(cx, {"notes": [_n(cid="77", date="2026-11-21")]}, commit=True)
    assert r["changed"] == 1


def test_duplicate_keys_collapse_to_the_last_and_commit(cx):
    r = fmp_notes.ingest(cx, {"notes": [_n(text="old"), _n(sid="900.0", text="new")]}, commit=True)
    assert r["duplicates"] == 1 and r["new"] == 1
    assert [n["text"] for n in fmp_notes.notes_for_email(cx, "a@b.com")] == ["new"]


def test_a_long_note_is_kept_whole_and_an_oversized_one_is_rejected(cx):
    long = "x" * 50000
    r = fmp_notes.ingest(cx, {"notes": [_n(text=long), _n(sid="901", text="y" * (fmp_notes.MAX_NOTE + 1))]},
                         commit=True)
    assert r["rejected"] == 1
    assert fmp_notes.notes_for_email(cx, "a@b.com")[0]["text"] == long


def _full(cx, extra_tests=12):
    rows = [_n(kind="client", sid="6057", date="", text="c")] + [
        _n(sid=str(1000 + i)) for i in range(extra_tests)]
    fmp_notes.ingest(cx, {"notes": rows}, commit=True)
    return rows


def test_a_note_cleared_in_filemaker_goes_only_with_prune(cx):
    rows = _full(cx)
    r = fmp_notes.ingest(cx, {"notes": rows[:-1]}, commit=True)
    assert r["absent"] == 1 and r["pruned"] == 0
    r = fmp_notes.ingest(cx, {"notes": rows[:-1]}, commit=True, prune=True)
    assert r["pruned"] == 1 and "prune_refused" not in r
    assert len(fmp_notes.notes_for_email(cx, "a@b.com")) == 12


@pytest.mark.parametrize("why,payload", [
    ("rejected", lambda rows: rows[:-1] + [dict(rows[-1], notes="y" * (fmp_notes.MAX_NOTE + 1))]),
    ("a kind", lambda rows: rows[1:]),
    ("empty", lambda rows: []),
    ("a tenth", lambda rows: rows[:-3]),
])
def test_prune_refuses_anything_but_a_full_export(cx, why, payload):
    rows = _full(cx)
    r = fmp_notes.ingest(cx, {"notes": payload(rows)}, commit=True, prune=True)
    assert r["pruned"] == 0 and r["prune_refused"], why
    assert cx.execute("SELECT COUNT(*) FROM fmp_notes").fetchone()[0] == 13


def test_a_nul_byte_is_dropped_before_the_write(cx):
    fmp_notes.ingest(cx, {"notes": [_n(text="a\x00b")]}, commit=True)
    assert fmp_notes.notes_for_email(cx, "a@b.com")[0]["text"] == "ab"


def test_ingest_without_the_filemaker_mirror_still_creates_the_table():
    c = sqlite3.connect(":memory:")
    r = fmp_notes.ingest(c, {"notes": [_n()]}, commit=True)
    assert r["new"] == 1 and r["client_known"] == 0
    assert c.execute("SELECT COUNT(*) FROM fmp_notes").fetchone()[0] == 1


def test_an_email_found_later_reaches_its_notes(cx):
    cx.execute("CREATE TABLE client_email_fills (fmp_client_id TEXT, email TEXT)")
    cx.execute("INSERT INTO client_email_fills VALUES ('77', 'late@x.com')")
    fmp_notes.ingest(cx, {"notes": [_n(cid="77")]}, commit=True)
    assert len(fmp_notes.notes_for_email(cx, "late@x.com")) == 1
