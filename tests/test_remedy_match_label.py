"""Remedy Match versus Biofield: the report_kind column, the rule for old rows, and the
words each kind shows a client (Glen, 2026-10-20, approved wording).

"Use Remedy Match for the AI matching of our FF's to her scan." "Biofield" is the paid
manual analysis, and it keeps every word it had. Plan:
platform/plans/2026-10-20-remedy-match-label-scope.md."""
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dashboard import portal_biofield_reports as pbr

ROOT = Path(__file__).resolve().parent.parent
PORTAL = (ROOT / "static" / "client-portal.html").read_text()
CONSOLE = (ROOT / "static" / "console-biofield-portal.html").read_text()
REVEAL_PAGE = (ROOT / "static" / "begin-biofield.html").read_text()

# One fixture per writer, shaped as each writer leaves its content.
SCAN_PIPELINE = {"biofield_status": "ai_draft", "greeting": "Aloha Gita,",
                 "layers": [{"n": 1, "title": "Liver", "remedy": "Vitality",
                             "patterns": ["ED3"], "alternatives": [{"name": "Chelation"}]}]}
ANIMAL = {"biofield_status": "ai_draft", "report_kind": "animal_infoceuticals",
          "layers": [{"n": 1, "title": "Kidney", "remedy": "IC 4"}]}
AUTOCONFIRMED = {"biofield_status": "confirmed",
                 "layers": [{"n": 1, "title": "Liver", "remedy": "Vitality", "patterns": ["ED3"]}]}
PAID_PUBLISH = {"greeting": "Aloha Kai,", "phase": 3, "location": "liver", "schedule": {},
                "comped_intake": False, "client_id": "77", "layers": [{"n": 1, "title": "L"}],
                "biofield_status": "confirmed"}
PAID_PDF_ONLY = {"greeting": "Aloha", "report_pdf": {"url": "https://x/portal-asset/b.pdf"},
                 "layers": []}
HANDOFF = {"greeting": "Aloha Kai, Here is your Biofield Analysis", "layers": [],
           "biofield_status": "ai_draft"}
LEGACY = {"legacy_source": "healing_oasis_legacy_fmp", "greeting": "Your historical report."}
# What the console confirm leaves behind: greeting, layers without patterns, findings,
# an order list. No writer's marks survive, so the rule cannot say.
CONSOLE_STRIPPED = {"greeting": "Aloha", "layers": [{"n": 1, "title": "L", "remedy": "R"}],
                    "findings": [], "reorder_items": [], "biofield_status": "confirmed"}


def _db(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "r.db"))
    pbr.init_table(cx)
    return cx


def _stored(cx, email, scan_date):
    return cx.execute("SELECT report_kind FROM portal_biofield_reports WHERE email=? AND "
                      "scan_date=?", (email, scan_date)).fetchone()[0]


# ── The rule for a row with no stored kind ─────────────────────────────────────

@pytest.mark.parametrize("content,scan_id,want", [
    (SCAN_PIPELINE, "1035975", "remedy_match"),
    (dict(SCAN_PIPELINE, layers=[{"n": 1, "patterns": ["ED3"]}]), "", "remedy_match"),
    (dict(SCAN_PIPELINE, layers=[{"n": 1, "second_match": "x"}]), "", "remedy_match"),
    (dict(SCAN_PIPELINE, layers=[{"n": 1, "ranked_second": []}]), "", "remedy_match"),
    (ANIMAL, "", "remedy_match"),
    (AUTOCONFIRMED, "", "remedy_match"),
    ({"layers": []}, "1035975", "remedy_match"),
    (PAID_PUBLISH, "", "biofield"),
    (PAID_PDF_ONLY, "", "biofield"),
    (HANDOFF, "", "biofield"),
    (LEGACY, "", "biofield"),
    ({"layers": []}, "legacy-fmp:a@x.com:2019-01-02", "biofield"),
    (CONSOLE_STRIPPED, "", None),
    ({}, "", None),
    # A paid mark wins over an AI mark: the paid publish is the later, deliberate act.
    (dict(SCAN_PIPELINE, phase=2), "1035975", "biofield"),
])
def test_infer_kind_per_writer(content, scan_id, want):
    assert pbr.infer_kind(content, scan_id) == want


def test_empty_patterns_and_blank_pdf_are_not_marks():
    assert pbr.infer_kind({"layers": [{"n": 1, "patterns": []}]}, "") is None
    assert pbr.infer_kind({"report_pdf": {"url": ""}}, "") is None


