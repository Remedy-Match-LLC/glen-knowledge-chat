import json
import sqlite3
import sys
from pathlib import Path

from dashboard import historical_intakes as hi


def _app(tmp_path, monkeypatch, console_secret="secret"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_HUB_ENABLED", "1")
    monkeypatch.setenv("PORTAL_HEALTH_PROFILE_ENABLED", "1")
    monkeypatch.setenv("CONSOLE_SECRET", console_secret)
    monkeypatch.setenv("PINECONE_API_KEY", "pcsk_fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", console_secret)
    monkeypatch.setattr(appmod, "_PORTAL_HUB_ENABLED", True)
    monkeypatch.setattr(appmod, "_PORTAL_HEALTH_PROFILE_ENABLED", True)
    return appmod


def _seed(appmod, email, *, visible=False, source_id="pb-1"):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        token = cp.ensure_token(cx, email, "Client")
        snapshot_id = hi.put_import(
            cx, person_id=7, email=email, form_date="2025-01-02",
            form_name="Practice Better Intake",
            answers={"terrain": 2, "sleep": "Woke often", "first_name": "Private",
                     "terms": {"agreed": True, "signature": "Must not survive"}},
            source_system="practice-better", source_record_id=source_id,
            import_batch_id="pilot-1")
        if visible:
            hi.review(cx, snapshot_id, approved=True, visible=True, reviewed_by="Glen")
    return token, snapshot_id


def test_store_is_idempotent_immutable_and_discards_consent():
    cx = sqlite3.connect(":memory:")
    first = hi.put_import(
        cx, person_id=1, email="A@X.com", form_date="2025-01-01", form_name="Intake",
        answers={"sleep": "old", "terms": {"agreed": True}},
        source_system="practice-better", source_record_id="same")
    second = hi.put_import(
        cx, person_id=1, email="a@x.com", form_date="2026-01-01", form_name="Changed",
        answers={"sleep": "replacement"}, source_system="practice-better",
        source_record_id="same")
    assert first == second
    row = hi.list_for_email(cx, "a@x.com")[0]
    assert row["form_date"] == "2025-01-01"
    assert row["answers"] == {"sleep": "old"}
    assert row["client_visible"] is False


def test_unreviewed_snapshot_is_invisible_to_portal(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, _ = _seed(appmod, "client@example.com")
    body = appmod.app.test_client().get(
        f"/api/portal/{token}/clinical-record/intakes").get_json()
    assert body["items"] == []


def test_approved_snapshot_exposes_only_curated_health_fields(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, _ = _seed(appmod, "client@example.com", visible=True)
    body = appmod.app.test_client().get(
        f"/api/portal/{token}/clinical-record/intakes").get_json()
    assert len(body["items"]) == 1
    fields = {field["id"]: field for field in body["items"][0]["fields"]}
    assert set(fields) == {"terrain", "sleep"}
    assert fields["terrain"]["value"] == "Rapid Aging, Bacterial, or Parasitic"
    raw = str(body)
    assert "signature" not in raw and "first_name" not in raw and "source_record_id" not in raw


def test_copy_selected_updates_current_profile_and_never_consent(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    token, snapshot_id = _seed(appmod, "client@example.com", visible=True)
    response = appmod.app.test_client().post(
        f"/api/portal/{token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["terrain", "sleep"]})
    assert response.status_code == 200
    assert response.get_json()["copied_fields"] == ["sleep", "terrain"]
    from dashboard import intake
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        answers = intake.get_response(cx, "client@example.com")["answers"]
    assert answers["terrain"] == 2 and answers["sleep"] == "Woke often"
    assert "terms" not in answers


def test_copy_rejects_identity_field_and_cross_token_access(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    owner_token, snapshot_id = _seed(appmod, "owner@example.com", visible=True)
    other_token, _ = _seed(appmod, "other@example.com", visible=True, source_id="pb-2")
    client = appmod.app.test_client()
    assert client.post(
        f"/api/portal/{owner_token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["first_name"]}).status_code == 400
    assert client.post(
        f"/api/portal/{other_token}/clinical-record/intakes/{snapshot_id}/copy-to-current",
        json={"fields": ["sleep"]}).status_code == 404


def test_console_review_requires_auth_and_rejection_forces_hidden(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    _, snapshot_id = _seed(appmod, "client@example.com")
    client = appmod.app.test_client()
    route = f"/api/console/historical-intake/{snapshot_id}/review"
    assert client.post(route, json={"approved": True, "visible": True}).status_code == 401
    response = client.post(route, json={"approved": False, "visible": True},
                           headers={"X-Console-Key": "secret"})
    assert response.status_code == 200
    assert response.get_json()["client_visible"] is False
    with sqlite3.connect(appmod.LOG_DB) as cx:
        row = hi.list_for_email(cx, "client@example.com")[0]
    assert row["review_status"] == "rejected" and row["client_visible"] is False


_BLOCK = "P5: stress; CC (9): vision. Regulating bowels."


def _snap_with_block(cx, email="k@x.com"):
    return hi.put_import(
        cx, person_id=1, email=email, form_date="2024-11-16", form_name="Application",
        answers={"health_concerns": [{"concern": "Other"}, {"concern": _BLOCK}]},
        source_system="filemaker", source_record_id="39")


def test_split_concern_replaces_in_place_and_keeps_the_original():
    cx = sqlite3.connect(":memory:")
    sid = _snap_with_block(cx)
    snap = hi.split_concern(cx, sid, "K@X.com", _BLOCK, ["Vision", " Bowel regulation ", "Vision"],
                            split_by="Glen")
    a = snap["answers"]
    assert a["health_concerns"] == [{"concern": "Other"}, {"concern": "Vision"},
                                    {"concern": "Bowel regulation"}]
    [h] = a["health_concerns_split"]
    assert h["original"] == _BLOCK and h["into"] == ["Vision", "Bowel regulation"]
    assert h["by"] == "Glen"


def test_split_concern_refuses_a_wrong_owner_wording_or_part():
    cx = sqlite3.connect(":memory:")
    sid = _snap_with_block(cx)
    assert hi.split_concern(cx, sid, "other@x.com", _BLOCK, ["Vision"], split_by="") is None
    assert hi.split_concern(cx, sid, "k@x.com", _BLOCK + " ", ["Vision"], split_by="") is None
    for bad in ([], [""], ["x" * 161]):
        try:
            hi.split_concern(cx, sid, "k@x.com", _BLOCK, bad, split_by="")
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    assert hi.list_for_email(cx, "k@x.com")[0]["answers"]["health_concerns"][1] == {"concern": _BLOCK}


def test_a_reimport_does_not_undo_a_split():
    cx = sqlite3.connect(":memory:")
    sid = _snap_with_block(cx)
    hi.split_concern(cx, sid, "k@x.com", _BLOCK, ["Vision"], split_by="")
    _snap_with_block(cx)
    assert {"concern": _BLOCK} not in hi.list_for_email(cx, "k@x.com")[0]["answers"]["health_concerns"]


def test_split_route_needs_the_console_key_and_splits(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    with sqlite3.connect(appmod.LOG_DB) as cx:
        sid = _snap_with_block(cx)
    c = appmod.app.test_client()
    url = f"/api/console/historical-intake/{sid}/split-concern"
    body = {"email": "k@x.com", "concern": _BLOCK, "into": ["Vision"]}
    assert c.post(url, json=body).status_code == 401
    r = c.post(url, json=body, headers={"X-Console-Key": "secret"})
    assert r.status_code == 200
    assert r.get_json()["health_concerns"] == [{"concern": "Other"}, {"concern": "Vision"}]
    r = c.post(url, json=body, headers={"X-Console-Key": "secret"})
    assert r.status_code == 404
    r = c.post(url, json={**body, "into": "Vision"}, headers={"X-Console-Key": "secret"})
    assert r.status_code == 400


def test_split_parts_keep_the_rows_other_fields_and_history_keeps_the_row():
    cx = sqlite3.connect(":memory:")
    sid = hi.put_import(
        cx, person_id=1, email="k@x.com", form_date="", form_name="",
        answers={"health_concerns": [{"concern": _BLOCK, "rating": "9", "years_since_onset": "3"}]},
        source_system="filemaker", source_record_id="40")
    a = hi.split_concern(cx, sid, "k@x.com", _BLOCK, ["Vision", "Bowels"], split_by="")["answers"]
    assert a["health_concerns"] == [
        {"concern": "Vision", "rating": "9", "years_since_onset": "3"},
        {"concern": "Bowels", "rating": "9", "years_since_onset": "3"}]
    assert a["health_concerns_split"][0]["original_row"] == {
        "concern": _BLOCK, "rating": "9", "years_since_onset": "3"}


def test_split_refuses_a_wording_stored_twice_or_a_part_equal_to_it():
    cx = sqlite3.connect(":memory:")
    sid = hi.put_import(
        cx, person_id=1, email="k@x.com", form_date="", form_name="",
        answers={"health_concerns": [{"concern": _BLOCK}, {"concern": _BLOCK}]},
        source_system="filemaker", source_record_id="41")
    for parts in (["Vision"],):
        try:
            hi.split_concern(cx, sid, "k@x.com", _BLOCK, parts, split_by="")
            raise AssertionError("split a duplicated wording")
        except ValueError:
            pass
    sid2 = _snap_with_block(cx, email="j@x.com")
    try:
        hi.split_concern(cx, sid2, "j@x.com", _BLOCK, [_BLOCK, "Vision"], split_by="")
        raise AssertionError("accepted the whole concern as a part")
    except ValueError:
        pass


def test_split_does_not_write_over_a_change_made_after_it_read():
    cx = sqlite3.connect(":memory:")
    sid = _snap_with_block(cx)
    real = cx.execute

    class Racing:
        """Changes the row between split_concern's read and its write."""
        def __init__(self):
            self.done = False

        def execute(self, sql, params=()):
            if sql.startswith("UPDATE historical_intake_snapshots SET answers_json") and not self.done:
                self.done = True
                real("UPDATE historical_intake_snapshots SET answers_json=? WHERE id=?",
                     (json.dumps({"health_concerns": [{"concern": _BLOCK}], "x": 1}), sid))
            return real(sql, params)

        def commit(self):
            cx.commit()

    assert hi.split_concern(Racing(), sid, "k@x.com", _BLOCK, ["Vision"], split_by="") is None
    assert hi.list_for_email(cx, "k@x.com")[0]["answers"]["x"] == 1


def test_a_part_already_stored_is_not_added_twice():
    cx = sqlite3.connect(":memory:")
    sid = _snap_with_block(cx)
    a = hi.split_concern(cx, sid, "k@x.com", _BLOCK, ["Other", "Vision"], split_by="")["answers"]
    assert [r["concern"] for r in a["health_concerns"]] == ["Other", "Vision"]
    assert a["health_concerns_split"][0]["index"] == 1
