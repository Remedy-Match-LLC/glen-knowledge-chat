"""The console action that invokes the client health-erasure engine.

Glen approved the flow on 2026-09-20: preview first, type the email to confirm, dark
behind CLIENT_ERASURE_ENABLED, and every run leaves a record of what it removed.

The engine itself is covered by tests/test_client_erasure.py. These tests cover the
route: who may call it, what it refuses, and what it must not touch.
"""
import importlib
import sqlite3

KEY = "test-console-secret"
VICTIM = "victim@x.com"
OTHER = "other@x.com"


def _seed(path):
    """Idempotent: a test that reloads the app twice against one tmp_path must not
    end up with two of every row."""
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS intake_responses (email TEXT, answers_json TEXT)")
        cx.execute("CREATE TABLE IF NOT EXISTS scan_analyses (email TEXT, data TEXT)")
        cx.execute("CREATE TABLE IF NOT EXISTS purchase_history (email TEXT, slug TEXT)")
        for t in ("intake_responses", "scan_analyses", "purchase_history"):
            cx.execute(f"DELETE FROM {t}")
        cx.execute("INSERT INTO intake_responses (email, answers_json) VALUES (?, '{}')", (VICTIM,))
        cx.execute("INSERT INTO scan_analyses (email, data) VALUES (?, 'x')", (VICTIM,))
        cx.execute("INSERT INTO intake_responses (email, answers_json) VALUES (?, '{}')", (OTHER,))
        cx.execute("INSERT INTO purchase_history (email, slug) VALUES (?, 'ocuflows')", (VICTIM,))
        cx.commit()


def _reload(monkeypatch, tmp_path, *, enabled=True, secret=KEY):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if secret is None:
        monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    else:
        monkeypatch.setenv("CONSOLE_SECRET", secret)
    monkeypatch.setenv("CLIENT_ERASURE_ENABLED", "1" if enabled else "")
    # dashboard/__init__.py reads CONSOLE_SECRET once, at the package's FIRST import,
    # and reloading app does not re-run it. _bos_actor compares against that copy, so
    # without this line the secret this test sets is ignored whenever another test file
    # imported `dashboard` first. In production both are read from the same env at
    # startup, so this keeps the reload honest rather than papering over a real gap.
    import dashboard as _dash
    monkeypatch.setattr(_dash, "CONSOLE_SECRET", secret or "")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    _seed(appmod.LOG_DB)
    return appmod


def _count(path, table, email):
    with sqlite3.connect(path) as cx:
        return cx.execute(f"SELECT COUNT(*) FROM {table} WHERE LOWER(email)=?",
                          (email,)).fetchone()[0]


def _post(appmod, path, body, *, key=KEY):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Console-Key"] = key
    return appmod.app.test_client().post(path, json=body, headers=headers)


# --- who may call it ---------------------------------------------------------------