def test_unclassifiable_reads_as_biofield():
    assert pbr.effective_kind(None, CONSOLE_STRIPPED, "") == "biofield"
    assert pbr.effective_kind("remedy_match", PAID_PUBLISH, "") == "remedy_match"  # stored wins


# ── The column and upsert_report ──────────────────────────────────────────────

def test_column_added_to_an_existing_table(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "old.db"))
    cx.execute("CREATE TABLE portal_biofield_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "email TEXT, scan_date TEXT, scan_id TEXT, content_json TEXT, status TEXT, "
               "created_at TEXT, updated_at TEXT, UNIQUE(email, scan_date))")
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, scan_id, content_json, "
               "status) VALUES ('a@x.com','2026-10-14','',?, 'ai_draft')",
               (json.dumps(SCAN_PIPELINE),))
    cx.commit()
    pbr.init_table(cx)
    pbr.init_table(cx)                                   # idempotent
    assert _stored(cx, "a@x.com", "2026-10-14") is None  # old rows stay unclassified
    assert pbr.get_report(cx, "a@x.com", "2026-10-14")["report_kind"] == "remedy_match"


def test_insert_with_no_kind_uses_the_rule(tmp_path):
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "1035975", SCAN_PIPELINE, "ai_draft")
    pbr.upsert_report(cx, "b@x.com", "2026-10-14", "", CONSOLE_STRIPPED, "confirmed")
    assert _stored(cx, "a@x.com", "2026-10-14") == "remedy_match"
    assert _stored(cx, "b@x.com", "2026-10-14") is None
    assert pbr.get_report(cx, "b@x.com", "2026-10-14")["report_kind"] == "biofield"


def test_update_with_no_kind_keeps_the_stored_kind(tmp_path):
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {"layers": []}, "ai_draft",
                      report_kind="remedy_match")
    # A console confirm: content rebuilt with paid-looking fields, no kind passed.
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", PAID_PUBLISH, "confirmed")
    assert _stored(cx, "a@x.com", "2026-10-14") == "remedy_match"
    pbr.upsert_report(cx, "p@x.com", "2026-10-14", "", {"layers": []}, "ai_draft",
                      report_kind="biofield")
    pbr.upsert_report(cx, "p@x.com", "2026-10-14", "", SCAN_PIPELINE, "confirmed")
    assert _stored(cx, "p@x.com", "2026-10-14") == "biofield"


def test_an_unclassified_row_is_classified_from_its_old_content_before_overwrite(tmp_path):
    # A paid hand-off written before the column existed: the console confirm strips its
    # marks, so the kind must be read from the content it is replacing.
    cx = _db(tmp_path)
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, scan_id, content_json, "
               "status) VALUES ('h@x.com','2026-10-14','',?, 'ai_draft')", (json.dumps(HANDOFF),))
    cx.commit()
    pbr.upsert_report(cx, "h@x.com", "2026-10-14", "", CONSOLE_STRIPPED, "confirmed")
    assert _stored(cx, "h@x.com", "2026-10-14") == "biofield"


def test_an_explicit_kind_is_written_and_junk_is_ignored(tmp_path):
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {}, "ai_draft", report_kind="biofield")
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {}, "ai_draft", report_kind="remedy_match")
    assert _stored(cx, "a@x.com", "2026-10-14") == "remedy_match"
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {}, "ai_draft", report_kind="nonsense")
    assert _stored(cx, "a@x.com", "2026-10-14") == "remedy_match"
    assert pbr.normalize_kind("animal_infoceuticals") == "remedy_match"


class _RacingCx:
    """A connection on which another writer commits a kind right after upsert_report
    reads the row, as a second worker would."""
    def __init__(self, cx, kind):
        self._cx, self._kind = cx, kind

    def execute(self, sql, params=()):
        cur = self._cx.execute(sql, params)
        if sql.startswith("SELECT id, report_kind, content_json"):
            rows = cur.fetchall()
            self._cx.execute("UPDATE portal_biofield_reports SET report_kind=?", (self._kind,))
            return _Rows(rows)
        return cur

    def commit(self):
        self._cx.commit()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_update_never_overwrites_a_kind_committed_after_its_read(tmp_path):
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {}, "ai_draft", report_kind="remedy_match")
    # A console confirm (no kind) reads remedy_match; a paid publish then commits
    # biofield before the confirm writes. The confirm must not put remedy_match back.
    pbr.upsert_report(_RacingCx(cx, "biofield"), "a@x.com", "2026-10-14", "", {}, "confirmed")
    assert _stored(cx, "a@x.com", "2026-10-14") == "biofield"


