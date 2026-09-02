"""Route tests for the console practitioner-admin page + API.

Supabase is unavailable in tests, so the DB-touching admin functions are
monkeypatched; we assert the routes gate on the console key, validate input,
and orchestrate create / geocode / invite / edit correctly.
"""
import shutil

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "send_magic_link_email", lambda *a, **k: ("test", None))
    monkeypatch.setattr(appmod, "_send_inquiry_email", lambda *a, **k: True)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _key(appmod):
    return appmod.CONSOLE_SECRET or ""


def test_page_served(client):
    c, _ = client
    r = c.get("/console/practitioners")
    assert r.status_code == 200


def test_list_console_gated(client):
    c, appmod = client
    r = c.get("/api/console/practitioners")  # no key
    if appmod.CONSOLE_SECRET:
        assert r.status_code == 401
    else:
        assert r.status_code == 200


def test_list_returns_rows(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "list_practitioners", lambda q=None: [
        {"id": "p1", "name": "Ashley King", "email": "a@b.com", "portal_role": "coach",
         "credentials": "Health Coach", "modules_completed": 0, "wallet_balance_cents": 0,
         "wholesale_unlocked_at": "2026-06-23T00:00:00", "application_status": None,
         "show_contact": True, "city": "Austin", "state": "TX"}])
    r = c.get("/api/console/practitioners?key=" + _key(appmod))
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["rows"][0]["id"] == "p1"
    assert body["rows"][0]["wholesale_access"] is True
    assert body["rows"][0]["section"] == "coach"


def test_create_validation_error_returns_400(client):
    c, appmod = client
    r = c.post("/api/console/practitioners?key=" + _key(appmod),
               json={"email": "bad", "name": "X", "role": "coach"})
    assert r.status_code == 400
    assert "email" in r.get_json()["error"].lower()


def test_create_calls_create_and_invite(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    captured = {}
    monkeypatch.setattr(pa, "create_or_update_practitioner",
                        lambda clean, **kw: captured.update({"clean": clean}) or "pid-1")
    geo = {}
    monkeypatch.setattr(pa, "geocode_and_set_location",
                        lambda pid, city, state, country=None: geo.update(
                            {"pid": pid, "city": city, "country": country}))
    sent = {}
    monkeypatch.setattr(appmod, "_send_practitioner_magic_link",
                        lambda *a, **k: sent.update({"called": True, "args": a}))
    r = c.post("/api/console/practitioners?key=" + _key(appmod), json={
        "email": "aking@yahoo.com", "name": "Ashley King", "role": "coach",
        "credentials": "Health Coach", "wholesale_access": True, "level": 0,
        "list_in_finder": True, "city": "Austin", "state": "TX", "send_invite": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["practitioner_id"] == "pid-1"
    assert body["invite_sent"] is True
    assert captured["clean"]["wholesale_access"] is True
    assert captured["clean"]["level"] == 0
    assert geo["city"] == "Austin"
    assert geo["country"] == "US"
    assert sent.get("called") is True


def test_create_skips_invite_when_not_requested(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "create_or_update_practitioner", lambda clean, **kw: "pid-2")
    sent = {}
    monkeypatch.setattr(appmod, "_send_practitioner_magic_link",
                        lambda *a, **k: sent.update({"called": True}))
    r = c.post("/api/console/practitioners?key=" + _key(appmod), json={
        "email": "x@y.com", "name": "No Invite", "role": "licensed", "send_invite": False})
    assert r.status_code == 200
    assert r.get_json()["invite_sent"] is False
    assert sent.get("called") is None


def test_edit_level_access_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "set_level_and_access",
                        lambda pid, level, wholesale_access: calls.update(
                            {"pid": pid, "level": level, "access": wholesale_access}))
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "level_access", "level": 3, "wholesale_access": False})
    assert r.status_code == 200
    assert calls == {"pid": "p9", "level": 3, "access": False}


def test_edit_finder_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "set_finder_visibility",
                        lambda pid, show: calls.update({"pid": pid, "show": show}))
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "finder", "show": True})
    assert r.status_code == 200
    assert calls == {"pid": "p9", "show": True}


def test_edit_location_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "geocode_and_set_location",
                        lambda pid, city, state, country=None: calls.update(
                            {"pid": pid, "city": city, "state": state, "country": country}))
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "location", "city": "Mexico City", "state": "", "country": "MX"})
    assert r.status_code == 200
    assert calls == {"pid": "p9", "city": "Mexico City", "state": "", "country": "MX"}


def test_edit_resend_invite_dispatch(client, monkeypatch):
    c, appmod = client
    sent = {}
    monkeypatch.setattr(appmod, "_send_practitioner_magic_link",
                        lambda *a, **k: sent.update({"called": True, "args": a}))
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "resend_invite", "email": "a@b.com", "name": "Ashley"})
    assert r.status_code == 200
    assert r.get_json()["sent"] is True
    assert sent.get("called") is True


def test_edit_credentials_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}
    monkeypatch.setattr(pa, "set_credentials",
                        lambda pid, credentials: calls.update({"pid": pid, "cred": credentials}))
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "credentials", "credentials": "ND"})
    assert r.status_code == 200
    assert calls == {"pid": "p9", "cred": "ND"}