def test_preview_is_unauthorized_without_the_console_key(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    assert _post(appmod, "/api/console/client-erasure/preview",
                 {"email": VICTIM}, key=None).status_code == 401


def test_erase_is_unauthorized_without_the_console_key(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    r = _post(appmod, "/api/console/client-erasure/erase",
              {"email": VICTIM, "confirm": VICTIM}, key=None)
    assert r.status_code == 401
    assert _count(appmod.LOG_DB, "intake_responses", VICTIM) == 1, "nothing may be deleted"


def test_an_unset_console_secret_refuses_rather_than_opening_the_gate(monkeypatch, tmp_path):
    # The `!= CONSOLE_SECRET` idiom used elsewhere passes when both are empty. This gate
    # must not: an absent secret has to close the gate, not open it.
    appmod = _reload(monkeypatch, tmp_path, secret=None)
    assert _post(appmod, "/api/console/client-erasure/preview",
                 {"email": VICTIM}, key=None).status_code == 401
    assert _post(appmod, "/api/console/client-erasure/preview",
                 {"email": VICTIM}, key="").status_code == 401


def test_a_key_in_the_query_string_is_not_enough(monkeypatch, tmp_path):
    # Every other console route accepts ?key=, which writes the master secret into
    # Render's access log. This one is an irreversible delete, so the header is the
    # only way in. The key below is VALID; it is the placement that is refused.
    appmod = _reload(monkeypatch, tmp_path)
    c = appmod.app.test_client()
    r = c.post(f"/api/console/client-erasure/preview?key={KEY}", json={"email": VICTIM})
    assert r.status_code == 401
    r2 = c.post(f"/api/console/client-erasure/erase?key={KEY}",
                json={"email": VICTIM, "confirm": VICTIM})
    assert r2.status_code == 401
    assert _count(appmod.LOG_DB, "intake_responses", VICTIM) == 1, "nothing may be deleted"


def test_the_routes_are_dark_until_the_flag_is_on(monkeypatch, tmp_path):
    # A 404 is also what an ABSENT route returns, so this test would pass against a
    # branch where nothing was built. The flag-on half is what makes the 404 mean dark.
    on = _reload(monkeypatch, tmp_path, enabled=True)
    assert _post(on, "/api/console/client-erasure/preview",
                 {"email": VICTIM}).status_code == 200, "the route must exist"
    off = _reload(monkeypatch, tmp_path, enabled=False)
    assert _post(off, "/api/console/client-erasure/preview",
                 {"email": VICTIM}).status_code == 404
    r = _post(off, "/api/console/client-erasure/erase", {"email": VICTIM, "confirm": VICTIM})
    assert r.status_code == 404
    assert _count(off.LOG_DB, "intake_responses", VICTIM) == 1, "nothing may be deleted"


# --- preview -----------------------------------------------------------------------

def test_preview_counts_the_rows_and_deletes_nothing(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    d = _post(appmod, "/api/console/client-erasure/preview", {"email": VICTIM}).get_json()
    assert d["ok"] is True
    assert d["tables"]["intake_responses"] == 1
    assert d["tables"]["scan_analyses"] == 1
    assert d["total"] == 2
    assert "purchase_history" not in d["tables"], "money is not health data"
    assert _count(appmod.LOG_DB, "intake_responses", VICTIM) == 1, "preview must not delete"


def test_preview_reports_whether_the_address_is_still_emailable(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    d = _post(appmod, "/api/console/client-erasure/preview", {"email": VICTIM}).get_json()
    assert d["suppressed"] is False
    from dashboard import email_suppression as ES
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ES.init_table(cx)
        ES.add(cx, VICTIM, "optout", "asked to stop", "test")
    d2 = _post(appmod, "/api/console/client-erasure/preview", {"email": VICTIM}).get_json()
    assert d2["suppressed"] is True


# --- erasing -----------------------------------------------------------------------

def test_erase_refuses_when_the_typed_email_does_not_match(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    r = _post(appmod, "/api/console/client-erasure/erase",
              {"email": VICTIM, "confirm": OTHER})
    assert r.status_code == 400
    assert _count(appmod.LOG_DB, "intake_responses", VICTIM) == 1
    assert _count(appmod.LOG_DB, "intake_responses", OTHER) == 1, "and not the typed one either"


def test_erase_removes_the_health_rows_and_leaves_money_alone(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    d = _post(appmod, "/api/console/client-erasure/erase",
              {"email": VICTIM, "confirm": VICTIM}).get_json()
    assert d["ok"] is True and d["total"] == 2
    assert d["removed"]["intake_responses"] == 1
    assert _count(appmod.LOG_DB, "intake_responses", VICTIM) == 0
    assert _count(appmod.LOG_DB, "scan_analyses", VICTIM) == 0
    assert _count(appmod.LOG_DB, "purchase_history", VICTIM) == 1, "money must survive"
    assert _count(appmod.LOG_DB, "intake_responses", OTHER) == 1, "only this client"


def test_erase_leaves_a_record_of_what_it_removed(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    _post(appmod, "/api/console/client-erasure/erase",
          {"email": VICTIM, "confirm": VICTIM, "note": "asked by email"})
    from dashboard import client_erasure as CE
    with sqlite3.connect(appmod.LOG_DB) as cx:
        rows = CE.erasures_for(cx, VICTIM)
    assert len(rows) == 1
    assert rows[0]["total_rows"] == 2
    assert rows[0]["note"] == "asked by email"
    assert rows[0]["removed"]["scan_analyses"] == 1


def test_erase_does_not_stop_the_email_unless_asked(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    r = _post(appmod, "/api/console/client-erasure/erase", {"email": VICTIM, "confirm": VICTIM})
    assert r.status_code == 200 and r.get_json()["ok"] is True, "the erasure must have run"
    assert r.get_json()["suppressed"] is False
    from dashboard import email_suppression as ES
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ES.init_table(cx)
        assert ES.is_suppressed(cx, VICTIM) is False


def test_erase_can_also_stop_the_email_when_asked(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    d = _post(appmod, "/api/console/client-erasure/erase",
              {"email": VICTIM, "confirm": VICTIM, "also_suppress": True}).get_json()
    assert d["suppressed"] is True
    from dashboard import email_suppression as ES, client_erasure as CE
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ES.is_suppressed(cx, VICTIM) is True
        assert CE.erasures_for(cx, VICTIM)[0]["suppressed"] is True


def test_suppressing_never_downgrades_an_existing_hard_bounce(monkeypatch, tmp_path):
    appmod = _reload(monkeypatch, tmp_path)
    from dashboard import email_suppression as ES
    with sqlite3.connect(appmod.LOG_DB) as cx:
        ES.init_table(cx)
        ES.add(cx, VICTIM, "hard", "mailbox does not exist", "bounce-scan")
    r = _post(appmod, "/api/console/client-erasure/erase",
              {"email": VICTIM, "confirm": VICTIM, "also_suppress": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True, "the erasure must have run"
    with sqlite3.connect(appmod.LOG_DB) as cx:
        assert ES.suppression_reason(cx, VICTIM) == "hard"