def test_update_from_old_content_never_overwrites_a_kind_set_after_its_read(tmp_path):
    cx = _db(tmp_path)
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, scan_id, content_json, "
               "status) VALUES ('h@x.com','2026-10-14','',?, 'ai_draft')", (json.dumps(HANDOFF),))
    cx.commit()
    pbr.upsert_report(_RacingCx(cx, "remedy_match"), "h@x.com", "2026-10-14", "",
                      CONSOLE_STRIPPED, "confirmed")
    assert _stored(cx, "h@x.com", "2026-10-14") == "remedy_match"


class _FakePgCx:
    """Enough of a Postgres connection for init_table: the column is missing and the
    ALTER fails, as a lock timeout would."""
    backend = "postgres"

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append(sql.split()[0] + " " + sql.split()[1])
        if sql.startswith("ALTER"):
            raise RuntimeError("canceling statement due to lock timeout")
        return _Rows([])

    def rollback(self):
        self.calls.append("ROLLBACK")

    def commit(self):
        self.calls.append("COMMIT")


def test_a_failed_column_add_on_postgres_is_rolled_back():
    cx = _FakePgCx()
    pbr.init_table(cx)
    assert "ROLLBACK" in cx.calls
    assert cx.calls.index("ROLLBACK") > cx.calls.index("ALTER TABLE")


def test_a_failed_column_add_on_sqlite_is_not_rolled_back(monkeypatch):
    # SQLite's failed ALTER leaves the transaction usable; a rollback would drop a
    # caller's pending writes.
    cx = _FakePgCx()
    cx.backend = "sqlite"
    monkeypatch.setattr(pbr._db, "column_exists", lambda c, t, col: False)
    pbr.init_table(cx)
    assert "ALTER TABLE" in cx.calls and "ROLLBACK" not in cx.calls


def test_status_change_keeps_the_kind(tmp_path):
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "a@x.com", "2026-10-14", "", {}, "ai_draft", report_kind="remedy_match")
    pbr.set_report_status(cx, "a@x.com", "2026-10-14", "confirmed")
    assert pbr.latest_report(cx, "a@x.com")["report_kind"] == "remedy_match"


def test_legacy_ingest_writes_biofield(tmp_path):
    from dashboard import legacy_fmp_reports as lfr
    cx = _db(tmp_path)
    lfr.ingest_payload(cx, {"reports": [{"email": "old@x.com", "scan_date": "2019-03-04",
                                          "content": {"greeting": "Aloha", "layers": []}}]},
                       commit=True)
    assert _stored(cx, "old@x.com", "2019-03-04") == "biofield"


# ── The backfill ──────────────────────────────────────────────────────────────

def _seed_unclassified(cx):
    for email, content, sid in (("rm@x.com", SCAN_PIPELINE, "1035975"),
                                ("bf@x.com", PAID_PUBLISH, ""),
                                ("unk@x.com", CONSOLE_STRIPPED, "")):
        cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, scan_id, "
                   "content_json, status) VALUES (?,?,?,?,?)",
                   (email, "2026-10-14", sid, json.dumps(content), "confirmed"))
    cx.execute("INSERT INTO portal_biofield_reports (email, scan_date, scan_id, content_json, "
               "status, report_kind) VALUES ('set@x.com','2026-10-14','',?, 'confirmed', "
               "'biofield')", (json.dumps(SCAN_PIPELINE),))
    cx.commit()


def test_backfill_is_a_dry_run_by_default(tmp_path):
    cx = _db(tmp_path)
    _seed_unclassified(cx)
    out = pbr.backfill_report_kinds(cx)
    assert out["dry_run"] is True and out["written"] == 0
    assert (out["rows_without_kind"], out["remedy_match"], out["biofield"]) == (3, 1, 1)
    assert [u["email"] for u in out["unknown"]] == ["unk@x.com"]
    assert _stored(cx, "rm@x.com", "2026-10-14") is None


