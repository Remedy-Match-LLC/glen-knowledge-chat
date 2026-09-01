"""A practitioner's tier must follow their certification level.

Before this, `practitioners.tier` was written once and never moved: the cert
upsert only filled a null (`COALESCE(tier,'panel_in_cert')`) and the admin
level write did not touch tier at all. Nothing in the codebase could assign
`panel_certified`, so a coach who finished all twelve modules still read as
"in certification" forever and the finder's "Certified" chip matched nobody.

The safety constraint is the point of most of this file. `practitioners.tier`
is SHARED with the scraped directory, whose rows carry `org_member`,
`healing_oasis` and `farm`. A level write must only ever move a row between
`panel_in_cert` and `panel_certified`; a directory row's tier is not ours to
rewrite at any level.

The fake cursor here EVALUATES the UPDATE's `tier=` expression the way Postgres
would, so the assertions are about the tier the row ends up with rather than the
shape of a SQL string.
"""
import shutil

import pytest


# ── a fake Supabase cursor that resolves the tier the row ends up with ────────

def _split_top_level(body):
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _eval_tier_expr(expr, vals, current_tier):
    """Evaluate a `tier=` right-hand side: a bare %s/column, or COALESCE(...)."""
    expr = expr.strip()
    args = [expr]
    if expr.upper().startswith("COALESCE(") and expr.endswith(")"):
        args = _split_top_level(expr[len("COALESCE("):-1])
    vals = list(vals)
    for arg in args:
        arg = arg.strip()
        if arg == "%s":
            v = vals.pop(0)
        elif arg == "tier":
            v = current_tier
        elif arg.startswith("'") and arg.endswith("'"):
            v = arg[1:-1]
        else:
            raise AssertionError(f"unhandled tier expression fragment: {arg!r}")
        if v is not None:
            return v
    return None


def tier_after_update(sql, params, current_tier):
    """The tier the row carries after this UPDATE runs against `current_tier`."""
    s = " ".join(sql.split())
    body = s.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    idx, found = 0, None
    for part in _split_top_level(body):
        col, _, expr = part.partition("=")
        n = expr.count("%s")
        if col.strip() == "tier":
            found = (expr, list(params)[idx:idx + n])
        idx += n
    if found is None:
        return current_tier          # the write leaves tier alone entirely
    return _eval_tier_expr(found[0], found[1], current_tier)


class _FakeCur:
    """Serves one practitioners row and records the writes against it."""

    def __init__(self, row):
        self.row = dict(row) if row else None
        self.updates = []            # (sql, params)
        self._r = None

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT") and "FROM practitioners" in s:
            self._r = self.row
        elif s.startswith("UPDATE practitioners SET"):
            self.updates.append((s, list(params)))
            self._r = None
        elif "INSERT INTO practitioners" in s:
            self._r = {"id": "new-uuid"}
        else:
            self._r = None

    def fetchone(self):
        return self._r

    def resulting_tier(self):
        """The tier the row ends up with after every UPDATE this cursor saw."""
        tier = (self.row or {}).get("tier")
        for sql, params in self.updates:
            tier = tier_after_update(sql, params, tier)
        return tier


class _FakeCtx:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self.cur

    def __exit__(self, *a):
        return False


@pytest.fixture
def cursor_for(monkeypatch):
    def _make(row):
        cur = _FakeCur(row)
        import db_supabase
        monkeypatch.setattr(db_supabase, "supabase_cursor", lambda: _FakeCtx(cur))
        return cur
    return _make


# ── the rule itself ───────────────────────────────────────────────────────────

def test_the_cert_pair_moves_with_the_level():
    from dashboard.practitioner_portal import cert_tier_for_level
    assert cert_tier_for_level("panel_in_cert", 12) == "panel_certified"
    assert cert_tier_for_level("panel_certified", 11) == "panel_in_cert"
    assert cert_tier_for_level("panel_in_cert", 0) == "panel_in_cert"
    assert cert_tier_for_level("panel_certified", 12) == "panel_certified"


@pytest.mark.parametrize("tier", ["org_member", "healing_oasis", "farm"])
@pytest.mark.parametrize("level", [0, 6, 11, 12])
def test_a_directory_tier_is_never_rewritten(tier, level):
    """The one that matters most: a scraped directory practitioner keeps her tier
    at every level. Rewriting it would change how she appears in the public
    finder, and she is not on the certification track at all."""
    from dashboard.practitioner_portal import cert_tier_for_level
    assert cert_tier_for_level(tier, level) is None, (
        f"{tier} must be left alone at level {level}")


def test_no_tier_is_left_to_the_callers_default():
    from dashboard.practitioner_portal import cert_tier_for_level
    assert cert_tier_for_level(None, 12) is None
    assert cert_tier_for_level("", 12) is None


