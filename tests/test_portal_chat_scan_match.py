"""The portal chat sees the client's own scan match and named order lines (2026-11-19).

The tour account asked "How do I take my remedies?" and was told nothing was on file,
though it had a scan match and two paid orders. Doses stay under the #2020 rule: only
approved Directions give an amount.
"""
import importlib
import json
import sqlite3

import pytest

from dashboard import portal_concierge as pcz

SCAN = {"scan_date": "2026-11-05", "infoceuticals": [
    {"code": "MB5", "label": "MB5 ESR", "rank": 2, "protocol_days": 30, "slug": "mb5-esr"},
    {"code": "ED11", "label": "ED11 Liver Driver", "rank": 1, "protocol_days": 60,
     "slug": "ed11-liver-driver"},
]}
DIRS = {"ed11-liver-driver": "10 drops twice daily in water, or as guided."}
NAMES = {"bone-builder": "Bone Builder"}


def _ctx(**kw):
    return pcz.build_context({}, kw.get("orders", []), scan=kw.get("scan", SCAN),
                             product_name=NAMES.get, directions_for=DIRS.get)


def test_scan_match_counts_as_data_and_is_ranked():
    ctx = _ctx()
    assert ctx["has_data"] is True
    assert [r["label"] for r in ctx["scan_match"]] == ["ED11 Liver Driver", "MB5 ESR"]
    sp = pcz.system_prompt(ctx)
    assert "ED11 Liver Driver, 60-day protocol; MB5 ESR, 30-day protocol" in sp
    assert "scan of 2026-11-05" in sp
    assert "No scan or order data on file" not in sp


def test_only_approved_directions_reach_the_prompt():
    sp = pcz.system_prompt(_ctx())
    assert "Approved directions for ED11 Liver Driver: 10 drops twice daily" in sp
    assert "Approved directions for MB5 ESR" not in sp
    assert pcz.DOSE_RULE in sp
    assert "or as guided" in pcz.DOSE_RULE


def test_an_order_line_with_only_a_slug_is_named():
    ctx = _ctx(scan=None, orders=[{"items": [{"slug": "bone-builder", "qty": 1}]}])
    assert ctx["owned"] == ["Bone Builder"]
    assert ctx["has_data"] is True
    assert pcz.DOSE_RULE in pcz.system_prompt(ctx)


def test_no_data_still_says_so_and_keeps_the_dose_rule():
    ctx = pcz.build_context({}, [], scan=None)
    assert ctx["has_data"] is False
    sp = pcz.system_prompt(ctx)
    assert "No scan or order data on file" in sp
    assert pcz.DOSE_RULE in sp


def test_old_two_argument_call_is_unchanged():
    ctx = pcz.build_context({"layers": [{"n": 1, "title": "T", "remedy": "R"}]},
                            [{"items": [{"name": "X"}]}])
    assert ctx["owned"] == ["X"] and ctx["scan_match"] == [] and ctx["directions"] == {}


def test_a_failing_lookup_never_breaks_the_context():
    def boom(_):
        raise RuntimeError("x")
    ctx = pcz.build_context({}, [{"items": [{"slug": "a"}]}], scan=SCAN,
                            product_name=boom, directions_for=boom)
    assert ctx["owned"] == [] and len(ctx["scan_match"]) == 2 and ctx["directions"] == {}


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        import app
        importlib.reload(app)
    except Exception as exc:
        pytest.skip(f"app import failed: {exc}")
    app.app.config["TESTING"] = True
    return app


def test_directions_need_pinned_copy(app_mod, monkeypatch):
    prods = {"pinned-one": {"name": "Pinned One", "copy_pinned": ["directions"],
                            "directions": "1 capsule daily, or as guided."},
             "unpinned-one": {"name": "Unpinned One", "directions": "3 capsules at once."},
             "other-pinned": {"name": "Other", "copy_pinned": ["description", "intro"],
                              "directions": "5 drops."},
             "warned": {"name": "Warned", "copy_pinned": ["directions"],
                        "directions": "1 tablet daily.", "warning": "Stop at any headache."},
             "flagged": {"name": "Flagged", "copy_pinned": ["directions"], "chat_caution": True,
                         "directions": "2 drops.", "warning": "Not in pregnancy."}}
    monkeypatch.setattr(app_mod, "_get_product", lambda s: prods.get(s))
    monkeypatch.setattr(app_mod, "_resolve_remedy_slug", lambda d: None)
    assert app_mod._portal_chat_approved_directions("pinned-one") == "1 capsule daily, or as guided."
    assert app_mod._portal_chat_approved_directions("unpinned-one") == ""
    assert app_mod._portal_chat_approved_directions("nothing") == ""
    assert app_mod._portal_chat_approved_directions("other-pinned") == ""
    assert app_mod._portal_chat_approved_directions("warned") == {
        "directions": "1 tablet daily.", "caution": "Stop at any headache."}
    assert app_mod._portal_chat_approved_directions("flagged") == {
        "directions": "2 drops.", "caution": "Not in pregnancy."}
    assert app_mod._portal_chat_product_name("unpinned-one") == "Unpinned One"