def test_backfill_commit_writes_only_null_rows(tmp_path):
    cx = _db(tmp_path)
    _seed_unclassified(cx)
    out = pbr.backfill_report_kinds(cx, dry_run=False)
    assert out["written"] == 2
    assert _stored(cx, "rm@x.com", "2026-10-14") == "remedy_match"
    assert _stored(cx, "bf@x.com", "2026-10-14") == "biofield"
    assert _stored(cx, "unk@x.com", "2026-10-14") is None      # reads as Biofield
    assert _stored(cx, "set@x.com", "2026-10-14") == "biofield"  # never overwritten
    assert pbr.backfill_report_kinds(cx, dry_run=False)["written"] == 0


def test_backfill_never_overwrites_a_kind_set_mid_run(tmp_path, monkeypatch):
    # A writer sets a kind between the backfill's read and its write. The write is
    # compare-and-set on NULL, so the writer's value stands and the row counts as raced.
    cx = _db(tmp_path)
    _seed_unclassified(cx)
    real = pbr.infer_kind

    def _racing(content, scan_id=None):
        cx.execute("UPDATE portal_biofield_reports SET report_kind='biofield' "
                   "WHERE email='rm@x.com'")
        return real(content, scan_id)

    monkeypatch.setattr(pbr, "infer_kind", _racing)
    out = pbr.backfill_report_kinds(cx, dry_run=False)
    assert out["raced"] == 1 and out["written"] == 1
    assert _stored(cx, "rm@x.com", "2026-10-14") == "biofield"


# ── The app: routes and emails ────────────────────────────────────────────────

@pytest.fixture
def app_env(monkeypatch, tmp_path):
    import app as appmod
    from dashboard import inbox as _inbox
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    sent = []
    monkeypatch.setattr(_inbox, "send_bulk",
                        lambda to, subject, body, **k: sent.append((to, subject, body)) or {"via": "x"})
    monkeypatch.setattr(appmod, "_send_inquiry_email",
                        lambda to, subject, body, **k: sent.append((to, subject, body)) or True)
    monkeypatch.setattr(appmod, "_send_full_report_email",
                        lambda to, name, subject, body, **k: sent.append((to, subject, body)) or ("x", None))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod, sent


AUTH = {"X-Console-Key": "test-secret"}


