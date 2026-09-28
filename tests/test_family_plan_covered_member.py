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
    with sqlite3.connect(appmod.LOG_DB) as cx:           # leave nothing behind (round 1)
        cx.execute("DELETE FROM family_subscriptions WHERE caregiver_email=?", (HOLDER,))
        for t, col in (("household_members", "member_email"), ("households", "primary_email")):
            try:
                cx.execute(f"DELETE FROM {t} WHERE {col} IN (?,?)", (HOLDER, MEMBER))
            except sqlite3.OperationalError:
                pass
        cx.commit()


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


def test_a_repeated_cancel_by_the_holder_is_still_ok():
    """Round 2: a retry after a lost reply must not tell the holder someone else manages it."""
    tok = _tok(HOLDER)
    _client().post(f"/api/portal/{tok}/family-plan/cancel")
    r = _client().post(f"/api/portal/{tok}/family-plan/cancel")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_the_holder_viewing_a_member_is_still_the_holder():
    """Round 1: the page re-points at a viewed member (?member=); holder must follow the
    signed-in person, or a mother viewing her son loses her own Cancel."""
    fpl = appmod._portal_options_for(MEMBER, holder_email=HOLDER)["family_plan"]
    assert fpl["holder"] is True
    fpl = appmod._portal_options_for(HOLDER, holder_email=MEMBER)["family_plan"]
    assert fpl["holder"] is False


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
// No "holder" in an older payload: keep Cancel; the server refuses a non-holder (round 2).
assert(familyPlanLine(Object.assign({active: true}, plan)).includes('data-fp-cancel'));
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


CANCEL_HARNESS = r"""
const assert = require('assert');
function esc(s){ return String(s); }
let reloaded = 0;
global.window = {confirm: () => true, location: {reload: () => { reloaded++; }}};
global.seg = "tok";
function btn(){ const b = {disabled: false, after: [],
  insertAdjacentHTML(pos, html){ this.after.push(html); }}; return b; }
FN
(async () => {
  global.fetch = async () => ({ok: false, status: 409,
    json: async () => ({ok: false, error: "The plan holder manages this plan."})});
  let b = btn();
  await familyPlanCancel(b);
  assert.strictEqual(reloaded, 0);
  assert(b.after.join('').includes('The plan holder manages this plan.'));
  global.fetch = async () => ({ok: true, status: 200, json: async () => ({ok: true})});
  b = btn();
  await familyPlanCancel(b);
  assert.strictEqual(reloaded, 1);
  console.log('OK');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_refused_cancel_is_shown_beside_the_button(tmp_path):
    """Round 1: a 409 reloaded the page silently and the reason never showed."""
    page = open(os.path.join(ROOT, "static", "client-portal.html")).read()
    at = page.find("\nasync function familyPlanCancel(")
    assert at != -1
    fn = page[at:page.find("\n}\n", at) + 3]
    js = tmp_path / "c.js"
    js.write_text(CANCEL_HARNESS.replace("FN", fn))
    out = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr + out.stdout
