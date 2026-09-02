"""Publishing to the portal when a report or invoice is printed or emailed.

Two guarantees this pins:

1. Exactly ONE portal-link email, at first publish. The upsert's existing `send`
   flag emails UNCONDITIONALLY -- its docstring claimed otherwise, which is how a
   client ends up with "Your personal healing home is ready" every time the
   practitioner hits print. `send_if_new` emails only when a token was actually
   minted, so a longstanding client is never mailed as if they were new.

2. A comped Biofield intake un-blurs. The paid gate keys on payment, and a
   deliberately comped analysis has none -- so with the gate ON, exactly the
   clients Glen chose to comp would be locked out of their own report.
"""
import json

import pytest


def test_send_if_new_emails_only_a_newly_minted_portal(monkeypatch):
    import app as _app
    sent = []
    monkeypatch.setattr(_app, "_send_full_report_email",
                        lambda *a, **k: sent.append(a[0]), raising=False)

    # A genuinely new portal mints a token -> notify.
    assert _app._portal_link_should_email({"send_if_new": True}, token_minted=True) is True
    # A returning client keeps their link -> never mailed as if new.
    assert _app._portal_link_should_email({"send_if_new": True}, token_minted=False) is False
    # The legacy flag is unchanged: it still notifies on every publish, which the
    # new-scan re-notify path relies on.
    assert _app._portal_link_should_email({"send": True}, token_minted=False) is True
    assert _app._portal_link_should_email({}, token_minted=True) is False


def test_a_comped_intake_unlocks_without_payment(monkeypatch):
    import app as _app
    monkeypatch.setattr(_app, "_portal_paid_gate_enabled", lambda: True)
    monkeypatch.setattr(_app, "_has_paid_biofield", lambda e: False)
    monkeypatch.setattr(_app, "_active_membership_for_email", lambda e: None)
    monkeypatch.setattr(_app, "_family_plan_enabled", lambda: False)

    monkeypatch.setattr(_app, "_latest_report_content",
                        lambda e: {"comped_intake": True})
    assert _app._portal_biofield_unlocked("comped@x.com") is True

    monkeypatch.setattr(_app, "_latest_report_content", lambda e: {})
    assert _app._portal_biofield_unlocked("unpaid@x.com") is False


def test_the_comped_marker_never_unlocks_when_absent_or_false(monkeypatch):
    import app as _app
    monkeypatch.setattr(_app, "_portal_paid_gate_enabled", lambda: True)
    monkeypatch.setattr(_app, "_has_paid_biofield", lambda e: False)
    monkeypatch.setattr(_app, "_active_membership_for_email", lambda e: None)
    monkeypatch.setattr(_app, "_family_plan_enabled", lambda: False)
    for content in ({"comped_intake": False}, {"comped_intake": None}, {}, None):
        monkeypatch.setattr(_app, "_latest_report_content", lambda e, c=content: c)
        assert _app._portal_biofield_unlocked("x@x.com") is False


def test_a_comped_test_marks_its_portal_content(tmp_path):
    import sqlite3
    from dashboard.biofield_authoring import create_test, init_auth_tables, set_no_charge
    from dashboard import biofield_portal_publish as _bpp
    with sqlite3.connect(tmp_path / "x.db") as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        assert _bpp.comped_intake(cx, tid) is False
        set_no_charge(cx, tid, True)
        assert _bpp.comped_intake(cx, tid) is True


def test_portal_content_carries_the_comped_marker(tmp_path, monkeypatch):
    import sqlite3
    from dashboard.biofield_authoring import (
        add_chain_row, create_test, init_auth_tables, set_no_charge)
    from dashboard import biofield_portal_publish as _bpp
    monkeypatch.setattr(_bpp, "load_catalog", lambda: [], raising=False)
    with sqlite3.connect(tmp_path / "x.db") as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        add_chain_row(cx, tid, 1, "Terrain", "Terrain", "", confirmed=1, origin="live")
        built = _bpp.build_portal_content(cx, tid, special_price_cents=0)
        assert built["content"]["comped_intake"] is False
        set_no_charge(cx, tid, True)
        built = _bpp.build_portal_content(cx, tid, special_price_cents=0)
        assert built["content"]["comped_intake"] is True