def _cx(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    pbr.init_table(cx)
    return cx


def _portal(appmod, email, name="Gita Wright", content=None):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cp.init_client_portal_table(cx)
        tok, _ = cp.upsert_portal(cx, email, name, content or {})
    return tok


def test_console_confirm_keeps_a_remedy_match(app_env):
    c, appmod, _ = app_env
    r = c.post("/admin/portal/upsert", headers=AUTH, json={
        "email": "g@x.com", "name": "Gita", "scan_date": "2026-10-14", "scan_id": "1035975",
        "content": SCAN_PIPELINE, "report_kind": "remedy_match"})
    assert r.status_code == 200
    r = c.post("/api/console/biofield-portal", headers=AUTH, json={
        "email": "g@x.com", "name": "Gita", "scan_date": "2026-10-14",
        "content": {"greeting": "Aloha Gita", "layers": [{"n": 1, "title": "L", "remedy": "R"}],
                    "report_kind": "biofield"}})
    assert r.status_code == 200, r.data[:300]
    with _cx(appmod) as cx:
        assert _stored(cx, "g@x.com", "2026-10-14") == "remedy_match"
    j = c.get("/api/console/biofield-portal?email=g@x.com", headers=AUTH).get_json()
    assert j["report_kind"] == "remedy_match"
    assert j["scan_kinds"] == {"2026-10-14": "remedy_match"}


def test_paid_handoff_draft_stays_biofield_through_a_console_confirm(app_env, monkeypatch):
    c, appmod, _ = app_env
    from dashboard import biofield_invoice as inv
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def _urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(inv, "_console", lambda: ("https://example.invalid", "k"))
    monkeypatch.setattr(inv.urllib.request, "urlopen", _urlopen)
    inv.default_handoff_push("k@x.com", "Kai", {"greeting": "Aloha Kai", "layers": []},
                             scan_date="2026-10-14")
    assert captured["body"]["report_kind"] == "biofield"
    # The same body, delivered to the real route, then confirmed in the console.
    assert c.post("/admin/portal/upsert", headers=AUTH, json=captured["body"]).status_code == 200
    with _cx(appmod) as cx:
        assert pbr.get_report(cx, "k@x.com", "2026-10-14")["status"] == "ai_draft"
    c.post("/api/console/biofield-portal", headers=AUTH, json={
        "email": "k@x.com", "name": "Kai", "scan_date": "2026-10-14",
        "content": {"greeting": "Aloha Kai", "layers": [{"n": 1, "title": "L", "remedy": "R",
                                                          "patterns": ["ED3"]}]}})
    with _cx(appmod) as cx:
        assert _stored(cx, "k@x.com", "2026-10-14") == "biofield"


def test_animal_route_writes_remedy_match(app_env, monkeypatch):
    c, appmod, _ = app_env
    from dashboard import client_species as cs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cs.init_table(cx)
    monkeypatch.setattr(cs, "get", lambda cx, email: {"is_animal": True})
    content = {"biofield_status": "ai_draft", "layers": [{"n": 1, "title": "Kidney"}],
               "reorder_items": [], "report_kind": "animal_infoceuticals"}
    monkeypatch.setattr(appmod, "_animal_report_content", lambda e, d: (dict(content), []))
    monkeypatch.setattr(appmod, "_run_autoconfirm", lambda *a, **k: "off")
    r = c.post("/api/console/animal-report", headers=AUTH,
               json={"email": "cat@x.com", "scan_date": "2026-10-14", "scan_id": ""})
    assert r.status_code == 200, r.data[:300]
    with _cx(appmod) as cx:
        assert _stored(cx, "cat@x.com", "2026-10-14") == "remedy_match"


def test_paid_publish_payload_carries_biofield():
    from dashboard import biofield_portal_publish as bpp
    from dashboard.biofield_authoring import create_test, add_chain_row
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Kai Test", "kai@example.com", "2026-10-14")
    add_chain_row(cx, f"a{tid}", layer=1, head="ED3", most_affected="Liver",
                  remedy="Vitality", dosage="1", frequency="daily", timing="")
    out = bpp.build_portal_content(cx, f"a{tid}", special_price_cents=0,
                                   catalog={"vitality": {"name": "Vitality"}})
    assert out["report_kind"] == "biofield"


def test_portal_api_returns_the_kind_of_the_report_it_shows(app_env):
    c, appmod, _ = app_env
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "g@x.com", "2026-10-14", "", {"layers": []}, "ai_draft",
                          report_kind="remedy_match")
        pbr.upsert_report(cx, "g@x.com", "2026-09-01", "", {"layers": []}, "confirmed",
                          report_kind="biofield")
    tok = _portal(appmod, "g@x.com")
    assert c.get(f"/api/portal/{tok}").get_json()["report_kind"] == "remedy_match"
    j = c.get(f"/api/portal/{tok}?scan_date=2026-09-01").get_json()
    assert j["report_kind"] == "biofield"


def test_portal_api_reveal_only_is_a_remedy_match(app_env):
    c, appmod, _ = app_env
    from dashboard import biofield_reveals as br
    with sqlite3.connect(appmod.LOG_DB) as cx:
        br.init_table(cx)
        br.upsert(cx, "rv@x.com", "2026-10-14", {"greeting": "Aloha"},
                  [{"name": "Top", "slug": "top", "meaning": "m"}], "src")
    tok = _portal(appmod, "rv@x.com")
    assert c.get(f"/api/portal/{tok}").get_json()["report_kind"] == "remedy_match"


def test_portal_view_block_carries_the_kind(tmp_path):
    from dashboard import portal_view as pv
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "g@x.com", "2026-10-14", "", {"layers": []}, "ai_draft",
                      report_kind="remedy_match")
    pbr.upsert_report(cx, "p@x.com", "2026-10-14", "", {"layers": [{"n": 1}]}, "ai_draft",
                      report_kind="biofield")
    assert pv._biofield_block(cx, "g@x.com")["report_kind"] == "remedy_match"
    assert pv._biofield_block(cx, "p@x.com")["report_kind"] == "biofield"


def test_checklist_counts_only_a_biofield(tmp_path):
    from dashboard import portal_onboarding as ob
    cx = _db(tmp_path)
    pbr.upsert_report(cx, "rm@x.com", "2026-10-14", "", {"layers": []}, "ai_draft",
                      report_kind="remedy_match")
    pbr.upsert_report(cx, "bf@x.com", "2026-10-14", "", {"layers": []}, "confirmed",
                      report_kind="biofield")
    pbr.upsert_report(cx, "unk@x.com", "2026-10-14", "", CONSOLE_STRIPPED, "confirmed")
    assert ob._biofield_completed(cx, "rm@x.com") is False
    assert ob._biofield_completed(cx, "bf@x.com") is True
    assert ob._biofield_completed(cx, "unk@x.com") is True       # unknown reads as Biofield
    assert ob._biofield_completed(cx, "none@x.com") is False