# ── writer: the cert-student upsert (POST /api/cert/student) ─────────────────

def test_upsert_promotes_a_cert_row_at_twelve(cursor_for):
    cur = cursor_for({"id": "p1", "tier": "panel_in_cert"})
    from dashboard.practitioner_portal import upsert_cert_student
    upsert_cert_student("coach@x.com", modules_completed=12)
    assert cur.resulting_tier() == "panel_certified"


def test_upsert_demotes_a_cert_row_below_twelve(cursor_for):
    cur = cursor_for({"id": "p1", "tier": "panel_certified"})
    from dashboard.practitioner_portal import upsert_cert_student
    upsert_cert_student("coach@x.com", modules_completed=11)
    assert cur.resulting_tier() == "panel_in_cert"


@pytest.mark.parametrize("tier", ["org_member", "healing_oasis", "farm"])
def test_upsert_leaves_a_directory_tier_alone(cursor_for, tier):
    cur = cursor_for({"id": "p1", "tier": tier})
    from dashboard.practitioner_portal import upsert_cert_student
    upsert_cert_student("dir@x.com", modules_completed=12)
    assert cur.resulting_tier() == tier


def test_upsert_still_defaults_a_null_tier(cursor_for):
    """Unchanged behaviour: a row with no tier at all gets the panel_in_cert
    default the COALESCE has always given it."""
    cur = cursor_for({"id": "p1", "tier": None})
    from dashboard.practitioner_portal import upsert_cert_student
    upsert_cert_student("new@x.com", modules_completed=12)
    assert cur.resulting_tier() == "panel_in_cert"


# ── writer: the console level control (set_level_and_access) ─────────────────

def test_console_level_write_promotes_a_cert_row_at_twelve(cursor_for):
    cur = cursor_for({"id": "p1", "tier": "panel_in_cert"})
    from dashboard import practitioner_admin as pa
    pa.set_level_and_access("p1", 12, False)
    assert cur.resulting_tier() == "panel_certified"


def test_console_level_write_demotes_below_twelve(cursor_for):
    cur = cursor_for({"id": "p1", "tier": "panel_certified"})
    from dashboard import practitioner_admin as pa
    pa.set_level_and_access("p1", 4, False)
    assert cur.resulting_tier() == "panel_in_cert"


@pytest.mark.parametrize("tier", ["org_member", "healing_oasis", "farm"])
def test_console_level_write_leaves_a_directory_tier_alone(cursor_for, tier):
    cur = cursor_for({"id": "p1", "tier": tier})
    from dashboard import practitioner_admin as pa
    pa.set_level_and_access("p1", 12, True)
    assert cur.resulting_tier() == tier


def test_console_level_write_leaves_a_null_tier_null(cursor_for):
    cur = cursor_for({"id": "p1", "tier": None})
    from dashboard import practitioner_admin as pa
    pa.set_level_and_access("p1", 12, False)
    assert cur.resulting_tier() is None


def test_console_level_write_still_sets_the_level_and_access(cursor_for):
    """The tier guard must not disturb what this writer already did."""
    cur = cursor_for({"id": "p1", "tier": "panel_in_cert"})
    from dashboard import practitioner_admin as pa
    pa.set_level_and_access("p1", 99, True)      # clamps to 12
    sql, params = cur.updates[-1]
    assert 12 in params and True in params


# ── writer: the add/edit practitioner form ───────────────────────────────────

@pytest.mark.parametrize("tier", ["org_member", "healing_oasis", "farm"])
def test_add_form_update_leaves_a_directory_tier_alone(cursor_for, tier):
    cur = cursor_for({"id": "p1", "tier": tier})
    from dashboard import practitioner_admin as pa
    pa.create_or_update_practitioner({
        "email": "d@x.com", "name": "D", "portal_role": "coach", "credentials": None,
        "level": 12, "wholesale_access": False, "list_in_finder": False,
        "city": None, "state": None})
    assert cur.resulting_tier() == tier


def test_add_form_update_promotes_a_cert_row_at_twelve(cursor_for):
    cur = cursor_for({"id": "p1", "tier": "panel_in_cert"})
    from dashboard import practitioner_admin as pa
    pa.create_or_update_practitioner({
        "email": "c@x.com", "name": "C", "portal_role": "coach", "credentials": None,
        "level": 12, "wholesale_access": False, "list_in_finder": False,
        "city": None, "state": None})
    assert cur.resulting_tier() == "panel_certified"


# ── the roster row carries the certification state ───────────────────────────

