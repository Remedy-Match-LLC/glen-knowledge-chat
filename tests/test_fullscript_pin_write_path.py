# tests/test_fullscript_pin_write_path.py
"""Pins can finally be created. Until 2026-08-28 they could not.

The table, the reader (`pins_for_client`) and the ranking that puts `pinned`
above every other driver all shipped 2026-07-23. The WRITE path never did —
every INSERT into fullscript_client_pins existed only in tests. So in production
`pins_for_client` returned [] for every client, forever, while the code
described a pin as "an explicit clinical decision, so it outranks anything
derived". A dead branch in a live feature (FULLSCRIPT_ENABLED=1 in prd).
"""
import sqlite3

import pytest

from dashboard import fullscript as fs


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    fs.init_tables(c)
    c.execute("INSERT INTO fullscript_products (name, active) VALUES ('Magnesium Glycinate', 1)")
    c.execute("INSERT INTO fullscript_products (name, active) VALUES ('Retired Thing', 0)")
    c.commit()
    return c


def test_a_pin_can_be_created_and_is_then_visible(cx):
    ok, reason = fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "for sleep", "glen")
    assert (ok, reason) == (True, "pinned")
    names = [r["name"] for r in fs.pins_for_client(cx, "a@x.com")]
    assert names == ["Magnesium Glycinate"], "the pin did not reach the reader"


def test_an_unknown_product_is_refused_not_silently_stored(cx):
    """pins_for_client JOINs active=1. Storing an unmatchable name would look
    like success and show nothing — the exact failure this path exists to end."""
    ok, reason = fs.pin_product(cx, "a@x.com", "Not A Real Product", "", "glen")
    assert ok is False and "unknown" in reason
    assert cx.execute("SELECT COUNT(*) FROM fullscript_client_pins").fetchone()[0] == 0


def test_an_inactive_product_is_refused(cx):
    ok, reason = fs.pin_product(cx, "a@x.com", "Retired Thing", "", "glen")
    assert ok is False
    assert cx.execute("SELECT COUNT(*) FROM fullscript_client_pins").fetchone()[0] == 0


def test_pinning_twice_updates_rather_than_duplicating(cx):
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "first", "glen")
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "second", "rae")
    rows = cx.execute("SELECT note, pinned_by FROM fullscript_client_pins").fetchall()
    assert len(rows) == 1, f"{len(rows)} rows for one (client, product)"
    assert rows[0]["note"] == "second" and rows[0]["pinned_by"] == "rae"


def test_unpin_removes_it(cx):
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "", "glen")
    assert fs.unpin_product(cx, "a@x.com", "Magnesium Glycinate") == 1
    assert fs.pins_for_client(cx, "a@x.com") == []


def test_unpinning_something_unpinned_is_zero_not_an_error(cx):
    assert fs.unpin_product(cx, "a@x.com", "Magnesium Glycinate") == 0


def test_pins_are_per_client(cx):
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "", "glen")
    assert fs.pins_for_client(cx, "b@x.com") == []


def test_email_case_does_not_create_a_second_pin(cx):
    fs.pin_product(cx, "A@X.com", "Magnesium Glycinate", "", "glen")
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "", "glen")
    assert cx.execute("SELECT COUNT(*) FROM fullscript_client_pins").fetchone()[0] == 1


def test_pins_raw_still_shows_a_pin_whose_product_went_inactive(cx):
    """pins_for_client hides it (active=1). Without pins_raw an operator sees the
    pin vanish and cannot tell whether it was deleted or merely stopped matching."""
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "note", "glen")
    cx.execute("UPDATE fullscript_products SET active = 0 WHERE name = 'Magnesium Glycinate'")
    cx.commit()
    assert fs.pins_for_client(cx, "a@x.com") == []
    raw = fs.pins_raw(cx, "a@x.com")
    assert len(raw) == 1 and raw[0]["product_active"] == 0


def test_a_pin_outranks_the_other_drivers(cx):
    """The whole point of the feature: ORIGIN_PRIORITY put `pinned` first, but
    nothing could ever be pinned. Now that it can, it must actually win."""
    assert fs.ORIGIN_PRIORITY["pinned"] < min(
        v for k, v in fs.ORIGIN_PRIORITY.items() if k != "pinned")
    fs.pin_product(cx, "a@x.com", "Magnesium Glycinate", "", "glen")
    cands = fs.candidates_for(cx, "a@x.com")
    pinned = [c for c in cands if c.get("origin") == "pinned"]
    assert pinned and pinned[0]["name"] == "Magnesium Glycinate"


# ---------------------------------------------------------------------------
# the operator endpoint
# ---------------------------------------------------------------------------
# Module-level tests do not prove the route is wired: it can validate, auth and
# report independently of the functions above. (Learned the hard way on the
# scan-tag work, where a "tag every row" mutant survived until a route test.)

def _client(monkeypatch, tmp_path):
    import importlib, pathlib, sys
    repo = pathlib.Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        app = importlib.import_module("app")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"app not importable: {e}")
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    c = sqlite3.connect(app.LOG_DB)
    fs.init_tables(c)
    c.execute("INSERT INTO fullscript_products (name, active) VALUES ('Magnesium Glycinate', 1)")
    c.commit(); c.close()
    app.app.config["TESTING"] = True
    return app, app.app.test_client()


def test_the_endpoint_rejects_a_non_owner(monkeypatch, tmp_path):
    app, c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_bos_actor", lambda: None)
    r = c.post("/api/console/fullscript-pins",
               json={"email": "a@x.com", "fs_product_name": "Magnesium Glycinate"})
    assert r.status_code == 401


def test_the_endpoint_pins_and_reports_skipped(monkeypatch, tmp_path):
    """A bad product name must come back in `skipped`, not vanish into an ok."""
    app, c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_bos_actor",
                        lambda: type("A", (), {"role": app._bos_rbac.OWNER, "user_name": "glen"})())
    r = c.post("/api/console/fullscript-pins", json={
        "email": "a@x.com",
        "products": [{"fs_product_name": "Magnesium Glycinate", "note": "sleep"},
                     {"fs_product_name": "Does Not Exist"}]})
    assert r.status_code == 200, r.data[:200]
    d = r.get_json()
    assert [p["fs_product_name"] for p in d["pinned"]] == ["Magnesium Glycinate"]
    assert [p["fs_product_name"] for p in d["skipped"]] == ["Does Not Exist"]
    assert len(d["pins"]) == 1


def test_the_endpoint_unpins(monkeypatch, tmp_path):
    app, c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_bos_actor",
                        lambda: type("A", (), {"role": app._bos_rbac.OWNER, "user_name": "glen"})())
    c.post("/api/console/fullscript-pins",
           json={"email": "a@x.com", "fs_product_name": "Magnesium Glycinate"})
    r = c.delete("/api/console/fullscript-pins",
                 json={"email": "a@x.com", "fs_product_name": "Magnesium Glycinate"})
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    assert r.get_json()["pins"] == []
