"""Endpoint-level tests for the portal health-suggestions review queue:
GET /api/portal/<token>/health-suggestions and
POST /api/portal/<token>/health-suggestions/<sid>/resolve.

Follows the tests/test_health_profile_endpoint.py fixture pattern: swap LOG_DB
to a tmp sqlite file so tests never touch the dev db, and use app.test_client().
"""
import os
# Dummy keys so `import app` (which constructs OpenAI + Pinecone clients at import)
# succeeds under a secretless CI without doppler. The clients are constructed but
# never called by these endpoints under test (DB-only write path).
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ.setdefault("PINECONE_API_KEY", "pc-dummy")

import sqlite3
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _mint_portal(appmod, email):
    from dashboard import client_portal as _cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        token, _pid = _cp.upsert_portal(cx, email, "Test Client", {})
    return token


def _seed_suggestion(appmod, email, field_id, value, source="chat"):
    from dashboard import health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sid = hs.add_pending(cx, email, field_id, value, "mentioned in chat", source=source)
    return sid


def test_flag_off_returns_404_for_list(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    r = c.get("/api/portal/whatever-token/health-suggestions")
    assert r.status_code == 404


def test_flag_off_returns_404_for_resolve(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", False)
    r = c.post("/api/portal/whatever-token/health-suggestions/1/resolve",
               json={"action": "dismiss"})
    assert r.status_code == 404


def test_list_pending_for_token_email(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_suggestion(appmod, "a@b.com", "terrain", 3)
    r = c.get(f"/api/portal/{token}/health-suggestions")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["field_id"] == "terrain"


def test_confirm_writes_value_and_clears_pending(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    sid = _seed_suggestion(appmod, "a@b.com", "terrain", 3)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "confirm"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    from dashboard import intake, health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        row = intake.get_response(cx, "a@b.com")
        pending = hs.list_pending(cx, "a@b.com")
    assert row["answers"]["terrain"] == 3
    assert pending == []


def test_edit_writes_supplied_value_not_suggested_value(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    sid = _seed_suggestion(appmod, "a@b.com", "terrain", 3)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "edit", "value": 4})
    assert r.status_code == 200

    from dashboard import intake, health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        row = intake.get_response(cx, "a@b.com")
        pending = hs.list_pending(cx, "a@b.com")
    assert row["answers"]["terrain"] == 4
    assert pending == []


def test_dismiss_clears_pending_without_writing_record(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    sid = _seed_suggestion(appmod, "a@b.com", "sleep", "improving")
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "dismiss"})
    assert r.status_code == 200

    from dashboard import intake, health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        row = intake.get_response(cx, "a@b.com")
        pending = hs.list_pending(cx, "a@b.com")
    assert row is None  # never had an intake row -- dismiss never creates one
    assert pending == []


def test_unknown_action_returns_400(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    sid = _seed_suggestion(appmod, "a@b.com", "terrain", 3)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "delete"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False

    from dashboard import health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        pending = hs.list_pending(cx, "a@b.com")
    assert len(pending) == 1  # untouched


def test_sid_belonging_to_different_email_404_and_no_write(client):
    c, appmod = client
    token = _mint_portal(appmod, "victim@b.com")
    sid = _seed_suggestion(appmod, "other@b.com", "terrain", 3)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "confirm"})
    assert r.status_code == 404

    from dashboard import intake, health_suggestions as hs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        victim_row = intake.get_response(cx, "victim@b.com")
        other_row = intake.get_response(cx, "other@b.com")
        other_pending = hs.list_pending(cx, "other@b.com")
    assert victim_row is None
    assert other_row is None  # never written -- ownership check blocked it
    assert len(other_pending) == 1  # still pending -- not resolved either


def test_identity_from_token_not_body(client):
    c, appmod = client
    token = _mint_portal(appmod, "real@b.com")
    sid = _seed_suggestion(appmod, "real@b.com", "terrain", 3)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "confirm", "email": "attacker@evil.com"})
    assert r.status_code == 200

    from dashboard import intake
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        real = intake.get_response(cx, "real@b.com")
        attacker = intake.get_response(cx, "attacker@evil.com")
    assert real["answers"]["terrain"] == 3
    assert attacker is None


# --- append, never replace (Glen, 20 Oct 2026) --------------------------------

def _seed_intake(appmod, email, answers):
    from dashboard import intake
    with sqlite3.connect(appmod.LOG_DB) as cx:
        intake.init_intake_table(cx)
        intake.submit(cx, email, answers, "2026-10-01T00:00:00")


def _answers(appmod, email):
    from dashboard import intake
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        return intake.get_response(cx, email)["answers"]