def test_build_rows_exposes_certified():
    from dashboard import practitioner_admin as pa
    rows = pa.build_rows([
        {"id": "a", "portal_role": "coach", "modules_completed": 12,
         "tier": "panel_certified"},
        {"id": "b", "portal_role": "coach", "modules_completed": 6,
         "tier": "panel_in_cert"},
        {"id": "c", "portal_role": "licensed", "modules_completed": 0,
         "tier": "org_member"},
    ], {})
    assert [r["certified"] for r in rows] == [True, False, False]


def test_list_query_selects_tier():
    """build_rows cannot report a tier the SELECT never fetched."""
    from dashboard import practitioner_admin as pa
    assert "tier" in [c.strip() for c in pa._LIST_COLS.split(",")]


# ── the console route reports the resulting certification state ──────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def test_level_access_route_reports_certified(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    seen = {}
    monkeypatch.setattr(pa, "set_level_and_access",
                        lambda pid, level, access, **kw: seen.update(
                            {"pid": pid, "level": level, "access": access})
                        or "panel_certified")
    r = c.post("/api/console/practitioners/p9/edit?key=" + (appmod.CONSOLE_SECRET or ""),
               json={"action": "level_access", "level": 12, "wholesale_access": True})
    assert r.status_code == 200
    assert seen == {"pid": "p9", "level": 12, "access": True}
    assert r.get_json()["certified"] is True


def test_level_access_route_reports_not_certified(client, monkeypatch):
    c, appmod = client
    from dashboard import practitioner_admin as pa
    monkeypatch.setattr(pa, "set_level_and_access",
                        lambda pid, level, access, **kw: "panel_in_cert")
    r = c.post("/api/console/practitioners/p9/edit?key=" + (appmod.CONSOLE_SECRET or ""),
               json={"action": "level_access", "level": 5})
    assert r.status_code == 200
    assert r.get_json()["certified"] is False


# ── the console control, executed ────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_the_console_level_control_posts_the_chosen_level_and_reflects_it():
    """Drives the roster page's own saveLevelAccess handler under Node against a
    DOM stub, rather than grepping the source: a source match would pass on a
    handler that never posts. Asserts the operator is shown what they are
    changing FROM, that the level they typed is what gets posted, and that the
    resulting certification state comes back into the flash message."""
    import json as _json
    import pathlib
    import subprocess

    import app as appmod
    page = (pathlib.Path(appmod.STATIC) / "console-practitioners.html").read_text()
    script = page.split("<script>")[-1].split("</script>")[0]

    harness = """
    const els = {};
    function mk(id){ return els[id] || (els[id] = {
      id, value:'', checked:false, textContent:'', className:'', style:{},
      classList:{ add(){}, remove(){}, toggle(){} },
      addEventListener(){}, insertAdjacentHTML(){} }); }
    global.window = { ISO_COUNTRY_CODES:['US'] };
    global.location = { search:'?key=SEKRIT' };
    global.document = {
      getElementById: mk, body:{ insertAdjacentHTML(){} },
      addEventListener(){},
    };
    let CONFIRMS = [];
    global.confirm = (t) => { CONFIRMS.push(t); return true; };
    let POSTS = [];
    global.fetch = (url, opts) => {
      POSTS.push({ url, body: (opts && opts.body) ? JSON.parse(opts.body) : null });
      if (String(url).indexOf('/edit') === -1) return Promise.resolve({ status:200, json:()=>Promise.resolve({ ok:true, rows:[] }) });
      return Promise.resolve({ status:200, json:()=>Promise.resolve(RESPONSE) });
    };
    let RESPONSE = null;
    __SCRIPT__
    (async () => {
      // the operator opens the row, types 12 over the current 6, and saves
      mk('lv-p7').value = '12';
      mk('ws-p7').checked = false;
      RESPONSE = { ok:true, certified:true };
      POSTS = []; CONFIRMS = [];
      await saveLevelAccess('p7', 6);
      await new Promise(r => setTimeout(r, 0));
      const edit = POSTS.filter(p => String(p.url).indexOf('/edit') !== -1);
      console.log(JSON.stringify({
        confirms: CONFIRMS, posts: edit, flash: mk('msg').textContent,
        cls: mk('msg').className }));
    })();
    """.replace("__SCRIPT__", script)

    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = _json.loads(out.stdout.strip().splitlines()[-1])

    # It posts the level the operator chose, through the existing edit action.
    assert len(res["posts"]) == 1, res
    post = res["posts"][0]
    assert "/api/console/practitioners/p7/edit" in post["url"]
    assert post["body"]["action"] == "level_access"
    assert post["body"]["level"] == 12
    # It shows the operator what they are changing from before acting.
    assert len(res["confirms"]) == 1, res["confirms"]
    assert "6" in res["confirms"][0] and "12" in res["confirms"][0]
    # It reflects the result the server reported.
    assert "12" in res["flash"]
    assert "certified" in res["flash"].lower(), res["flash"]
    assert "ok" in res["cls"]
