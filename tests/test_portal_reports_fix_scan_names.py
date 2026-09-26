"""Older portal reports still said "voice scan" (clinical, 2026-09-25). The fix writes a
report row's content only: never the portal's main content, never status, never mail."""
import json
import sqlite3

import pytest

from dashboard import portal_biofield_reports as pbr

SECRET = "test-secret"


def _seed(cx):
    pbr.init_table(cx)
    pbr.upsert_report(cx, "a@x.com", "2026-06-01", "s1",
                      {"narrative": "Your recent E4L Voice Scan showed stress.",
                       "layers": [{"meaning": "as corroborated by your voice scan."}]}, "confirmed")
    pbr.upsert_report(cx, "a@x.com", "2026-09-01", "s2",
                      {"narrative": "Your Bioenergetic Wellness Scan showed calm."}, "confirmed")
    pbr.upsert_report(cx, "b@x.com", "2026-07-01", "s3",
                      {"narrative": "Your Five Element Voice Scan showed Water. The voice scan "
                                    "also showed Kidney."}, "draft")


def _rows(cx):
    return {r[0]: (r[1], r[2], r[3]) for r in cx.execute(
        "SELECT scan_date, content_json, status, updated_at FROM portal_biofield_reports")}


def test_dry_run_changes_nothing(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _seed(cx)
    before = _rows(cx)
    out = pbr.fix_scan_names_in_reports(cx)
    assert out["rows"] == 3
    assert [c["scan_date"] for c in out["changed"]] == ["2026-06-01"]
    assert [c["scan_date"] for c in out["left_for_glen"]] == ["2026-07-01"]
    assert _rows(cx) == before


def test_apply_rewrites_only_the_content_of_affected_rows(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _seed(cx)
    before = _rows(cx)
    pbr.fix_scan_names_in_reports(cx, apply=True)
    after = _rows(cx)
    content = json.loads(after["2026-06-01"][0])
    assert content["narrative"] == "Your recent Bioenergetic Wellness Scan showed stress."
    assert content["layers"][0]["meaning"] == "as corroborated by your Bioenergetic Wellness Scan."
    assert after["2026-06-01"][1:] == before["2026-06-01"][1:]      # status, updated_at kept
    assert after["2026-09-01"] == before["2026-09-01"]
    assert after["2026-07-01"] == before["2026-07-01"]              # Five Element left alone


def test_apply_twice_is_a_no_op(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _seed(cx)
    pbr.fix_scan_names_in_reports(cx, apply=True)
    assert pbr.fix_scan_names_in_reports(cx, apply=True)["changed"] == []


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def test_route_is_owner_only_dry_run_by_default_and_leaves_the_portal_alone(client):
    c, appmod = client
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    _seed(cx)
    cp.init_client_portal_table(cx)
    cp.upsert_portal(cx, "a@x.com", "A", {"greeting": "Your E4L voice scan is in."})
    portal_before = cx.execute("SELECT content_json, updated_at FROM client_portals").fetchall()
    cx.close()
    url = "/admin/portal/biofield-reports/fix-scan-names"
    assert c.post(url, json={}).status_code == 401
    dry = c.post(url, json={}, headers={"X-Console-Key": SECRET}).get_json()
    assert dry["applied"] is False and dry["changed"] == 1 and dry["left_for_glen"] == 1
    for truthy in ("true", 1, "yes"):
        assert c.post(url, json={"apply": truthy},
                      headers={"X-Console-Key": SECRET}).get_json()["applied"] is False
    done = c.post(url, json={"apply": True}, headers={"X-Console-Key": SECRET}).get_json()
    assert done["applied"] is True and done["changed"] == 1
    cx = sqlite3.connect(appmod.LOG_DB)
    assert cx.execute("SELECT content_json, updated_at FROM client_portals").fetchall() == portal_before


# ── blind review round 1, 2026-09-26 ────────────────────────────────────────

def _one(cx, content, status="confirmed"):
    pbr.init_table(cx)
    pbr.upsert_report(cx, "c@x.com", "2026-05-01", "s", content, status)


def test_links_and_file_names_are_never_reworded(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"report_pdf": {"url": "https://cdn.x.com/reports/voice-scan-2026.pdf"},
              "file": "voice scan notes.txt", "note": "/files/voice-scan.mp3",
              "narrative": "Your voice scan showed it."})
    pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["report_pdf"]["url"] == "https://cdn.x.com/reports/voice-scan-2026.pdf"
    assert c["file"] == "voice scan notes.txt" and c["note"] == "/files/voice-scan.mp3"
    assert c["narrative"] == "Your Bioenergetic Wellness Scan showed it."


def test_dry_run_names_the_changed_fields_without_their_text(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"layers": [{"meaning": "voice scan"}], "narrative": "ok"})
    out = pbr.fix_scan_names_in_reports(cx)
    assert out["changed"][0]["fields"] == ["layers[0].meaning"]
    assert "voice" not in json.dumps(out)


def test_a_report_naming_five_element_anywhere_keeps_every_bare_voice_scan(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"five": "Your Five Element Voice Scan showed Water.",
              "e4l": "The voice scan showed liver stress.",
              "q": "Your E4L voice scan agrees."})
    out = pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["e4l"] == "The voice scan showed liver stress."
    assert c["q"] == "Your Bioenergetic Wellness Scan agrees."      # E4L-qualified is clear
    assert len(out["left_for_glen"]) == 1