def test_portal_chat_reads_the_scan_for_the_token_email_not_a_typed_one(app_mod, monkeypatch):
    from dashboard import client_portal
    with sqlite3.connect(app_mod.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        token, _ = client_portal.upsert_portal(cx, email="owner@x.com", name="O", content={})
    seen = []

    def fake_scan(email, scan_date=None):
        seen.append(email)
        return SCAN
    monkeypatch.setattr(app_mod, "_scan_recommendations_for", fake_scan)
    _no_network(app_mod, monkeypatch)
    with app_mod.app.test_client() as client:
        client.post(f"/api/portal/{token}/chat",
                    data=json.dumps({"query": "How do I take my remedies?",
                                     "email": "someone-else@x.com"}),
                    content_type="application/json")
    assert seen and set(seen) == {"owner@x.com"}


def test_a_caution_travels_with_its_directions():
    ctx = pcz.build_context({}, [{"items": [{"name": "Warned"}]}], scan=None,
                            directions_for={"Warned": {"directions": "1 tablet daily.",
                                                       "caution": "Stop at any headache."}}.get)
    sp = pcz.system_prompt(ctx)
    assert "Approved directions for Warned: 1 tablet daily." in sp
    assert "Caution for Warned, to give in full" in sp and "Stop at any headache." in sp


def test_a_duplicate_code_is_listed_once_at_its_best_rank():
    scan = {"infoceuticals": [{"code": "MB5", "label": "MB5 ESR", "rank": 7},
                              {"code": "MB5", "label": "MB5 ESR", "rank": 2},
                              {"code": "ED11", "label": "ED11 Liver", "rank": 4}]}
    ctx = pcz.build_context({}, [], scan=scan)
    assert [(r["label"], r["rank"]) for r in ctx["scan_match"]] == [("MB5 ESR", 2), ("ED11 Liver", 4)]


def test_a_mihealth_only_scan_is_data_not_remedies():
    ctx = pcz.build_context({}, [], scan={"infoceuticals": [],
                                          "mihealth": [{"code": "ER1", "label": "ER1 Calm"}]})
    sp = pcz.system_prompt(ctx)
    assert ctx["has_data"] is True
    assert "No scan or order data on file" not in sp
    assert "ER1 Calm" in sp and "not remedies to take" in sp


def _no_network(app_mod, monkeypatch):
    """Keep the route off the real embedding and model APIs (round 3)."""
    def boom(*a, **k):
        raise RuntimeError("offline in tests")
    monkeypatch.setattr(app_mod, "embed", boom)

    class _Off:
        class messages:
            stream = staticmethod(boom)
            create = staticmethod(boom)
    monkeypatch.setattr(app_mod, "_cl", _Off)


def test_console_draft_reply_reads_the_scan_for_its_client(app_mod, monkeypatch):
    from dashboard import client_portal, portal_chat
    with sqlite3.connect(app_mod.LOG_DB) as cx:
        client_portal.init_client_portal_table(cx)
        client_portal.upsert_portal(cx, email="owner@x.com", name="O", content={})
        portal_chat.init_table(cx)
        portal_chat.add_message(cx, "owner@x.com", "client", "How do I take my remedies?")
    seen = []

    def fake_scan(email, scan_date=None):
        seen.append(email)
        return SCAN
    monkeypatch.setattr(app_mod, "_scan_recommendations_for", fake_scan)
    monkeypatch.setattr(app_mod, "_portal_console_ok", lambda: True)
    _no_network(app_mod, monkeypatch)
    with app_mod.app.test_client() as client:
        client.post("/api/console/portal/Owner@X.com/draft-reply")
    assert seen == ["owner@x.com"]
