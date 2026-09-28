"""A member covered by someone else's Family Plan cannot cancel it.

money-07, 2026-09-28: a covered member's portal said "you're on it" with a Cancel button.
Their cancel updated a family_subscriptions row keyed to their own email, which does not
exist, and answered ok. Live case: a son covered by his mother's plan. Glen, 2026-09-28
("build"): hide Cancel for a covered member and say the plan holder manages it; the server
refuses rather than pretending."""
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

import app as appmod
from dashboard import family_plan as fp
from dashboard import household as hh

HOLDER, MEMBER = "mother@x.com", "son@x.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _plan(monkeypatch):
    monkeypatch.setenv("FAMILY_PLAN_ENABLED", "1")
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        fp.init_family_plan_table(cx)
        hh.init_household_tables(cx)
        cx.execute("DELETE FROM family_subscriptions")
        fp.activate(cx, HOLDER, next_charge_at="2026-10-28", customer_id="c",
                    payment_method_id="p")
        hh.add_member(cx, HOLDER, MEMBER, relationship="child")
        cx.commit()
    yield


def _tok(email):
    from dashboard import client_portal as cp
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        cp.init_client_portal_table(cx)
        cp.upsert_portal(cx, email, "X", {})
        tok = cp.reissue_token(cx, email)
        cx.commit()
    return tok


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _holder_active():
    with sqlite3.connect(appmod.LOG_DB) as cx:
        cx.row_factory = sqlite3.Row
        return fp.is_active(cx, HOLDER)


def test_a_covered_members_cancel_is_refused_and_changes_nothing():
    r = _client().post(f"/api/portal/{_tok(MEMBER)}/family-plan/cancel")
    assert r.status_code == 409 and r.get_json().get("ok") is not True
    assert _holder_active() is True


def test_the_holder_still_cancels():
    r = _client().post(f"/api/portal/{_tok(HOLDER)}/family-plan/cancel")
    assert r.get_json()["ok"] is True and _holder_active() is False


def test_the_payload_tells_the_holder_from_a_covered_member():
    held = appmod._portal_options_for(HOLDER)["family_plan"]
    covered = appmod._portal_options_for(MEMBER)["family_plan"]
    assert held["active"] and held["holder"] is True
    assert covered["active"] and covered["holder"] is False


HARNESS = r"""
const assert = require('assert');
function esc(s){ return String(s); }
FN
const plan = {label: "Family Plan", price_cents: 4900, value_cents: 9900};
const held = familyPlanLine(Object.assign({active: true, holder: true}, plan));
const covered = familyPlanLine(Object.assign({active: true, holder: false}, plan));
assert(held.includes('data-fp-cancel'));
assert(!covered.includes('data-fp-cancel'));
assert(covered.includes('The plan holder manages it'));
assert(familyPlanLine(Object.assign({active: false}, plan)).includes('data-fp-subscribe'));
console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_portal_shows_cancel_only_to_the_holder(tmp_path):
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    at = page.find("\nfunction familyPlanLine(")
    assert at != -1
    fn = page[at:page.find("\n}\n", at) + 3]
    js = tmp_path / "h.js"
    js.write_text(HARNESS.replace("FN", fn))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