def test_an_edit_landing_mid_run_is_not_reverted(tmp_path, monkeypatch):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"n": "voice scan", "pdf": "old"})
    real = pbr._rewrite_strings

    def racing(value, fix, path="", key=""):
        if path == "":
            pbr.upsert_report(cx, "c@x.com", "2026-05-01", "s", {"n": "voice scan", "pdf": "NEW"},
                              "confirmed")
        return real(value, fix, path, key)
    monkeypatch.setattr(pbr, "_rewrite_strings", racing)
    out = pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["pdf"] == "NEW" and len(out["raced"]) == 1 and out["changed"] == []


def test_a_row_too_deep_to_walk_is_skipped_not_fatal(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    pbr.init_table(cx)
    deep = "[" * 3000 + '"voice scan"' + "]" * 3000
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, content_json, status) "
               "VALUES ('d@x.com','2026-01-01',?,'confirmed')", (deep,))
    _one(cx, {"n": "voice scan"})
    out = pbr.fix_scan_names_in_reports(cx)
    assert len(out["skipped"]) == 1 and len(out["changed"]) == 1


# ── blind review round 2, 2026-09-26 ────────────────────────────────────────

def test_a_link_inside_prose_is_left_exactly_as_written(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"greeting": "Watch https://x.com/e4l-voice-scan-guide before your voice scan review.",
              "md": "See [the guide](https://x.com/voice-scan-2026.pdf) about the voice scan.",
              "html": 'Open <a href="/files/voice-scan.pdf">the voice scan notes</a>.'})
    pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["greeting"] == ("Watch https://x.com/e4l-voice-scan-guide before your "
                             "Bioenergetic Wellness Scan review.")
    assert c["md"] == ("See [the guide](https://x.com/voice-scan-2026.pdf) about the "
                       "Bioenergetic Wellness Scan.")
    assert c["html"] == 'Open <a href="/files/voice-scan.pdf">the Bioenergetic Wellness Scan notes</a>.'


def test_five_elements_as_a_plain_phrase_does_not_block_the_report(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"remedy": "Five Elements Tea", "narrative": "Your voice scan showed it."})
    out = pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["narrative"] == "Your Bioenergetic Wellness Scan showed it." and out["left_for_glen"] == []


def test_five_element_split_across_fields_holds_the_report(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    _one(cx, {"title": "Five Element", "sub": "Voice Scan", "n": "The voice scan showed it."})
    out = pbr.fix_scan_names_in_reports(cx, apply=True)
    c = json.loads(cx.execute("SELECT content_json FROM portal_biofield_reports").fetchone()[0])
    assert c["sub"] == "Voice Scan" and c["n"] == "The voice scan showed it."
    assert len(out["left_for_glen"]) == 1