def test_notify_email_for_a_remedy_match(app_env, monkeypatch):
    c, appmod, sent = app_env
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "g@x.com", "Gita Wright")
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "g@x.com", "2026-10-14", "", {}, "confirmed",
                          report_kind="remedy_match")
    assert c.post("/api/console/portal/notify-scan", headers=AUTH,
                  json={"email": "g@x.com"}).get_json()["sent"] is True
    _, subject, body = sent[-1]
    assert subject == "Your new Remedy Match is ready"
    assert "Your newest Remedy Match is ready in your portal." in body
    assert ("It maps the stress patterns your body is working through right now, along "
            "with the gentle remedy schedule matched to where you are today.") in body
    assert "layer" not in body
    assert "Biofield" not in subject + body and "—" not in body


def test_notify_email_for_a_biofield_is_unchanged(app_env, monkeypatch):
    c, appmod, sent = app_env
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "k@x.com", "Kai Lee")
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "k@x.com", "2026-10-14", "", {}, "confirmed",
                          report_kind="biofield")
    c.post("/api/console/portal/notify-scan", headers=AUTH, json={"email": "k@x.com"})
    _, subject, body = sent[-1]
    assert subject == "Your new Biofield Analysis is ready"
    assert "Your newest Biofield Analysis is ready in your portal." in body
    assert ("It maps the layers your body is working through right now, along with the "
            "gentle remedy schedule matched to where you are today.") in body


def test_notify_email_follows_the_current_pointer(app_env, monkeypatch):
    c, appmod, sent = app_env
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "k@x.com", "Kai Lee", {"current_scan_date": "2026-09-01"})
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "k@x.com", "2026-10-14", "", {}, "ai_draft",
                          report_kind="remedy_match")
        pbr.upsert_report(cx, "k@x.com", "2026-09-01", "", {}, "confirmed",
                          report_kind="biofield")
    c.post("/api/console/portal/notify-scan", headers=AUTH, json={"email": "k@x.com"})
    assert sent[-1][1] == "Your new Biofield Analysis is ready"


def test_notify_and_page_agree_when_the_pointer_names_a_reveal_only_date(app_env, monkeypatch):
    # The page does not follow a pointer to a reveal-only date when stored reports
    # exist: it shows the newest stored report. The email must name that same report.
    c, appmod, sent = app_env
    from dashboard import biofield_reveals as br
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    tok = _portal(appmod, "k@x.com", "Kai Lee", {"current_scan_date": "2026-10-14"})
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "k@x.com", "2026-09-01", "", {}, "confirmed",
                          report_kind="biofield")
        br.init_table(cx)
        br.upsert(cx, "k@x.com", "2026-10-14", {"greeting": "Aloha"},
                  [{"name": "Top", "slug": "top", "meaning": "m"}], "src")
    page = c.get(f"/api/portal/{tok}").get_json()
    c.post("/api/console/portal/notify-scan", headers=AUTH, json={"email": "k@x.com"})
    assert (page["scan_date"], page["report_kind"]) == ("2026-09-01", "biofield")
    assert sent[-1][1] == "Your new Biofield Analysis is ready"


def test_notify_and_page_agree_on_a_reveal_only_client(app_env, monkeypatch):
    c, appmod, sent = app_env
    from dashboard import biofield_reveals as br
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    tok = _portal(appmod, "r@x.com", "Rae Lee")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        br.init_table(cx)
        br.upsert(cx, "r@x.com", "2026-10-14", {"greeting": "Aloha"},
                  [{"name": "Top", "slug": "top", "meaning": "m"}], "src")
    assert c.get(f"/api/portal/{tok}").get_json()["report_kind"] == "remedy_match"
    c.post("/api/console/portal/notify-scan", headers=AUTH, json={"email": "r@x.com"})
    assert sent[-1][1] == "Your new Remedy Match is ready"


def test_notify_does_not_send_when_the_kind_lookup_fails(app_env, monkeypatch):
    c, appmod, sent = app_env
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "g@x.com", "Gita Wright")

    def _boom(cx, email):
        raise RuntimeError("db went away")

    monkeypatch.setattr(appmod, "_current_report_kind", _boom)
    r = c.post("/api/console/portal/notify-scan", headers=AUTH, json={"email": "g@x.com"})
    assert r.status_code == 503
    assert r.get_json()["sent"] is False
    assert sent == []


