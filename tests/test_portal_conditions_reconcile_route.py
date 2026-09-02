"""POST /api/portal/<token>/triage with a `conditions` list reconciles the
client's whole set, which is what makes the permanent Find Solutions checklist
re-runnable. Identity comes ONLY from the portal token: the body carries no
email, and one token's submit can never touch another client's conditions.
"""
import importlib
import sqlite3
import sys
from pathlib import Path


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as appmod
    importlib.reload(appmod)
    return appmod


def _seed(appmod, email):
    from dashboard import client_portal as cp, condition_programs as cprog
    cx = sqlite3.connect(appmod.LOG_DB)
    cx.row_factory = sqlite3.Row
    cp.init_client_portal_table(cx)
    cprog.init_table(cx)
    cprog.upsert(cx, "dry-eye", "Dry eye", False,
                 [{"slug": "tear-support", "name": "Tear Support"}])
    cprog.upsert(cx, "symptom-sleep", "Trouble sleeping", False,
                 [{"slug": "sleep-calm", "name": "Sleep Calm"}])
    tok = cp.ensure_token(cx, email, "T")
    cx.commit()
    return tok


def _seeds(appmod, email):
    cx = sqlite3.connect(appmod.LOG_DB)
    rows = cx.execute(
        "SELECT origin_ref, product_key FROM recommendation_events "
        "WHERE client_email=? AND source_key='condition'", (email,)).fetchall()
    out = {}
    for ref, slug in rows:
        out.setdefault(ref, set()).add(slug)
    return out


def test_reconcile_seeds_new_and_clears_unchecked(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    email = "reco-route@x.com"
    tok = _seed(appmod, email)
    client = appmod.app.test_client()

    first = client.post(f"/api/portal/{tok}/triage", json={"conditions": [
        {"condition": "dry-eye"}, {"condition": "symptom-sleep"}]})
    assert first.status_code == 200
    assert sorted(first.get_json()["added"]) == ["dry-eye", "symptom-sleep"]
    # The route seeds from the practice's own authored programs
    # (_init_support_programs_tables), so pin the SETS it produced rather than
    # the fixture's placeholder slugs, then assert what survives the unchecking.
    before = _seeds(appmod, email)
    assert before["dry-eye"] and before["symptom-sleep"]

    second = client.post(f"/api/portal/{tok}/triage",
                         json={"conditions": [{"condition": "symptom-sleep"}]})
    assert second.status_code == 200
    body = second.get_json()
    assert body["removed"] == ["dry-eye"]
    assert body["kept"] == ["symptom-sleep"]
    assert body["added"] == []
    seeds = _seeds(appmod, email)
    assert "dry-eye" not in seeds                                   # its remedies stopped
    assert seeds["symptom-sleep"] == before["symptom-sleep"]        # the other one held


def test_reconcile_rejects_a_condition_outside_the_authored_list(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    tok = _seed(appmod, "reco-bad@x.com")
    r = appmod.app.test_client().post(
        f"/api/portal/{tok}/triage", json={"conditions": [{"condition": "wallet"}]})
    assert r.status_code == 400


def test_reconcile_requires_text_for_other(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    tok = _seed(appmod, "reco-other@x.com")
    client = appmod.app.test_client()
    assert client.post(f"/api/portal/{tok}/triage",
                       json={"conditions": [{"condition": "other"}]}).status_code == 400
    ok = client.post(f"/api/portal/{tok}/triage", json={"conditions": [
        {"condition": "other", "other_condition": "floaters"}]})
    assert ok.status_code == 200 and ok.get_json()["added"] == ["other"]


def test_reconcile_is_scoped_to_the_token_holder(tmp_path, monkeypatch):
    appmod = _app(tmp_path, monkeypatch)
    mine = _seed(appmod, "mine@x.com")
    _seed(appmod, "theirs@x.com")
    client = appmod.app.test_client()
    client.post(f"/api/portal/{mine}/triage", json={"conditions": [{"condition": "dry-eye"}]})
    from dashboard import condition_triage as ct
    cx = sqlite3.connect(appmod.LOG_DB)
    assert ct.stored_conditions(cx, "mine@x.com") == ["dry-eye"]
    assert ct.stored_conditions(cx, "theirs@x.com") == []


def test_single_condition_post_still_works(tmp_path, monkeypatch):
    """The onboarding path posts one condition at a time. Unchanged."""
    appmod = _app(tmp_path, monkeypatch)
    tok = _seed(appmod, "reco-single@x.com")
    r = appmod.app.test_client().post(
        f"/api/portal/{tok}/triage", json={"condition": "dry-eye"})
    assert r.status_code == 200
    assert r.get_json()["programs"] == ["dry-eye"]
