"""The console publish takes report_kind (Glen, platform tab, 2026-11-25: "yes, fix the
publish route").

Linda Maher's FileMaker Biofield test 263 was published with E4L scan_id 1079926. The
route passed no kind, so the rule read the numeric scan_id as Remedy Match. Her page then
said "Remedy Match" and showed the AI notice. The publisher now says what made the report.
"""
import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


_LAYER = {"n": 1, "title": "Calm", "meaning": "settle", "remedy": "Terrain Restore",
          "dosing": "10 drops 3x/day, or as guided"}


def _publish(c, **extra):
    body = {"email": "x@y.com", "name": "X", "scan_date": "2026-11-24", "scan_id": "1079926",
            "content": {"greeting": "Aloha", "layers": [_LAYER]}}
    body.update(extra)
    r = c.post("/api/console/biofield-portal", json=body,
               headers={"X-Console-Key": "test-secret"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def _kind(appmod):
    from dashboard import db
    from dashboard import portal_biofield_reports as pbr
    with db.connect(appmod.LOG_DB) as cx:
        return pbr.get_report(cx, "x@y.com", "2026-11-24")["report_kind"]


def test_a_numeric_scan_id_with_no_kind_still_reads_as_remedy_match(client):
    c, appmod = client
    _publish(c)
    assert _kind(appmod) == "remedy_match"


def test_the_publisher_can_say_biofield(client):
    c, appmod = client
    j = _publish(c, report_kind="biofield")
    assert j["emailed"] is False
    assert _kind(appmod) == "biofield"


def test_a_republish_with_no_kind_keeps_the_stored_kind(client):
    c, appmod = client
    _publish(c, report_kind="biofield")
    _publish(c)
    assert _kind(appmod) == "biofield"


@pytest.mark.parametrize("junk", ["nonsense", 7, ["biofield"], ""])
def test_junk_kind_is_ignored(client, junk):
    c, appmod = client
    _publish(c, report_kind=junk)
    assert _kind(appmod) == "remedy_match"


def test_the_publisher_can_correct_a_wrong_kind(client):
    c, appmod = client
    _publish(c)
    _publish(c, report_kind="biofield")
    assert _kind(appmod) == "biofield"
