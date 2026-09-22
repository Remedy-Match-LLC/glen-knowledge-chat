"""A layer can carry several remedies. Counting remedy rows is not counting layers.

Glen, 2026-09-22: for Peach Goddard's intake, Clinical layers -> propose replied
"This intake already has 7 layer(s)" when it has 4. authored_report()'s "layers" list
holds one ROW per remedy (a41: 7 rows across 4 layers), and four routes took len() of
it. The same count then numbered new layers, so the next layer landed as 8, not 5.

These run against an intake shaped like hers: 7 rows in 4 layers.
"""
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)


# (stored layer, head, remedy): layer 1 has 2 remedies, layer 2 has 3.
_PEACH = [(1, "Liver congestion", "Liver Support"), (1, "Liver congestion", "Lipid Cleanse"),
          (2, "Gut terrain", "Microbiome"), (2, "Gut terrain", "Terrain Restore"),
          (2, "Gut terrain", "Clear the Way"), (3, "Adrenal load", "Adrenal Syntropy"),
          (4, "Sleep", "Sleep Syntropy")]


def _intake(tmp_path):
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    from dashboard.biofield_stress import add_stress, init_stress_tables
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Peach", "peach@example.com", "2026-09-22")
        for layer, head, remedy in _PEACH:
            add_chain_row(cx, tid, layer, head, "", remedy)
        init_stress_tables(cx)
        add_stress(cx, tid, "Kidney Driver", source="scan", balance="required")
    client = create_app(db, fetch_profile=lambda email: {},
                        fetch_recent_comms=lambda email: {}).test_client()
    return client, tid, db


def _stored_layers(db):
    with sqlite3.connect(db) as cx:
        return [r[0] for r in cx.execute(
            "SELECT layer FROM biofield_auth_chain ORDER BY id")]


def test_propose_reports_layers_not_remedies(tmp_path):
    client, tid, _ = _intake(tmp_path)
    j = client.post(f"/author/{tid}/balance-all", json={}).get_json()
    assert j["ok"] is True
    assert j["existing_layers"] == 4, "REGRESSION: counted 7 remedy rows as layers"


def test_apply_refusal_names_the_layer_count(tmp_path):
    client, tid, _ = _intake(tmp_path)
    j = client.post(f"/author/{tid}/balance-all", json={"apply": True}).get_json()
    assert j["needs_confirm"] is True and j["existing"] == 4
    assert j["error"] == "This intake already has 4 layer(s)."


def test_an_appended_layer_follows_the_last_stored_layer(tmp_path):
    client, tid, db = _intake(tmp_path)
    j = client.post(f"/author/{tid}/balance-all",
                    json={"apply": True, "force": True}).get_json()
    assert j["ok"] is True and j["layers_added"] >= 1
    new = _stored_layers(db)[len(_PEACH):]
    assert new[0] == 5, "REGRESSION: numbered from the remedy count (8), not the layers"


def test_reveal_import_counts_layers_and_appends_after_them(tmp_path, monkeypatch):
    import dashboard.biofield_reveal_import as RI
    monkeypatch.setattr(RI, "synthesize_reveal_layers", lambda *a, **k: {
        "found": True, "scan_id": 1, "scan_date": "2026-09-22", "days_ago": 0,
        "fresh": True, "layers": [
            {"n": 1, "title": "Oxidative load", "most_affected": "", "remedy_name": ""},
            {"n": 2, "title": "Mineral need", "most_affected": "", "remedy_name": ""}]})
    client, tid, db = _intake(tmp_path)
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={}).get_json()
    assert j == {"ok": False, "needs_confirm": True, "existing": 4}
    j = client.post(f"/author/{tid}/e4l/import-reveal", json={"force": True}).get_json()
    assert j["ok"] is True
    assert _stored_layers(db)[len(_PEACH):] == [5, 6], (
        "appended reveal layers must not reuse numbers 1 and 2")


def test_import_layers_to_test_keeps_reveal_numbers_on_a_fresh_intake(tmp_path):
    from dashboard.biofield_authoring import create_test, init_auth_tables
    from dashboard.biofield_reveal_import import import_layers_to_test
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "New", "new@example.com", "2026-09-22")
        import_layers_to_test(cx, tid, [{"n": 1, "title": "A"}, {"n": 2, "title": "B"}])
    assert _stored_layers(db) == [1, 2]
