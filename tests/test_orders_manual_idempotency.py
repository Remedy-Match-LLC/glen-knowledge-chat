"""A double-submit of the order form must make one order, not two.

Money found six same-email same-total pairs created seconds apart, from 2026-07-08 to
2026-09-19, each cleaned up by hand. 190/191 were 0 seconds apart.

`orders` already carries UNIQUE(source, external_ref) and `upsert_order` is already
idempotent on that pair. `/api/orders/manual` minted `INH-` + uuid4 on every request,
so every click produced a new key and the index it would have hit never saw a repeat.
Two other routes already do this right: FFINV-{email}-{scan_date} at app.py and
SPINV-{email}-{key}.

A "was there a twin in the last N seconds?" lookup was considered and rejected: it is a
read followed by a write, so two requests one second apart can both read "no twin" and
both insert. Four of the six pairs sit inside that gap. The caller mints one token per
FORM LOAD instead, both clicks carry it, and the database enforces the rest.
"""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

BOTTLE = {"slug": "mix", "price_cents": 7000, "name": "Drink Mix", "bottle_type": "120 g"}
_CAT = {"mix": BOTTLE}


def _app():
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app not importable: {e}")


@pytest.fixture
def env(monkeypatch, tmp_path):
    appmod = _app()
    db = str(tmp_path / "m.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_get_product", _CAT.get)
    appmod._init_people_table()
    from dashboard import orders as O, db as D
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        # prod has it; init_orders_table does not add it (see
        # tests/test_replacement_order_takes_the_old_ones_place.py). replace_open reads it.
        if not D.column_exists(cx, "orders", "portal_published"):
            cx.execute("ALTER TABLE orders ADD COLUMN portal_published INTEGER DEFAULT 0")
        cx.commit()
    # Auth via the actor, not the header: the console-secret globals are mutated by
    # neighbouring test files (see tests/test_orders_manual_pickup_pref.py).
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: type("A", (), {"role": appmod._bos_rbac.OWNER})())
    monkeypatch.setattr(appmod._shipping, "_default_db_path", lambda: db)
    with sqlite3.connect(db) as scx:
        appmod._shipping.init_shipping_schema(scx)
    return appmod, db


def _post(appmod, email, *, key=None, qty=2, extra=None):
    body = {"customer": {"name": "T", "email": email,
                         "address": {"address1": "1", "city": "Hilo", "state": "HI",
                                     "zip": "96720", "country": "US"}},
            "lines": [{"slug": "mix", "qty": qty}], "method": "Zelle"}
    if key is not None:
        body["idempotency_key"] = key
    body.update(extra or {})
    r = appmod.app.test_client().post("/api/orders/manual", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _order_count(db):
    with sqlite3.connect(db) as cx:
        return cx.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def test_two_clicks_carrying_one_token_make_one_order(env):
    """The defect, stated as a test. Alyssa's 196/197 were one second apart."""
    appmod, db = env
    first = _post(appmod, "alyssa@x.com", key="form-load-1")
    second = _post(appmod, "alyssa@x.com", key="form-load-1")
    assert second["order_id"] == first["order_id"]
    assert second["external_ref"] == first["external_ref"]
    assert _order_count(db) == 1


def test_the_reference_is_derived_from_the_token_not_minted_fresh(env):
    """Same token, same external_ref. That is what UNIQUE(source, external_ref) needs."""
    appmod, db = env
    a = _post(appmod, "ref@x.com", key="form-load-1")
    with sqlite3.connect(db) as cx:
        cx.execute("DELETE FROM orders")
        cx.commit()
    b = _post(appmod, "ref@x.com", key="form-load-1")
    assert a["external_ref"] == b["external_ref"]


def test_a_second_form_load_can_still_raise_a_second_order(env):
    """A genuine repeat order means a new page load, so a new token. Nothing is blocked."""
    appmod, db = env
    first = _post(appmod, "repeat@x.com", key="form-load-1")
    second = _post(appmod, "repeat@x.com", key="form-load-2")
    assert second["order_id"] != first["order_id"]
    assert _order_count(db) == 2


def test_one_token_reaching_two_clients_does_not_merge_them(env):
    """The address is part of the key, so a token leaking between tabs cannot put one
    client's lines onto another client's order."""
    appmod, db = env
    a = _post(appmod, "one@x.com", key="shared")
    b = _post(appmod, "two@x.com", key="shared")
    assert a["order_id"] != b["order_id"]
    assert _order_count(db) == 2


def test_a_caller_that_sends_no_token_keeps_todays_behaviour(env):
    """Backwards compatible on purpose: the internal caregiver post sends none, and the
    local Biofield app's page does not mint one yet. Those callers must keep working."""
    appmod, db = env
    first = _post(appmod, "notoken@x.com")
    second = _post(appmod, "notoken@x.com")
    assert first["order_id"] != second["order_id"]
    assert _order_count(db) == 2


def test_a_blank_token_is_treated_as_no_token(env):
    """A form that posts an empty string must not collapse every order onto one key."""
    appmod, db = env
    first = _post(appmod, "blank@x.com", key="")
    second = _post(appmod, "blank@x.com", key="   ")
    assert first["order_id"] != second["order_id"]
    assert _order_count(db) == 2


def test_a_repeat_submit_does_not_cancel_the_order_it_is_repeating(env):
    """The Biofield hand-off posts replace_open, which cancels the client's prior OPEN
    drafts before inserting. Not inserting twice is therefore only half the fix: without
    an early return the second click cancels the order the first click just made, then
    updates that cancelled row and hands back an order nobody can pay."""
    appmod, db = env
    first = _post(appmod, "handoff@x.com", key="form-load-1", extra={"replace_open": True})
    second = _post(appmod, "handoff@x.com", key="form-load-1", extra={"replace_open": True})
    assert second["order_id"] == first["order_id"]
    assert _order_count(db) == 1
    with sqlite3.connect(db) as cx:
        status = cx.execute("SELECT status FROM orders WHERE id=?",
                            (first["order_id"],)).fetchone()[0]
    assert status == "proposed", f"the repeat cancelled the first order: {status}"