def test_publish_passes_send_if_new_through_to_the_upsert():
    from dashboard import biofield_portal_publish as _bpp
    seen = {}

    class _R:
        status_code = 200
        def json(self):
            return {"ok": True, "token": "t", "url": "u", "updated": False}

    def _post(url, json=None, headers=None, timeout=None):
        seen.update(json or {})
        return _R()

    _bpp.publish_to_portal({"email": "a@x.com"}, base_url="https://x", console_key="k",
                           send_if_new=True, http_post=_post)
    assert seen.get("send_if_new") is True
    assert seen.get("send") is False        # never the unconditional flag

    seen.clear()
    _bpp.publish_to_portal({"email": "a@x.com"}, base_url="https://x", console_key="k",
                           http_post=_post)
    assert "send_if_new" not in seen


def _app_with_publish(tmp_path, monkeypatch, calls):
    import sqlite3
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard
    monkeypatch.setattr(dashboard, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "Pam", "pam@example.com", "2026-09-02")
        add_chain_row(cx, tid, 1, "Terrain", "Terrain", "Terrain Restore", confirmed=1)

    def _pub(test_id, special, send=False, send_if_new=False):
        calls.append({"test_id": test_id, "send": send, "send_if_new": send_if_new})
        return {"ok": True, "url": "u"}

    return create_app(db, fetch_profile=lambda e: {}, auto_publish=_pub), tid


def _stub_pdf(monkeypatch, tmp_path):
    """Make the PDF step succeed; CI has no PDF toolchain."""
    import biofield_local_app as _bla
    monkeypatch.setattr(_bla, "save_report_pdf",
                        lambda html, out: open(out, "wb").write(b"%PDF-1.4 stub"))
    monkeypatch.setenv("BIOFIELD_REPORTS_DIR", str(tmp_path / "reports"))
    import os
    os.makedirs(tmp_path / "reports", exist_ok=True)


def test_printing_the_report_publishes_it_once_only(tmp_path, monkeypatch):
    calls = []
    app, tid = _app_with_publish(tmp_path, monkeypatch, calls)
    _stub_pdf(monkeypatch, tmp_path)
    r = app.test_client().get(f"/test/{tid}/report.pdf")
    assert r.status_code == 200
    assert calls, "printing the report did not publish it to the portal"
    assert calls[-1]["send_if_new"] is True
    assert calls[-1]["send"] is False           # never the unconditional email flag


def test_a_portal_failure_never_breaks_the_print(tmp_path, monkeypatch):
    """The report still prints when the portal is unreachable."""
    import biofield_local_app as _bla
    calls = []
    app, tid = _app_with_publish(tmp_path, monkeypatch, calls)
    _stub_pdf(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("portal down")

    app.view_functions  # touch, then rebuild with a failing publisher
    broken = _bla.create_app(str(tmp_path / "chat_log.db"), fetch_profile=lambda e: {},
                             auto_publish=_boom)
    r = broken.test_client().get(f"/test/{tid}/report.pdf")
    assert r.status_code == 200, "a portal outage must not break printing"


def test_a_failed_print_publishes_nothing(tmp_path, monkeypatch):
    """Nothing reaches the client's portal for an output that never happened."""
    import biofield_local_app as _bla
    calls = []
    app, tid = _app_with_publish(tmp_path, monkeypatch, calls)

    def _fail(html, out):
        raise RuntimeError("no pdf toolchain")

    monkeypatch.setattr(_bla, "save_report_pdf", _fail)
    r = app.test_client().get(f"/test/{tid}/report.pdf")
    assert r.status_code == 500
    assert calls == []
