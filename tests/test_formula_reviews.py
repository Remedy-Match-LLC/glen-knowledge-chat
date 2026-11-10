"""Formula review dates (Glen, 2026-11-09, to-do #66).

One row per product and step. The console page lists every product that is not
inactive: reviewed ones oldest review first, never-reviewed ones in their own group.
The API refuses without the console key and adds a row with it. The seed is idempotent,
and the vault backfill maps folders to slugs through the app's existing resolver.
"""
import json
import sqlite3

import pytest

from dashboard import formula_reviews as FR

CAT = {
    "liver-support": {"name": "Liver Support"},
    "mitochondrial-biogenesis": {"name": "Mitochondrial Biogenesis"},
    "blood-cleanse": {"name": "Blood Cleanse"},
    "alkalize-bicarbonate-blend": {"name": "Alkalize Bicarbonate Blend"},
    "aces-eye-drops": {"name": "ACES Eye Drops"},
    "old-liver": {"name": "Old Liver", "inactive": True, "superseded_by": "liver-support"},
}


@pytest.fixture()
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "chat_log.db"))
    FR.init_tables(c)
    yield c
    c.close()


# ── table helpers ───────────────────────────────────────────────────────────────

def test_add_and_list(cx):
    r = FR.add_event(cx, "liver-support", "review", "2026-11-09",
                     link="/x/review.html", note="first", who="t", catalog=CAT)
    assert r["created"] is True and isinstance(r["id"], int)
    rows = FR.list_events(cx, "liver-support")
    assert len(rows) == 1
    assert rows[0]["step"] == "review" and rows[0]["link"] == "/x/review.html"
    assert rows[0]["note"] == "first" and rows[0]["created_at"]


def test_repeat_is_stored_once(cx):
    a = FR.add_event(cx, "liver-support", "decision", "2026-11-09", value="waiting", catalog=CAT)
    b = FR.add_event(cx, "liver-support", "decision", "2026-11-09", value="waiting", catalog=CAT)
    assert a["created"] and not b["created"] and a["id"] == b["id"]
    c = FR.add_event(cx, "liver-support", "decision", "2026-11-09", value="approved", catalog=CAT)
    assert c["created"]                       # a changed answer is a new row
    assert len(FR.list_events(cx)) == 2


@pytest.mark.parametrize("args, msg", [
    (("nope", "review", "2026-11-09", None), "No product"),
    (("liver-support", "tasting", "2026-11-09", None), "step must be"),
    (("liver-support", "review", "9 Nov 2026", None), "YYYY-MM-DD"),
    (("liver-support", "review", "2026-02-30", None), "not a real date"),
    (("liver-support", "decision", "2026-11-09", None), "decision must be"),
    (("liver-support", "decision", "2026-11-09", "maybe"), "decision must be"),
    (("liver-support", "review", "2026-11-09", "approved"), "Only the decision"),
])
def test_bad_rows_are_refused(cx, args, msg):
    with pytest.raises(ValueError, match=msg):
        FR.add_event(cx, *args[:3], value=args[3], catalog=CAT)
    assert FR.list_events(cx) == []