def test_family_notify_for_a_remedy_match(app_env, monkeypatch):
    c, appmod, sent = app_env
    from dashboard import household as hh
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "care@x.com", "Karin Takahashi")
    _portal(appmod, "kid@x.com", "Kai Takahashi")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        hh.init_household_tables(cx)
        hh.add_member(cx, "care@x.com", "kid@x.com", label="Kai", relationship="child")
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "kid@x.com", "2026-10-14", "", {}, "confirmed",
                          report_kind="remedy_match")
        # The caregiver's own report is a Biofield: the member's kind must decide.
        pbr.upsert_report(cx, "care@x.com", "2026-10-14", "", {}, "confirmed",
                          report_kind="biofield")
    r = c.post("/api/console/portal/notify-scan", headers=AUTH,
               json={"email": "care@x.com", "member": "kid@x.com"})
    assert r.get_json()["sent"] is True
    _, subject, body = sent[-1]
    assert subject == "Kai's new Remedy Match is ready"
    assert "Kai's newest Remedy Match is ready in your family portal." in body
    assert ("It maps the stress patterns their body is working through now, with the "
            "gentle remedy schedule matched to where they are today.") in body
    assert "layer" not in body


def test_family_notify_for_a_biofield_keeps_layers(app_env, monkeypatch):
    c, appmod, sent = app_env
    from dashboard import household as hh
    monkeypatch.setenv("PORTAL_SCAN_NOTIFY_ENABLED", "1")
    _portal(appmod, "care@x.com", "Karin Takahashi")
    _portal(appmod, "kid@x.com", "Kai Takahashi")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        hh.init_household_tables(cx)
        hh.add_member(cx, "care@x.com", "kid@x.com", label="Kai", relationship="child")
    with _cx(appmod) as cx:
        pbr.upsert_report(cx, "kid@x.com", "2026-10-14", "", {}, "confirmed",
                          report_kind="biofield")
    c.post("/api/console/portal/notify-scan", headers=AUTH,
           json={"email": "care@x.com", "member": "kid@x.com"})
    _, subject, body = sent[-1]
    assert subject == "Kai's new Biofield Analysis is ready"
    assert "Kai's newest Biofield Analysis is ready in your family portal." in body
    assert ("It maps the layers their body is working through now, with the gentle "
            "remedy schedule matched to where they are today.") in body


def test_reveal_email_wording(app_env):
    c, appmod, sent = app_env
    from dashboard import biofield_reveals as br
    with sqlite3.connect(appmod.LOG_DB) as cx:
        br.init_table(cx)
        rid, _ = br.upsert(cx, "s@x.com", "2026-10-14", {"body": "x"},
                           [{"name": "Top", "slug": "top", "meaning": "m"}], "src")
    assert appmod._send_reveal_link(rid) is True
    to, subject, body = sent[0]
    assert subject == "Your Remedy Match is ready"
    assert body.startswith("Aloha,\n\nYour Remedy Match is ready. View your recommendations here:\n")
    assert "Biofield" not in subject + body


def test_household_notice_wording(app_env, monkeypatch):
    c, appmod, sent = app_env
    from dashboard import household as hh
    monkeypatch.setenv("HOUSEHOLD_VIEW_ENABLED", "1")
    monkeypatch.setenv("HOUSEHOLD_SHARING_ENABLED", "1")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        hh.init_household_tables(cx)
        hh.add_member(cx, "care@x.com", "mem@x.com", "M", "pet")
        cx.commit()
    appmod._household_cc_report("mem@x.com", None)
    assert sent, "no caregiver copy was sent"
    to, subject, body = sent[-1]
    assert to == "care@x.com"
    assert subject == "A new Remedy Match is available for someone in your care"
    assert "biofield" not in (subject + body).lower()


def test_backfill_route_is_dry_run_by_default(app_env):
    c, appmod, _ = app_env
    with _cx(appmod) as cx:
        _seed_unclassified(cx)
    assert c.post("/api/console/portal/backfill-report-kinds", json={}).status_code == 401
    j = c.post("/api/console/portal/backfill-report-kinds", headers=AUTH, json={}).get_json()
    assert j["committed"] is False and j["written"] == 0 and j["remedy_match"] == 1
    with _cx(appmod) as cx:
        assert _stored(cx, "rm@x.com", "2026-10-14") is None
    # Only a literal true commits.
    j = c.post("/api/console/portal/backfill-report-kinds", headers=AUTH,
               json={"commit": "yes"}).get_json()
    assert j["committed"] is False
    j = c.post("/api/console/portal/backfill-report-kinds", headers=AUTH,
               json={"commit": True}).get_json()
    assert j["committed"] is True and j["written"] == 2