def test_edit_dropship_price_persists_practitioner_override(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_settings as ps
    calls = {}
    monkeypatch.setattr(
        ps,
        "set_dropship_unit_cents",
        lambda cx, pid, cents: calls.update({"pid": pid, "cents": cents}),
    )
    response = c.post(
        "/api/console/practitioners/p9/edit?key=" + _key(appmod),
        json={"action": "dropship_price", "unit_cents": 4000},
    )
    assert response.status_code == 200
    assert response.get_json()["dropship_unit_cents"] == 4000
    assert calls == {"pid": "p9", "cents": 4000}


def test_edit_mark_duplicate_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}

    def _mark(pid, target):
        calls.update({"pid": pid, "target": target})
        return {"id": pid, "name": "A B", "email": "e@x.com", "duplicate_of": target}

    monkeypatch.setattr(pa, "mark_duplicate_of", _mark)
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "mark_duplicate", "duplicate_of": "p1"})
    assert r.status_code == 200
    assert calls == {"pid": "p9", "target": "p1"}
    assert r.get_json()["duplicate_of"] == "p1"


def test_edit_mark_duplicate_without_a_target_is_400(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _boom(*a, **k):
        raise AssertionError("must not reach the writer without a target")

    monkeypatch.setattr(pa, "mark_duplicate_of", _boom)
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "mark_duplicate", "duplicate_of": "  "})
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]


def test_edit_mark_duplicate_refusal_is_409_with_the_reason(client, monkeypatch):
    """The operator must see why, or a refused de-duplication reads as a done one."""
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _blocked(pid, target):
        raise pa.DuplicateMarkBlocked("Cannot hide A B as a duplicate: this row is "
                                      "a portal account (coach).")

    monkeypatch.setattr(pa, "mark_duplicate_of", _blocked)
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "mark_duplicate", "duplicate_of": "p1"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["ok"] is False
    assert "portal account" in body["error"]
    assert body["reason"] == body["error"]


def test_edit_mark_duplicate_unknown_row_is_404(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa

    def _missing(pid, target):
        raise pa.PractitionerNotFound(pid)

    monkeypatch.setattr(pa, "mark_duplicate_of", _missing)
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "mark_duplicate", "duplicate_of": "p1"})
    assert r.status_code == 404


def test_edit_unmark_duplicate_dispatch(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    calls = {}

    def _unmark(pid):
        calls["pid"] = pid
        return {"id": pid, "name": "A B", "email": "e@x.com", "was_duplicate_of": "p1"}

    monkeypatch.setattr(pa, "unmark_duplicate", _unmark)
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "unmark_duplicate"})
    assert r.status_code == 200
    assert calls == {"pid": "p9"}
    assert r.get_json()["was_duplicate_of"] == "p1"


def test_edit_unknown_action_400(client, monkeypatch):
    c, appmod = client
    r = c.post("/api/console/practitioners/p9/edit?key=" + _key(appmod),
               json={"action": "explode"})
    assert r.status_code == 400


def test_edit_console_gated(client):
    c, appmod = client
    r = c.post("/api/console/practitioners/p9/edit", json={"action": "finder", "show": True})
    if appmod.CONSOLE_SECRET:
        assert r.status_code == 401


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_a_successful_signup_leaves_the_button_in_a_done_state():
    """The register button used to be restored to "Create my account" and
    re-enabled BEFORE `d.ok` was checked, so a successful signup looked
    identical to one that had not happened: same label, clickable again. The
    second click registers again and sends a second sign-in email.

    Executed under Node against a DOM stub rather than grepped, because a
    source match would pass on an unreachable branch. The stub captures the
    real submit handler out of the page and drives it once per outcome.
    """
    import json as _json
    import pathlib
    import subprocess

    import app as appmod
    page = (pathlib.Path(appmod.STATIC) / "practitioner-register.html").read_text()
    script = page.split("<script>")[-1].split("</script>")[0]

    harness = """
    const els = {};
    function mk(id){ return els[id] || (els[id] = {
      id, value:'', disabled:false, textContent:'', className:'',
      classList:{ add(){}, remove(){}, toggle(){} },
      getAttribute:()=> 'coach', addEventListener(ev,fn){ this['on_'+ev]=fn; },
      insertAdjacentHTML(){} }); }
    global.window = {};
    global.document = {
      getElementById: mk,
      querySelectorAll: () => [ mk('role-coach') ],
    };
    let RESPONSE = null;
    global.fetch = () => Promise.resolve({ json: () => Promise.resolve(RESPONSE) });
    __SCRIPT__
    // pick the coach role so the handler's `role` guard passes
    mk('role-coach').on_click();
    async function run(resp){
      RESPONSE = resp;
      await mk('form').on_submit({ preventDefault(){} });
      await new Promise(r => setTimeout(r, 0));
      const b = mk('submit');
      return { label: b.textContent, disabled: b.disabled };
    }
    (async () => {
      const ok  = await run({ ok:true,  message:'Check your email.' });
      const bad = await run({ ok:false, error:'Email already registered.' });
      console.log(JSON.stringify({ ok, bad }));
    })();
    """.replace("__SCRIPT__", script)

    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = _json.loads(out.stdout.strip().splitlines()[-1])

    # Success: the label must change and the button must NOT invite a second click.
    assert res["ok"]["label"] != "Create my account", res["ok"]
    assert res["ok"]["disabled"] is True, "a successful signup must not stay clickable"
    # Failure: the button must come back so they can correct and retry.
    assert res["bad"]["label"] == "Create my account", res["bad"]
    assert res["bad"]["disabled"] is False, "a failed signup must be retryable"