def test_overview_orders_oldest_first_and_groups_never_reviewed(cx):
    FR.add_event(cx, "liver-support", "review", "2026-11-09", catalog=CAT)
    FR.add_event(cx, "liver-support", "decision", "2026-11-09", value="waiting", catalog=CAT)
    FR.add_event(cx, "blood-cleanse", "review", "2026-10-31", catalog=CAT)
    FR.add_event(cx, "blood-cleanse", "review", "2025-01-01", catalog=CAT)   # older, not latest
    FR.add_event(cx, "blood-cleanse", "decision", "2026-11-02",
                 value="approved with changes", catalog=CAT)
    FR.add_event(cx, "aces-eye-drops", "review", "2026-08-01", catalog=CAT)
    FR.add_event(cx, "aces-eye-drops", "label_printed", "2026-09-01", catalog=CAT)
    FR.add_event(cx, "old-liver", "review", "2020-01-01", catalog=CAT)       # inactive: hidden
    d = FR.overview(cx, catalog=CAT)
    assert [x["slug"] for x in d["reviewed"]] == ["aces-eye-drops", "blood-cleanse",
                                                  "liver-support"]
    bc = d["reviewed"][1]
    assert bc["last_review"] == "2026-10-31" and bc["decision"] == "approved with changes"
    assert bc["steps"]["decision"] == "2026-11-02" and bc["steps"]["label_printed"] is None
    assert d["reviewed"][0]["steps"]["label_printed"] == "2026-09-01"
    assert [x["slug"] for x in d["never_reviewed"]] == ["alkalize-bicarbonate-blend",
                                                        "mitochondrial-biogenesis"]
    assert all(x["last_review"] is None for x in d["never_reviewed"])
    assert [s["key"] for s in d["steps"]] == list(FR.STEP_KEYS)


# ── console page and API ────────────────────────────────────────────────────────

KEY = "test-console-secret"


@pytest.fixture()
def console(monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "console.db"))
    monkeypatch.setattr(app, "CONSOLE_SECRET", KEY)
    monkeypatch.setattr(FR, "_CATALOG_FN", lambda: CAT)
    return app.app.test_client()


def test_api_refuses_without_the_key(console):
    assert console.get("/api/console/formula-reviews").status_code == 401
    assert console.get("/api/console/formula-reviews/overview").status_code == 401
    r = console.post("/api/console/formula-reviews",
                     json={"slug": "liver-support", "step": "review", "date": "2026-11-09"})
    assert r.status_code == 401
    assert console.post("/api/console/formula-reviews", headers={"X-Console-Key": "wrong"},
                        json={"slug": "liver-support", "step": "review",
                              "date": "2026-11-09"}).status_code == 401
    h = {"X-Console-Key": KEY}
    assert console.get("/api/console/formula-reviews", headers=h).get_json()["rows"] == []


def test_api_adds_a_row_with_the_key(console):
    h = {"X-Console-Key": KEY}
    body = {"slug": "liver-support", "step": "review", "date": "2026-11-09",
            "link": "/r.html", "note": "analyzer"}
    r = console.post("/api/console/formula-reviews", headers=h, json=body)
    assert r.status_code == 201 and r.get_json()["created"] is True
    again = console.post("/api/console/formula-reviews", headers=h, json=body)
    assert again.status_code == 200 and again.get_json()["created"] is False
    bad = console.post("/api/console/formula-reviews", headers=h,
                       json={"slug": "liver-support", "step": "decision", "date": "2026-11-09"})
    assert bad.status_code == 400 and "decision must be" in bad.get_json()["error"]
    rows = console.get("/api/console/formula-reviews?slug=liver-support",
                       headers=h).get_json()["rows"]
    assert len(rows) == 1 and rows[0]["note"] == "analyzer" and rows[0]["who"] == "console"
    d = console.get("/api/console/formula-reviews/overview", headers=h).get_json()
    assert [x["slug"] for x in d["reviewed"]] == ["liver-support"]
    assert "mitochondrial-biogenesis" in [x["slug"] for x in d["never_reviewed"]]


def test_page_is_served_with_the_shared_nav(console):
    r = console.get("/console/formula-reviews")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/static/op-nav.js" in html
    assert "/api/console/formula-reviews/overview" in html
    assert "never reviewed" in html.lower()


# ── seed and backfill ───────────────────────────────────────────────────────────

def test_starting_rows_resolve_in_the_real_catalog():
    from scripts import seed_formula_reviews as S
    real = S.load_catalog()
    for e in S.STARTING_ROWS:
        FR.validate(e["slug"], e["step"], e["date"], e.get("value"), catalog=real)
        assert not real[e["slug"]].get("inactive")
    assert real["liver-support"]["name"] == "Liver Support"
    assert real["mitochondrial-biogenesis"]["name"] == "Mitochondrial Biogenesis"