# ── The pages ─────────────────────────────────────────────────────────────────

def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _extract_fn(src, name):
    m = re.search(r"\nfunction " + name + r"\(.*?\n}\n", src, re.S)
    assert m, f"{name} not found"
    return m.group(0)


def _run_words(kind_js):
    esc = re.search(r"\nconst esc = [^\n]*", PORTAL).group(0)
    js = (esc + _extract_fn(PORTAL, "reportWords") +
          f"const w = reportWords({kind_js});"
          "console.log(JSON.stringify({remedyMatch: w.remedyMatch, name: w.name,"
          " printTitle: w.printTitle, header: w.header('2026-10-14'),"
          " readyHtml: w.readyHtml('biofield-section'), aiNotice: w.aiNotice}));")
    out = subprocess.run([_node(), "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_portal_words_for_a_remedy_match():
    w = _run_words('"remedy_match"')
    assert w["header"] == "Remedy Match, 2026-10-14"
    assert w["printTitle"] == "Remedy Match"
    assert w["aiNotice"] == "Generated by Dr. Glen's knowledgebase."
    assert w["readyHtml"].startswith("See which remedies match to your scan. Your Remedy Match is <a ")
    assert ">here</a>." in w["readyHtml"]
    assert "scrollToReportPart('biofield-section')" in w["readyHtml"]
    assert "—" not in json.dumps(w, ensure_ascii=False)


@pytest.mark.parametrize("kind_js", ['"biofield"', "undefined", '""', "null"])
def test_portal_words_for_a_biofield_are_unchanged(kind_js):
    w = _run_words(kind_js)
    assert w["header"] == "Biofield report — 2026-10-14"
    assert w["printTitle"] == "Biofield Analysis"
    assert w["aiNotice"] == ""
    assert w["readyHtml"] == ("Your Biofield Analysis is ready: your healing path, mapped "
                              "from the surface layer down to the deepest root.")


def test_render_test_passes():
    # The rendered-page checks live in tests/test_remedy_match_label_render.js, which
    # CI's node runner also picks up. Run it here too so this file alone covers it.
    out = subprocess.run([_node(), str(ROOT / "tests" / "test_remedy_match_label_render.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "test_remedy_match_label_render: ok" in out.stdout


def test_console_shows_the_kind_beside_the_date():
    m = re.search(r"function kindLabel\(k\)\{[^\n]*\}", CONSOLE)
    assert m, "kindLabel not found"
    js = (m.group(0) + "\nconsole.log(kindLabel('remedy_match') + '|' + kindLabel('biofield') + '|' + kindLabel(undefined));")
    out = subprocess.run([_node(), "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "Remedy Match|Biofield|Biofield"
    assert "label.textContent = d + ' · ' + kindLabel(SCAN_KINDS[d]);" in CONSOLE
    # Publish sends no kind, so it can never change one.
    pub = CONSOLE[CONSOLE.index("async function publish(send)"):]
    pub = pub[:pub.index("\n}\n")]
    assert "report_kind" not in pub


def test_reveal_page_says_remedy_match():
    assert "<title>Your Remedy Match - Dr. Glen Swartwout</title>" in REVEAL_PAGE
    assert '"Your Remedy Match is ready."' in REVEAL_PAGE
    # Glen, 2026-10-20: "Reserve 'layers' language for causal Biofield Analysis."
    assert 'layersLabel.textContent = "Your Active Stress Patterns";' in REVEAL_PAGE
    assert "Stress Pattern Layers" not in REVEAL_PAGE
    # Glen's own wording, 2026-10-20.
    assert ('tosP.textContent = "To view your personal Remedy Match, please agree to our '
            'terms of service.";') in REVEAL_PAGE


def test_console_video_label_by_kind():
    assert ("(CUR_KIND === 'remedy_match' ? 'Watch your Remedy Match walkthrough' : "
            "'Watch your Biofield walkthrough')") in CONSOLE
    assert "Biofield Analysis" not in REVEAL_PAGE