@pytest.mark.parametrize("field,old,new", [
    ("diagnoses", {"diagnosis": "Asthma", "current": "Past"},
     {"diagnosis": "Pregnancy", "current": "Current"}),
    ("surgeries", {"procedure": "Appendectomy"}, {"procedure": "Pacemaker", "age": 62}),
    ("medications", {"medication": "Metformin"}, {"medication": "Warfarin", "reason": "blood"}),
    ("otc_drugs", {"medication": "Aspirin"}, {"medication": "Ibuprofen"}),
    ("allergies", {"sensitivity": "Shellfish"}, {"sensitivity": "Peanuts", "reaction": "hives"}),
])
def test_confirm_table_row_appends_to_existing_rows(client, field, old, new):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {field: [old], "sleep": "fine"})
    sid = _seed_suggestion(appmod, "a@b.com", field, new)
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve", json={"action": "confirm"})
    assert r.status_code == 200, r.get_json()
    a = _answers(appmod, "a@b.com")
    assert a[field] == [old, new]
    assert a["sleep"] == "fine"


def test_confirm_identical_row_is_not_doubled(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    row = {"medication": "Warfarin"}
    _seed_intake(appmod, "a@b.com", {"medications": [row]})
    sid = _seed_suggestion(appmod, "a@b.com", "medications", row)
    c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve", json={"action": "confirm"})
    assert _answers(appmod, "a@b.com")["medications"] == [row]


@pytest.mark.parametrize("field", ["diet", "alcohol"])
def test_confirm_text_appends_on_a_new_line(client, field):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {field: "What I wrote myself."})
    sid = _seed_suggestion(appmod, "a@b.com", field, "Added from chat")
    c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve", json={"action": "confirm"})
    assert _answers(appmod, "a@b.com")[field] == "What I wrote myself.\nAdded from chat"


def test_edit_table_row_object_appends(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {"medications": [{"medication": "Metformin"}]})
    sid = _seed_suggestion(appmod, "a@b.com", "medications", {"medication": "Warfrin"})
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "edit", "value": {"medication": "Warfarin", "reason": "AF"}})
    assert r.status_code == 200
    assert _answers(appmod, "a@b.com")["medications"] == [
        {"medication": "Metformin"}, {"medication": "Warfarin", "reason": "AF"}]


def test_edit_table_with_a_bare_string_appends_a_first_column_row(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {"medications": [{"medication": "Metformin"}]})
    sid = _seed_suggestion(appmod, "a@b.com", "medications", {"medication": "Warfarin"})
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "edit", "value": "warfarin"})
    assert r.status_code == 200
    assert _answers(appmod, "a@b.com")["medications"] == [
        {"medication": "Metformin"}, {"medication": "warfarin"}]


def test_old_shape_clinician_string_confirms_as_a_row(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {"diagnoses": "MS, asthma"})
    sid = _seed_suggestion(appmod, "a@b.com", "diagnoses", "Hypothyroidism", source="clinician")
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve", json={"action": "confirm"})
    assert r.status_code == 200, r.get_json()
    assert _answers(appmod, "a@b.com")["diagnoses"] == [
        {"diagnosis": "MS, asthma"}, {"diagnosis": "Hypothyroidism"}]


def test_edit_table_with_nothing_usable_is_refused_and_keeps_the_table(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {"medications": [{"medication": "Metformin"}]})
    sid = _seed_suggestion(appmod, "a@b.com", "medications", {"medication": "Warfarin"})
    r = c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
               json={"action": "edit", "value": {"reason": "only a reason"}})
    assert r.status_code == 400
    assert _answers(appmod, "a@b.com")["medications"] == [{"medication": "Metformin"}]


def test_edit_text_appends(client):
    c, appmod = client
    token = _mint_portal(appmod, "a@b.com")
    _seed_intake(appmod, "a@b.com", {"diet": "Vegetarian."})
    sid = _seed_suggestion(appmod, "a@b.com", "diet", "Low salt")
    c.post(f"/api/portal/{token}/health-suggestions/{sid}/resolve",
           json={"action": "edit", "value": "Low salt, doctor's orders"})
    assert _answers(appmod, "a@b.com")["diet"] == "Vegetarian.\nLow salt, doctor's orders"


def test_health_profile_block_carries_column_labels(client):
    c, appmod = client
    from dashboard import health_profile
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        from dashboard import intake
        intake.init_intake_table(cx)
        block = health_profile.build_block(cx, "a@b.com", True)
    fields = {f["id"]: f for s in block["sections"] for f in s["fields"]}
    assert fields["medications"]["columns"][0] == {"id": "medication", "label": "Medication"}
    assert "columns" not in fields["diet"]