BACKFILL = {"rows": [
    {"folder": "blood-cleanse", "fmp_name": "Blood Cleanse", "steps": {
        "review": {"date": "2026-10-31", "basis": "folder date",
                   "file": "production/05 Formulations/blood-cleanse/2026-10-31/x.html",
                   "line": "x.html"},
        "decision": {"date": "2026-11-02", "outcome": "approved with changes",
                     "basis": "hand-read", "file": "formulation/STATE.md", "line": "yes"},
        "app": "unknown", "filemaker": "unknown", "label_printed": "unknown",
        "store_page": "unknown"}},
    {"folder": "alkalize", "fmp_name": "unknown", "steps": {      # by name, not slug
        "review": {"date": "2026-10-12", "basis": "folder date", "file": "a.html", "line": ""},
        "decision": "unknown", "app": "unknown", "filemaker": "unknown",
        "label_printed": {"date": "2026-10-20", "basis": "label file", "file": "l.pdf",
                          "line": ""},
        "store_page": "unknown"}},
    {"folder": "old-liver", "fmp_name": "unknown", "steps": {     # retired: its live twin
        "review": {"date": "2026-01-05", "basis": "folder date", "file": "o.md", "line": ""},
        "decision": "unknown", "app": "unknown", "filemaker": "unknown",
        "label_printed": "unknown", "store_page": "unknown"}},
    {"folder": "snake-and-dragon", "fmp_name": "unknown", "steps": {
        "review": {"date": "2026-03-01", "basis": "folder date", "file": "s.md", "line": ""},
        "decision": "unknown", "app": "unknown", "filemaker": "unknown",
        "label_printed": "unknown", "store_page": "unknown"}},
    {"folder": "aces-eye-drops", "fmp_name": "unknown", "steps": {k: "unknown" for k in
        ("review", "decision", "app", "filemaker", "label_printed", "store_page")}},
]}


def test_backfill_maps_folders_and_loads_only_dated_steps(cx):
    from scripts import seed_formula_reviews as S
    res = S.load_backfill(BACKFILL, CAT)
    assert {m["folder"]: m["slug"] for m in res["mapped"]} == {
        "blood-cleanse": "blood-cleanse", "alkalize": "alkalize-bicarbonate-blend",
        "old-liver": "liver-support", "aces-eye-drops": "aces-eye-drops"}
    assert res["unmatched"] == [{"folder": "snake-and-dragon", "dated_steps": 1}]
    got = sorted((e["slug"], e["step"], e["date"], e["value"], e["note"]) for e in res["rows"])
    assert got == [
        ("alkalize-bicarbonate-blend", "label_printed", "2026-10-20", None, "label file"),
        ("alkalize-bicarbonate-blend", "review", "2026-10-12", None, "folder date"),
        ("blood-cleanse", "decision", "2026-11-02", "approved with changes", "hand-read"),
        ("blood-cleanse", "review", "2026-10-31", None, "folder date"),
        ("liver-support", "review", "2026-01-05", None, "folder date"),
    ]


def test_seed_is_idempotent_and_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    from scripts import seed_formula_reviews as S
    monkeypatch.setattr(S, "load_catalog", lambda: CAT)
    src = tmp_path / "backfill.json"
    src.write_text(json.dumps(BACKFILL))
    out = tmp_path / "unmatched.txt"
    db_path = tmp_path / "seed.db"
    base = ["--backfill", str(src), "--unmatched-out", str(out)]
    assert S.main(base) == 0
    printed = capsys.readouterr().out
    assert "mapped:   4" in printed and "unmatched: 1" in printed
    assert "step rows to insert: 5" in printed
    assert not db_path.exists()
    assert out.read_text().startswith("snake-and-dragon\t")
    S.main(base + ["--apply", "--db", str(db_path)])
    assert "Added 9, already there 0." in capsys.readouterr().out    # 4 starting + 5
    S.main(base + ["--apply", "--db", str(db_path)])
    assert "Added 0, already there 9." in capsys.readouterr().out
