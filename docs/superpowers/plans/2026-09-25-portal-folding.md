# Client Portal Card Folding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every portal card gets a working Hide/Show toggle whose state is saved on the server per viewer, with Fold all / Restore and a staff-only Match client's view.

**Architecture:** A small store module (`dashboard/portal_folds.py`) keeps one JSON record per (portal email, viewer). One route pair, `GET/PUT /api/portal/<token>/folds`, resolves the viewer on the server and is gated by `PORTAL_FOLDS_V2`. A pure browser module (`static/js/portal-folds.js`) holds the fold rules. Glue in `static/client-portal.html` applies them after every render. With the setting off the route answers 404 and the page runs the old code unchanged.

**Tech Stack:** Flask (`app.py`), SQLite in tests and Postgres in production through `db`, plain browser JavaScript tested under node, Playwright for the browser check.

**Spec:** `docs/superpowers/specs/2026-09-25-portal-folding-design.md`

## Global Constraints

- Setting names: `PORTAL_FOLDS_V2` (truthy: `1`, `true`, `yes`, `on`) and `PORTAL_FOLDS_V2_EMAILS` (comma-separated portal emails). Set in Doppler only, never on Render.
- Table `portal_fold_state`, primary key (`portal_email`, `viewer`). Upsert with `ON CONFLICT ... DO UPDATE`. Never `INSERT OR REPLACE`, never `lastrowid`.
- Viewer ids: `client`, `staff:master`, `staff:user:<workspace_users.id>`. The access token itself is never stored.
- Record cap: 64 KB serialised, 500 card entries.
- Save debounce: about 600 ms.
- No em dashes and no ALL CAPS in any client-visible text. Button labels: `Hide`, `Show`, `Fold all`, `Restore`, `Match client's view`.
- Never run the whole pytest suite. A bare full run sends real email. Run named files only, built as a bash array.
- Work in `~/worktrees/wt-deploy-chat-58bbe53b` on branch `sess/58bbe53b-portal-folding`. Stage named files. Never `git add -A`. Never `git stash`.
- Mutate each guard and watch its test fail before trusting it. Restore the code from a copy, never `git checkout`.

## Review Focus

1. Two devices or two tabs open at once. A person expects a fold made on the phone to survive an older laptop tab. Pinned in Task 5: the page re-reads the record when the tab becomes visible again.
2. A card with no heading. Folding hides everything except the heading, so it would vanish. Pinned in Task 4: `foldAll` only touches the ids it is given, and Task 5 passes only cards with a heading.
3. The Live Events card already has its own Hide button. Two buttons on one card would confuse. Pinned in Task 3: it is marked `data-fold-skip` and Task 6 asserts no card shows two toggles.
4. A portal link reissued after a lost email. A person expects their folds to remain. Pinned in Task 2: a record saved under the old token is read under the new one.
5. A fold clicked just before closing the page. A person expects it kept. Pinned in Task 5: pending saves flush on `pagehide` with `keepalive`.

---

### Task 1: Fold store module

**Files:**
- Create: `dashboard/portal_folds.py`
- Test: `tests/test_portal_folds_store.py`

**Interfaces:**
- Produces: `init_table(cx)`, `empty() -> dict`, `clean(state) -> dict`, `get(cx, portal_email, viewer) -> dict`, `put(cx, portal_email, viewer, state) -> dict` (returns the cleaned state), exception `TooLarge(ValueError)`, constants `MAX_BYTES = 65536`, `MAX_CARDS = 500`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portal_folds_store.py
"""Server-side fold state for the client portal. Spec:
docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
import json
import sqlite3

import pytest

from dashboard import portal_folds as pf


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "f.db"))
    pf.init_table(c)
    yield c
    c.close()


def test_missing_record_is_empty(cx):
    assert pf.get(cx, "a@x.com", "client") == {"cards": {}, "seen": [], "before_fold_all": {}}


def test_roundtrip_and_email_is_lowercased(cx):
    st = {"cards": {"scans-history": True, "biofield-section": False},
          "seen": ["scans"], "before_fold_all": {"scans": {"scans-history": False}}}
    pf.put(cx, "A@X.com ", "client", st)
    assert pf.get(cx, "a@x.com", "client") == st


def test_viewers_are_separate_rows(cx):
    pf.put(cx, "a@x.com", "client", {"cards": {"c1": True}})
    pf.put(cx, "a@x.com", "staff:master", {"cards": {"c1": False}})
    assert pf.get(cx, "a@x.com", "client")["cards"] == {"c1": True}
    assert pf.get(cx, "a@x.com", "staff:master")["cards"] == {"c1": False}


def test_put_replaces_not_merges(cx):
    pf.put(cx, "a@x.com", "client", {"cards": {"c1": True, "c2": True}})
    pf.put(cx, "a@x.com", "client", {"cards": {"c2": False}})
    assert pf.get(cx, "a@x.com", "client")["cards"] == {"c2": False}


def test_clean_drops_unknown_keys_and_bad_values():
    got = pf.clean({"cards": {"ok": True, "bad": "yes", "": True, 5: True},
                    "seen": ["scans", 3, "scans", ""], "evil": 1,
                    "before_fold_all": {"scans": {"ok": False, "x": 1}, "": {}}})
    assert got == {"cards": {"ok": True}, "seen": ["scans"],
                   "before_fold_all": {"scans": {"ok": False}}}


def test_clean_of_non_dict_is_empty():
    assert pf.clean(None) == pf.empty()
    assert pf.clean([1, 2]) == pf.empty()


def test_card_cap(cx):
    many = {f"c{i}": True for i in range(pf.MAX_CARDS + 50)}
    assert len(pf.put(cx, "a@x.com", "client", {"cards": many})["cards"]) == pf.MAX_CARDS


def test_too_large_is_refused(cx):
    huge = {("k" * 110) + str(i): True for i in range(pf.MAX_CARDS)}
    with pytest.raises(pf.TooLarge):
        pf.put(cx, "a@x.com", "client", {"cards": huge})
    assert pf.get(cx, "a@x.com", "client") == pf.empty()


def test_malformed_stored_json_reads_as_empty(cx):
    cx.execute("INSERT INTO portal_fold_state VALUES (?,?,?,?)",
               ("a@x.com", "client", "{not json", "t"))
    cx.commit()
    assert pf.get(cx, "a@x.com", "client") == pf.empty()


def test_init_table_is_idempotent(cx):
    pf.init_table(cx)
    pf.init_table(cx)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/worktrees/wt-deploy-chat-58bbe53b && python3 -m pytest tests/test_portal_folds_store.py -q`
Expected: FAIL, `ImportError: cannot import name 'portal_folds'`.

- [ ] **Step 3: Implement**

```python
# dashboard/portal_folds.py
"""Card fold state for the client portal, one record per viewer per portal.

Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md

A viewer is 'client', 'staff:master' or 'staff:user:<workspace_users.id>'. The route
decides which; nothing here trusts the browser to say who it is. Keyed by the portal's
email because tokens get reissued and the email does not.
"""
import json
from datetime import datetime, timezone

MAX_BYTES = 64 * 1024
MAX_CARDS = 500
_MAX_ID = 120
_MAX_DOOR = 40
_MAX_DOORS = 50


class TooLarge(ValueError):
    pass


def init_table(cx) -> None:
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_fold_state (
            portal_email TEXT NOT NULL,
            viewer       TEXT NOT NULL,
            state_json   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (portal_email, viewer)
        )
        """
    )
    cx.commit()


def empty() -> dict:
    return {"cards": {}, "seen": [], "before_fold_all": {}}


def _card_map(v) -> dict:
    out = {}
    if not isinstance(v, dict):
        return out
    for k, folded in v.items():
        if isinstance(k, str) and 0 < len(k) <= _MAX_ID and isinstance(folded, bool):
            out[k] = folded
            if len(out) >= MAX_CARDS:
                break
    return out


def _door_ok(d) -> bool:
    return isinstance(d, str) and 0 < len(d) <= _MAX_DOOR


def clean(state) -> dict:
    """Keep only the three known keys, each in its expected shape."""
    if not isinstance(state, dict):
        return empty()
    seen_raw = state.get("seen")
    seen = []
    if isinstance(seen_raw, list):
        for d in seen_raw:
            if _door_ok(d) and d not in seen:
                seen.append(d)
    seen = seen[:_MAX_DOORS]
    bfa = {}
    bfa_raw = state.get("before_fold_all")
    if isinstance(bfa_raw, dict):
        for door, cards in bfa_raw.items():
            if _door_ok(door) and len(bfa) < _MAX_DOORS:
                bfa[door] = _card_map(cards)
    return {"cards": _card_map(state.get("cards")), "seen": seen, "before_fold_all": bfa}


def _norm(email) -> str:
    return (email or "").strip().lower()


def get(cx, portal_email, viewer) -> dict:
    row = cx.execute(
        "SELECT state_json FROM portal_fold_state WHERE portal_email = ? AND viewer = ?",
        (_norm(portal_email), viewer)).fetchone()
    if not row:
        return empty()
    try:
        return clean(json.loads(row[0]))
    except (TypeError, ValueError):
        return empty()


def put(cx, portal_email, viewer, state) -> dict:
    st = clean(state)
    body = json.dumps(st, separators=(",", ":"), sort_keys=True)
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise TooLarge(f"fold record is {len(body)} bytes")
    cx.execute(
        "INSERT INTO portal_fold_state (portal_email, viewer, state_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (portal_email, viewer) DO UPDATE SET "
        "state_json = excluded.state_json, updated_at = excluded.updated_at",
        (_norm(portal_email), viewer, body, datetime.now(timezone.utc).isoformat()))
    cx.commit()
    return st
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_portal_folds_store.py -q`
Expected: 10 passed.

- [ ] **Step 5: Mutate and watch it fail**

Copy the module aside. Change `put` to write `viewer="client"` always. Run the file. `test_viewers_are_separate_rows` must fail. Restore from the copy and re-run green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/portal_folds.py tests/test_portal_folds_store.py
git commit -m "Portal folds: per-viewer fold record store"
```

---

### Task 2: Viewer resolution, setting gate and routes

**Files:**
- Modify: `app.py`. Add helpers directly after `_portal_open_is_owner()` (search `def _portal_open_is_owner`). Add the route directly after `api_client_portal` ends (search `@app.route("/api/portal/<token>")`, then the next `@app.route`).
- Test: `tests/test_portal_folds_routes.py`

**Interfaces:**
- Consumes: `portal_folds.init_table/get/put/TooLarge` from Task 1. Existing `_present_console_key()`, `_portal_open_is_owner()`, `_portal_record_for(cx, token)`, `db`, `LOG_DB`, `_db_lock`.
- Produces: `_portal_folds_enabled(email) -> bool`, `_workspace_user_id_for_token(token) -> int | None`, `_portal_fold_viewer() -> str | None`.
- HTTP: `GET /api/portal/<token>/folds[?of=client]` returns `{"ok": true, "viewer": "client"|"staff", "state": {...}}`. `PUT` with body `{"state": {...}}` returns `{"ok": true, "state": {...}}`. 404 unknown token or setting off. 403 for a VA or unrecognised key. 400 bad `of` or bad body. 413 too large.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_portal_folds_routes.py
"""GET/PUT /api/portal/<token>/folds. Spec:
docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
import sqlite3

import pytest

_RAE = "rae-owner-token-folds"
_VA = "va-token-folds"
SECRET = "test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", SECRET)
    monkeypatch.setenv("PORTAL_FOLDS_V2", "1")
    monkeypatch.delenv("PORTAL_FOLDS_V2_EMAILS", raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def _seed_portal(appmod, email="brooke@example.com"):
    from dashboard import client_portal as cp
    cx = sqlite3.connect(appmod.LOG_DB)
    cp.init_client_portal_table(cx)
    token, _ = cp.upsert_portal(cx, email, "Brooke Webb", {"greeting": "Aloha."})
    cx.close()
    return token


def _seed_user(appmod, name, scope, token):
    appmod._init_workspace_schema()
    with appmod.db.connect(appmod.LOG_DB) as cx:
        cx.execute("INSERT INTO workspace_users (name, display_name, scope) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET scope=excluded.scope", (name, name, scope))
        uid = cx.execute("SELECT id FROM workspace_users WHERE name=?", (name,)).fetchone()[0]
        cx.execute("INSERT INTO access_tokens (token, user_id) VALUES (?,?)", (token, uid))
        cx.commit()
    return uid


def _rows(appmod):
    cx = sqlite3.connect(appmod.LOG_DB)
    try:
        return {(r[0], r[1]): r[2] for r in cx.execute(
            "SELECT portal_email, viewer, state_json FROM portal_fold_state")}
    finally:
        cx.close()


def _put(c, tok, state, key=None):
    h = {"X-Console-Key": key} if key else {}
    return c.put(f"/api/portal/{tok}/folds", json={"state": state}, headers=h)


def _get(c, tok, key=None, of=None):
    h = {"X-Console-Key": key} if key else {}
    q = f"?of={of}" if of is not None else ""
    return c.get(f"/api/portal/{tok}/folds{q}", headers=h)


def test_client_roundtrip(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    assert _put(c, tok, {"cards": {"scans-history": True}}).status_code == 200
    r = _get(c, tok)
    assert r.status_code == 200
    assert r.get_json()["viewer"] == "client"
    assert r.get_json()["state"]["cards"] == {"scans-history": True}


def test_staff_put_never_touches_client_row(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    before = _rows(appmod)[("brooke@example.com", "client")]
    assert _put(c, tok, {"cards": {"a": False}}, key=SECRET).status_code == 200
    rows = _rows(appmod)
    assert rows[("brooke@example.com", "client")] == before
    assert ("brooke@example.com", "staff:master") in rows


def test_rae_gets_her_own_row_keyed_by_account_id(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    uid = _seed_user(appmod, "rae", "workspace:rae", _RAE)
    assert _put(c, tok, {"cards": {"a": True}}, key=_RAE).status_code == 200
    rows = _rows(appmod)
    assert ("brooke@example.com", f"staff:user:{uid}") in rows
    assert not any(_RAE in v for (_, v) in rows)
    assert _get(c, tok, key=_RAE).get_json()["viewer"] == "staff"


def test_staff_reads_client_view_with_of(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    _put(c, tok, {"cards": {"a": False}}, key=SECRET)
    assert _get(c, tok, key=SECRET, of="client").get_json()["state"]["cards"] == {"a": True}
    assert _get(c, tok, key=SECRET).get_json()["state"]["cards"] == {"a": False}


def test_client_cannot_reach_a_staff_row(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": False}}, key=SECRET)
    assert _get(c, tok).get_json()["state"]["cards"] == {}
    assert _get(c, tok, of="client").get_json()["state"]["cards"] == {}
    for bad in ("staff", "staff:master", "x"):
        assert _get(c, tok, of=bad).status_code == 400
        assert _get(c, tok, key=SECRET, of=bad).status_code == 400


def test_va_and_unknown_keys_are_refused(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    _seed_user(appmod, "shaira", "workspace:shaira", _VA)
    for key in (_VA, "not-a-key"):
        assert _put(c, tok, {"cards": {"a": True}}, key=key).status_code == 403
        assert _get(c, tok, key=key).status_code == 403
    assert _rows(appmod) == {}


def test_unknown_token_404(client):
    c, _ = client
    assert _get(c, "no-such-token").status_code == 404
    assert _put(c, "no-such-token", {"cards": {}}).status_code == 404


def test_setting_off_404(client, monkeypatch):
    c, appmod = client
    tok = _seed_portal(appmod)
    monkeypatch.delenv("PORTAL_FOLDS_V2")
    assert _get(c, tok).status_code == 404
    assert _put(c, tok, {"cards": {}}).status_code == 404


def test_email_allowlist_enables_one_portal(client, monkeypatch):
    c, appmod = client
    on = _seed_portal(appmod, "fold-test@example.com")
    off = _seed_portal(appmod, "brooke@example.com")
    monkeypatch.delenv("PORTAL_FOLDS_V2")
    monkeypatch.setenv("PORTAL_FOLDS_V2_EMAILS", " Fold-Test@example.com ,other@x.com")
    assert _get(c, on).status_code == 200
    assert _get(c, off).status_code == 404


def test_bad_body_400_and_too_large_413(client):
    c, appmod = client
    tok = _seed_portal(appmod)
    assert c.put(f"/api/portal/{tok}/folds", data="nope",
                 content_type="application/json").status_code == 400
    assert c.put(f"/api/portal/{tok}/folds", json={"state": [1]}).status_code == 400
    huge = {("k" * 110) + str(i): True for i in range(500)}
    assert _put(c, tok, {"cards": huge}).status_code == 413


def test_reissued_link_keeps_folds(client):
    c, appmod = client
    from dashboard import client_portal as cp
    tok = _seed_portal(appmod)
    _put(c, tok, {"cards": {"a": True}})
    cx = sqlite3.connect(appmod.LOG_DB)
    new_tok = cp.reissue_token(cx, "brooke@example.com")
    cx.close()
    assert new_tok and new_tok != tok
    assert _get(c, new_tok).get_json()["state"]["cards"] == {"a": True}
```

Before running, check `reissue_token`'s return value in `dashboard/client_portal.py`. If it returns a tuple, unpack it the same way `upsert_portal` is unpacked.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_portal_folds_routes.py -q`
Expected: FAIL with 404 on the first request, because the route does not exist.

- [ ] **Step 3: Implement the helpers** (after `_portal_open_is_owner`)

```python
def _portal_folds_enabled(email):
    """PORTAL_FOLDS_V2 on for everyone, or this portal's email listed in
    PORTAL_FOLDS_V2_EMAILS. Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
    if os.environ.get("PORTAL_FOLDS_V2", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    allow = {e.strip().lower() for e in
             os.environ.get("PORTAL_FOLDS_V2_EMAILS", "").split(",") if e.strip()}
    return (email or "").strip().lower() in allow


def _workspace_user_id_for_token(token):
    """The staff account behind a live access token, or None. The id, not the token,
    names a staff fold record, so a reissued sign-in link keeps the same folds."""
    if not token:
        return None
    try:
        with db.connect(LOG_DB) as cx:
            row = cx.execute(
                "SELECT user_id FROM access_tokens WHERE token = ? AND revoked_at IS NULL",
                (token,)).fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _portal_fold_viewer():
    """Whose fold record this request reads and writes. 'client' when no console key is
    presented; 'staff:master' or 'staff:user:<id>' for an owner; None for any other key
    (a VA token, or garbage), which the route refuses rather than guessing."""
    key = _present_console_key()
    if not key:
        return "client"
    if not _portal_open_is_owner():
        return None
    uid = _workspace_user_id_for_token(key)
    return "staff:master" if uid is None else f"staff:user:{uid}"
```

- [ ] **Step 4: Implement the route** (after `api_client_portal`)

```python
@app.route("/api/portal/<token>/folds", methods=["GET", "PUT"])
def api_portal_folds(token):
    """One fold record per viewer per portal. Staff never write the client's record:
    the viewer comes from the request's credentials, and there is no parameter that
    names another viewer for a write. ?of=client lets staff READ the client's record
    for Match client's view. Spec: docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
    from dashboard import client_portal as _cp
    from dashboard import portal_folds as _pf
    with db.connect(LOG_DB) as cx:
        _cp.init_client_portal_table(cx)
        portal = _portal_record_for(cx, token)
    email = ((portal or {}).get("email") or "").strip().lower()
    if not email or not _portal_folds_enabled(email):
        return jsonify({"error": "not found"}), 404
    viewer = _portal_fold_viewer()
    if viewer is None:
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        of = request.args.get("of")
        if of not in (None, "client"):
            return jsonify({"error": "bad of"}), 400
        target = "client" if of == "client" else viewer
        with db.connect(LOG_DB) as cx:
            _pf.init_table(cx)
            state = _pf.get(cx, email, target)
        return jsonify({"ok": True, "viewer": "client" if viewer == "client" else "staff",
                        "state": state})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("state"), dict):
        return jsonify({"error": "bad body"}), 400
    try:
        with _db_lock, db.connect(LOG_DB) as cx:
            _pf.init_table(cx)
            state = _pf.put(cx, email, viewer, data["state"])
    except _pf.TooLarge:
        return jsonify({"error": "too large"}), 413
    return jsonify({"ok": True, "state": state})
```

- [ ] **Step 5: Run to verify they pass**

Run: `python3 -m pytest tests/test_portal_folds_routes.py tests/test_portal_folds_store.py -q`
Expected: all pass.

- [ ] **Step 6: Mutate each guard and watch it fail**

One at a time, from a saved copy of `app.py`:
1. In `api_portal_folds`, write with `"client"` instead of `viewer`. `test_staff_put_never_touches_client_row` must fail.
2. In `_portal_fold_viewer`, return `"client"` in place of `None`. `test_va_and_unknown_keys_are_refused` must fail.
3. Accept any `of` value as the target. `test_client_cannot_reach_a_staff_row` must fail.
4. Make `_portal_folds_enabled` return True. `test_setting_off_404` must fail.

Restore from the copy after each mutation. Re-run green.

- [ ] **Step 7: Run the neighbouring portal route tests**

Run: `python3 -m pytest tests/test_client_portal_routes.py tests/test_portal_card_folding.py -q`
Expected: same pass set as on `origin/main`.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_portal_folds_routes.py
git commit -m "Portal folds: GET/PUT route, server-decided viewer, setting gate"
```

---

### Task 3: A fixed fold id on every card

**Files:**
- Modify: `static/client-portal.html`. Every card template, about 97 opening tags. Also the two `className = "card..."` sites near `warning.className` and `upsell.className`.
- Modify: `static/js/portal-*.js` and `static/portal-mentor.js`, wherever a card is built. A grep finds 3.
- Test: `tests/test_portal_fold_ids.py`

**Interfaces:**
- Produces: every `.card` element carries `data-fold-id="<name>"`, or keeps its existing `id`, which Task 5 uses as the fold id. The Live Events summary card gets `data-fold-skip="1"`.

Naming rule: a lower-case, hyphenated name that says what the card is, prefixed with its door when that helps. Examples: `scans-history`, `account-your-account`, `remedies-fullscript`. A card built in a loop appends a stable slug from its data, never an index. The health section, for example, becomes `data-fold-id="health-${slug(sec.title)}"`. Use the page's existing slug helper if one exists (search `function slug`). Otherwise add `function foldSlug(s){ return String(s||"").toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,""); }` beside `wireCardFolding` and use that.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portal_fold_ids.py
"""Every portal card carries a fold id, so a fold is keyed on a fixed name and not on
heading text (the 2026-09-16 defect). Runtime uniqueness is checked in the browser
test, because loop-built ids only exist once rendered."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = [ROOT / "static" / "client-portal.html", ROOT / "static" / "portal-mentor.js",
         *sorted((ROOT / "static" / "js").glob("portal-*.js"))]

# An opening tag whose class list starts with the bare word card, in HTML or inside a
# JS string: class="card", class="card quiet", class=\"card x\", class='card'.
CARD_CLASS = re.compile(r"""class=\\?(["'])card(?:\s[^"'\\]*)?\\?\1""")


def _strip_comments(src):
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _card_tags(src):
    for m in CARD_CLASS.finditer(src):
        start = src.rfind("<", 0, m.start())
        end = src.find(">", m.end())
        yield src[start:end + 1]


def test_every_card_tag_has_a_fold_id():
    missing = []
    for f in FILES:
        for tag in _card_tags(_strip_comments(f.read_text())):
            if "data-fold-id=" not in tag and not re.search(r"""\sid=\\?["']""", tag):
                missing.append(f"{f.name}: {tag[:120]}")
    assert not missing, "cards with no fold id:\n" + "\n".join(missing)


def test_script_built_cards_get_a_fold_id():
    page = (ROOT / "static" / "client-portal.html").read_text()
    for var in ("warning", "upsell"):
        assert re.search(rf"{var}\.dataset\.foldId\s*=", page), var


def test_the_scan_finds_cards_at_all():
    # A clean zero here would mean the regex went blind, not that all is well.
    n = sum(1 for f in FILES for _ in _card_tags(_strip_comments(f.read_text())))
    assert n >= 80, n


def test_live_events_card_is_skipped():
    page = (ROOT / "static" / "client-portal.html").read_text()
    assert re.search(r'class="card calendar-summary"[^>]*data-fold-skip="1"', page)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_portal_fold_ids.py -q`
Expected: `test_every_card_tag_has_a_fold_id` fails and lists every card without an id. `test_the_scan_finds_cards_at_all` passes. If it fails, the regex is wrong, so fix it before going on.

- [ ] **Step 3: Add the ids**

Work through the failure list top to bottom. Add `data-fold-id="..."` to each tag per the naming rule. Where two branches build the same card, one shown or the other, give both the same id. For `warning` and `upsell`, add `warning.dataset.foldId = "portal-load-alert";` and `upsell.dataset.foldId = "account-upsell";` after their `className` lines. Add `data-fold-skip="1"` to the `calendar-summary` card.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_portal_fold_ids.py tests/test_portal_card_folding.py -q`
Expected: all pass. The old fold test still passes because `card.id` behaviour is untouched.

- [ ] **Step 5: Mutate**

Remove one `data-fold-id` and re-run. The test must name that tag. Restore it.

- [ ] **Step 6: Commit**

```bash
git add static/client-portal.html static/portal-mentor.js static/js/portal-*.js tests/test_portal_fold_ids.py
git commit -m "Portal folds: a fixed fold id on every card"
```

Stage only the `static/js/portal-*.js` files actually changed. Name each one.

---

### Task 4: Fold rules module (pure, node-tested)

**Files:**
- Create: `static/js/portal-folds.js`
- Test: `tests/test_portal_folds_rules.js`, plus the wrapper `tests/test_portal_folds_rules_node.py`

**Interfaces:**
- Produces `window.PortalFolds` in the browser, and `module.exports` under node, with:
  - `emptyState() -> {cards:{}, seen:[], before_fold_all:{}}`
  - `normalise(state) -> state`, which tolerates null and missing keys.
  - `resolveDoor(state, ids) -> {id: bool}`. A listed card uses its saved value. An unlisted card uses the default: the first id open, the rest folded.
  - `markSeen(state, door) -> state`, a new object.
  - `setCard(state, id, folded) -> state`, a new object. It leaves `before_fold_all` alone, so Restore stays available.
  - `foldAll(state, door, ids) -> state`. It saves `resolveDoor(state, ids)` into `before_fold_all[door]` and sets every id folded. A second Fold all while a Restore is pending keeps the first saved layout.
  - `restore(state, door) -> state`. It applies `before_fold_all[door]` and deletes that entry. With no entry it is a no-op.
  - `canRestore(state, door) -> bool`
  - `adopt(clientState) -> state`, a deep copy through `normalise`.

- [ ] **Step 1: Write the failing test**

```js
// tests/test_portal_folds_rules.js
// Run: node tests/test_portal_folds_rules.js
const assert = require('assert');
const F = require('../static/js/portal-folds.js');

const ids = ['a', 'b', 'c'];

// first visit: first open, rest folded
assert.deepStrictEqual(F.resolveDoor(F.emptyState(), ids), {a: false, b: true, c: true});

// saved values win; an unlisted card takes the positional default
let s = F.setCard(F.emptyState(), 'b', false);
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: false, b: false, c: true});

// setCard does not mutate its input
const e = F.emptyState();
F.setCard(e, 'a', true);
assert.deepStrictEqual(e.cards, {});

// markSeen adds once
s = F.markSeen(F.markSeen(F.emptyState(), 'scans'), 'scans');
assert.deepStrictEqual(s.seen, ['scans']);

// fold all, then restore returns exactly the earlier layout
s = F.setCard(F.emptyState(), 'b', false);           // a open, b open, c folded
const before = F.resolveDoor(s, ids);
s = F.foldAll(s, 'scans', ids);
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: true, b: true, c: true});
assert.ok(F.canRestore(s, 'scans'));
s = F.restore(s, 'scans');
assert.deepStrictEqual(F.resolveDoor(s, ids), before);
assert.ok(!F.canRestore(s, 'scans'));

// Restore stays available after single clicks in between
s = F.foldAll(F.emptyState(), 'scans', ids);
s = F.setCard(s, 'a', false);
assert.ok(F.canRestore(s, 'scans'));
s = F.restore(s, 'scans');
assert.deepStrictEqual(F.resolveDoor(s, ids), {a: false, b: true, c: true});

// a second Fold all keeps the FIRST saved layout
s = F.setCard(F.emptyState(), 'c', false);
const first = F.resolveDoor(s, ids);
s = F.foldAll(F.foldAll(s, 'scans', ids), 'scans', ids);
assert.deepStrictEqual(F.restore(s, 'scans').cards, Object.assign({}, first));

// foldAll only touches the ids it is given (Review Focus 2: headless cards)
s = F.foldAll(F.setCard(F.emptyState(), 'x', false), 'scans', ['a']);
assert.strictEqual(s.cards.x, false);

// doors are independent
s = F.foldAll(F.emptyState(), 'scans', ids);
assert.ok(!F.canRestore(s, 'home'));
assert.deepStrictEqual(F.restore(s, 'home'), s);

// adopt is a deep copy
const client = {cards: {a: true}, seen: ['scans'], before_fold_all: {}};
const mine = F.adopt(client);
mine.cards.a = false;
assert.strictEqual(client.cards.a, true);

// normalise tolerates junk
assert.deepStrictEqual(F.normalise(null), F.emptyState());
assert.deepStrictEqual(F.normalise({cards: 3}), F.emptyState());

console.log('OK');
```

```python
# tests/test_portal_folds_rules_node.py
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_portal_fold_rules():
    r = subprocess.run(["node", str(ROOT / "tests" / "test_portal_folds_rules.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_portal_folds_rules_node.py -q`
Expected: FAIL, `Cannot find module '../static/js/portal-folds.js'`.

- [ ] **Step 3: Implement**

```js
// static/js/portal-folds.js
// Fold rules for the client portal. Pure: no DOM, no network. The page glue in
// client-portal.html applies what these return. Spec:
// docs/superpowers/specs/2026-09-25-portal-folding-design.md
(function(){
  function emptyState(){ return {cards: {}, seen: [], before_fold_all: {}}; }

  function isMap(v){ return !!v && typeof v === 'object' && !Array.isArray(v); }

  function boolMap(v){
    var out = {};
    if(!isMap(v)) return out;
    Object.keys(v).forEach(function(k){ if(typeof v[k] === 'boolean') out[k] = v[k]; });
    return out;
  }

  function normalise(state){
    if(!isMap(state)) return emptyState();
    var bfa = {};
    if(isMap(state.before_fold_all)){
      Object.keys(state.before_fold_all).forEach(function(d){ bfa[d] = boolMap(state.before_fold_all[d]); });
    }
    return {
      cards: boolMap(state.cards),
      seen: Array.isArray(state.seen) ? state.seen.filter(function(d){ return typeof d === 'string'; }) : [],
      before_fold_all: bfa
    };
  }

  function copy(state){ return normalise(JSON.parse(JSON.stringify(state))); }

  function resolveDoor(state, ids){
    var out = {};
    ids.forEach(function(id, i){
      out[id] = Object.prototype.hasOwnProperty.call(state.cards, id) ? state.cards[id] : i > 0;
    });
    return out;
  }

  function markSeen(state, door){
    var s = copy(state);
    if(s.seen.indexOf(door) === -1) s.seen.push(door);
    return s;
  }

  function setCard(state, id, folded){
    var s = copy(state);
    s.cards[id] = !!folded;
    return s;
  }

  function foldAll(state, door, ids){
    var s = copy(state);
    if(!s.before_fold_all[door]) s.before_fold_all[door] = resolveDoor(s, ids);
    ids.forEach(function(id){ s.cards[id] = true; });
    return s;
  }

  function canRestore(state, door){ return !!(state.before_fold_all && state.before_fold_all[door]); }

  function restore(state, door){
    if(!canRestore(state, door)) return state;
    var s = copy(state);
    var saved = s.before_fold_all[door];
    Object.keys(saved).forEach(function(id){ s.cards[id] = saved[id]; });
    delete s.before_fold_all[door];
    return s;
  }

  function adopt(clientState){ return copy(normalise(clientState)); }

  var api = {emptyState: emptyState, normalise: normalise, resolveDoor: resolveDoor,
             markSeen: markSeen, setCard: setCard, foldAll: foldAll, restore: restore,
             canRestore: canRestore, adopt: adopt};
  if(typeof module !== 'undefined' && module.exports) module.exports = api;
  if(typeof window !== 'undefined') window.PortalFolds = api;
})();
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_portal_folds_rules_node.py -q`
Expected: PASS.

- [ ] **Step 5: Mutate**

Make `foldAll` always overwrite `before_fold_all[door]`. The "second Fold all" assertion must fail. Restore the code from a copy.

- [ ] **Step 6: Commit**

```bash
git add static/js/portal-folds.js tests/test_portal_folds_rules.js tests/test_portal_folds_rules_node.py
git commit -m "Portal folds: pure fold rules module"
```

---

### Task 5: Page glue (toggles, load, save, first visit)

**Files:**
- Modify: `static/client-portal.html`
  - After line `<script src="/static/js/portal-shell.js"></script>`, add `<script src="/static/js/portal-folds.js"></script>`.
  - CSS near `.card-fold{float:right;...}`: see Step 3.
  - Beside `function wireCardFolding()`: add the v2 functions.
  - At the render call site, find the comment "After the cards exist, and on EVERY render". Replace `wireCardFolding();` with `wirePortalFolds();`.
- Test: `tests/test_portal_folds_glue.py`, which runs a node harness like `tests/test_portal_card_folding.py`.

**Interfaces:**
- Consumes: `window.PortalFolds` from Task 4. `GET/PUT /api/portal/<token>/folds` from Task 2. The page-global `token` (`const token = seg;`). `data-fold-id`/`id` and `data-fold-skip` from Task 3. `section[data-door]` from the render.
- Produces: `wirePortalFolds()`, `_foldsV2` (`undefined` while loading, `false` for legacy, an object when live), `_foldIdOf(card)`, `_foldCardsByDoor()`, `_foldApplyAll()`, `_foldSave()`, `_foldFlush()`. Task 6 adds the bar buttons on top of these.

Behaviour to implement:

1. `wirePortalFolds()` runs on every render.
   - If `_foldsV2 === undefined` and no load is in flight, it starts `_foldLoad()` and returns. The page shows its default layout while the record loads.
   - If `_foldsV2 === false`, it calls the legacy `wireCardFolding()`.
   - Otherwise it calls `_foldApplyAll()`.
2. `_foldLoad()` GETs `/api/portal/<token>/folds` with `credentials: "same-origin"`.
   - A 404 sets `_foldsV2 = false`, then calls `wireCardFolding()`.
   - An OK response sets `_foldsV2 = {state: PortalFolds.normalise(j.state), staff: j.viewer === "staff"}`. It then removes every `localStorage` key starting `rm_fold_`, inside try/catch, and calls `_foldApplyAll()`.
   - Any other failure leaves `_foldsV2` undefined, so the next render retries.
3. `_foldIdOf(card)` returns `card.dataset.foldId || card.id || ""`.
4. `_foldCardsByDoor()` returns `{door: [card, ...]}` in document order. It includes only `.card` elements that meet three tests:
   - They have a non-empty fold id.
   - They have no `data-fold-skip`.
   - They have an `h2` or `h3` inside, which covers Review Focus 2.
   The door comes from `card.closest("[data-door]").dataset.door`. A card outside any door goes under `"home"`.
5. `_foldApplyAll()` runs for each door.
   - It first computes `ids`.
   - If the door is not in `state.seen` and at least one of its sections is visible, it sets `state = markSeen(state, door)` and schedules a save.
   - `r = resolveDoor(state, ids)`.
   - For each card, it ensures one `button.card-fold` exists as a direct child of the card. It sets the `is-folded` class, the button text (`Show` when folded, `Hide` when open) and `aria-expanded`.
   - The button's `aria-label` is `"Hide or show " + heading text`.
6. The existing delegated click handler in `wireCardFolding` branches first:
   - When `_foldsV2` is an object, it calls `_foldToggle(card)` and returns.
   - `_foldToggle` sets `state = setCard(state, id, !card.classList.contains("is-folded"))`, re-applies and schedules a save.
   - Wire the handler once, whichever path runs first. Extract the `document.addEventListener("click", ...)` block into `_foldWireClickOnce()`, called from both `wireCardFolding` and `wirePortalFolds`.
7. `_foldSave()` debounces for 600 ms, then PUTs `{state}` as JSON. On failure it does nothing, because the next save sends the full record. `_foldFlush()` sends any pending save at once with `keepalive: true`. It is wired once to `window.addEventListener("pagehide", _foldFlush)` (Review Focus 5).
8. `document.addEventListener("visibilitychange", ...)` fires when the page becomes visible and no save is pending. The handler re-GETs the record and re-applies it (Review Focus 1). Wire it once.

- [ ] **Step 1: Write the failing test**

Build `tests/test_portal_folds_glue.py` on the pattern of `tests/test_portal_card_folding.py`. Extract functions with its `_fn_source` helper, and run them in node against a fake DOM.

- Load the real module with `require('../static/js/portal-folds.js')` and assign it to `window.PortalFolds`.
- Replace `fetch` with a recorder that answers from a script:

```js
const calls = [];
let reply = {status: 200, body: {ok: true, viewer: 'client', state: {cards:{}, seen:[], before_fold_all:{}}}};
global.fetch = function(url, opts){
  calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body, keepalive: opts && opts.keepalive});
  return Promise.resolve({status: reply.status, ok: reply.status < 300, json: () => Promise.resolve(reply.body)});
};
```

- Replace `setTimeout` with a manual queue. The test flushes it to run the debounce.

Assertions, each its own block:

1. With a door `scans` holding cards `a`, `b` and `c`, all with headings, the first `wirePortalFolds()` makes exactly one GET to `/api/portal/TOKEN/folds`.
2. After the promise settles, all 3 cards have exactly one `.card-fold`. `a` is open, while `b` and `c` are folded. This is the first visit. `seen` includes `scans` and one PUT is queued.
3. A card with 0 height still gets a toggle. This is the 09-16 defect.
4. A card with `data-fold-skip` gets no toggle. A card with no heading gets no toggle.
5. Clicking `b`'s toggle opens it. After the debounce, one PUT is sent whose body has `cards.b === false`.
6. Three quick clicks send one PUT.
7. A second `wirePortalFolds()`, which simulates a re-render with new card elements, re-applies the same state. It makes no new GET.
8. A 404 reply sets the legacy path, and `wireCardFolding` runs. Stub it and assert it was called.
9. A 500 reply leaves `_foldsV2` undefined. The next `wirePortalFolds()` issues a new GET.
10. `_foldFlush()` with a pending save sends it at once with `keepalive: true`.
11. `rm_fold_x` in the fake localStorage is removed after a 200 load.

Also add a static assertion. The CSS must keep a folded card's toggle visible: `".card.is-folded > *:not(h2):not(h3):not(.card-fold){display:none}"` is in the page.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_portal_folds_glue.py -q`
Expected: FAIL, `function wirePortalFolds() not found`.

- [ ] **Step 3: Implement**

CSS: replace `.card-fold{float:right;` with a top-right absolute position. Keep the rest of that rule. Change the folded rule so the button stays visible.

```css
  .card{position:relative}
  .card-fold{position:absolute;top:10px;right:10px;background:none;border:0;color:var(--muted);cursor:pointer;
```
```css
  .card.is-folded > *:not(h2):not(h3):not(.card-fold){display:none}
```

Check that no existing `.card` rule sets `position` to something other than relative. Search `.card{` and `.card.`. Add `padding-right:64px` to `.card > h2:first-of-type, .card > h3:first-of-type` so a long heading does not run under the button.

Write the functions to the behaviour list above. Keep each function under about 30 lines. Put a comment block above them that cites the spec path and says why the record is server-side. This replaces the old localStorage rationale, which Glen's ruling of 2026-09-25 reversed. Update the legacy comment block to say it is the setting-off path only. Run `grep -n "localStorage is right for this" static/client-portal.html` after editing. The old reasoning must not survive as a live claim (see "Reversal leaves stale reasoning").

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_portal_folds_glue.py tests/test_portal_card_folding.py tests/test_portal_fold_ids.py tests/test_portal_folds_rules_node.py -q`
Expected: all pass. In `test_portal_card_folding.py`, update two things:
- The folded-CSS string assertion, to the new rule.
- `test_folding_runs_on_every_render`, so it asserts `wirePortalFolds();` sits after `wireEntityRefs`.

Keep the legacy behaviour test unchanged.

- [ ] **Step 5: Mutate**

1. Make `_foldApplyAll` skip cards with `offsetHeight === 0`. Assertion 3 must fail.
2. Remove the debounce. Assertion 6 must fail.

Restore the code from a copy each time.

- [ ] **Step 6: Commit**

```bash
git add static/client-portal.html tests/test_portal_folds_glue.py tests/test_portal_card_folding.py
git commit -m "Portal folds: page glue, server-backed toggles on every card"
```

---

### Task 6: Fold all / Restore bar and Match client's view

**Files:**
- Modify: `static/client-portal.html`, beside the Task 5 functions, plus CSS.
- Test: extend `tests/test_portal_folds_glue.py`.

**Interfaces:**
- Consumes: `_foldsV2`, `_foldCardsByDoor()`, `_foldApplyAll()`, `_foldSave()` from Task 5. `PortalFolds.foldAll/restore/canRestore/adopt` from Task 4.
- Produces: `_foldBars()` is called at the end of `_foldApplyAll()`. It also provides `_foldAllOrRestore(door)` and `_foldMatchClient()`.

Behaviour:

1. For each door with at least one foldable card, the first `section[data-door="<door>"]` in document order gets one `div.fold-bar[data-fold-bar="<door>"]` as its first child. The bar is created once per render and updated in place.
2. The bar holds `button.fold-bar-all`. Its text is `Restore` when `canRestore(state, door)` is true, else `Fold all`. A click calls `_foldAllOrRestore(door)`. That function applies `foldAll(state, door, ids)` or `restore(state, door)`, then re-applies and saves.
3. When `_foldsV2.staff` is true, the bar also holds `button.fold-bar-match` labelled `Match client's view`. A click runs `_foldMatchClient()`:
   - It GETs `/api/portal/<token>/folds?of=client`.
   - On success it sets `state = adopt(j.state)`, applies and saves. The save goes through the normal PUT, which the server writes to the staff row.
   - On failure it sets the button's `title` to "Could not load the client's view" and leaves state alone.
4. No client-visible text contains an em dash or all capitals.

CSS: `.fold-bar{display:flex;gap:8px;justify-content:flex-end;margin:0 0 8px}`. The buttons reuse the existing small button style. Search the page for `.btn.small` or the style `calendar-fold` uses, and reuse it.

- [ ] **Step 1: Write the failing tests** (append to the node harness)

1. A client view shows one bar per door with cards, with `Fold all` and no match button.
2. Clicking `Fold all` folds `a`, `b` and `c` and changes the label to `Restore`. Clicking `Restore` returns the exact earlier states and the label `Fold all`.
3. Re-rendering does not duplicate the bar.
4. A staff view (`viewer: 'staff'`) shows the match button. Clicking it makes a GET ending `?of=client`. After it settles, the card states equal the client record the fake returned. The next PUT body equals that record, and its URL has no `of=`.
5. A failed match GET leaves the state unchanged.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_portal_folds_glue.py -q`
Expected: the new blocks fail with no `.fold-bar` found.

- [ ] **Step 3: Implement** to the behaviour list.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_portal_folds_glue.py -q`
Expected: PASS.

- [ ] **Step 5: Mutate**

Show the match button for clients too. Assertion 1 must fail. Restore the code from a copy.

- [ ] **Step 6: Commit**

```bash
git add static/client-portal.html tests/test_portal_folds_glue.py
git commit -m "Portal folds: Fold all / Restore bar and staff Match client's view"
```

---

### Task 7: Browser check on a real rendered portal

**Files:**
- Create: `tests/test_portal_folds_e2e.py`

**Interfaces:**
- Consumes: everything above. The live-server pattern is from `tests/test_atlas_e2e.py`.

The test has five parts:

1. **Fixture.**
   - Point `LOG_DB` at `tmp_path` and call `_init_auth_tables()`.
   - Set `CONSOLE_SECRET` to `"test-secret"` and `appmod._PORTAL_SHELL_ENABLED` to True with `monkeypatch.setattr`.
   - Set `PORTAL_FOLDS_V2=1`.
   - Seed one portal with made-up content through `client_portal.upsert_portal`. Use the email `fold-test@example.com` and give it enough fields that each door renders at least one card.
   - Start `werkzeug.serving.make_server` on port 0 in a thread.
   - Do not set `DATA_DIR`, so the scheduler stays off.
2. **For each width**, 390x844 and 1280x900, open `/portal/<token>` and wait for `.card`. Then, for each door in `window.PortalShell.DOORS`:
   - Call `showDoor(key)` with `page.evaluate`.
   - Count the visible foldable cards: `.card` with a fold id, no `data-fold-skip`, and a heading, inside visible `section[data-door=key]`.
   - Count the `.card-fold` buttons in the same set.
   - Assert the two counts are equal.
   - Across all doors, assert the total is at least 7. Zero anywhere fails.
3. **Across the page**, assert every rendered fold id is unique, and no card has two `.card-fold` buttons. This covers Review Focus 3.
4. **Persistence.**
   - On Scans, click the first card's toggle.
   - Wait for the PUT response with `page.wait_for_response`.
   - Reload the page and open Scans again.
   - Assert that card is still folded.
   - Open a fresh browser context with no shared storage. This stands in for a second device. The same card must be folded there too.
5. **Staff isolation.**
   - Load the same portal in a new context with the header `X-Console-Key: test-secret`, set through `extra_http_headers`.
   - Fold a different card.
   - In the client context, reload and assert that card is unchanged.
   - In the staff context, click `Match client's view` and assert the staff view now matches the client's.

Start the file with `pytest.importorskip("playwright.sync_api")`. If chromium is not installed locally, say so and stop. Do not install it without asking.

- [ ] **Step 1: Write the test** as above.

- [ ] **Step 2: Run it against the Task 6 branch**

Run: `python3 -m pytest tests/test_portal_folds_e2e.py -q -s`
Expected: PASS. If the portal does not render with synthetic data, report the console errors with a screenshot saved in the scratchpad. Do not guess at fixes to unrelated render code.

- [ ] **Step 3: Prove it catches the 09-16 defect**

Temporarily make `_foldCardsByDoor` skip cards with `offsetHeight === 0`, and run the test. It must fail on the hidden doors. Restore the code from a copy.

- [ ] **Step 4: Save desktop and phone screenshots of Scans, folded and unfolded**

Save them under `~/AI-Training/00 System/primary/portal-review-2026-09-25/folds-v2/` for Glen. They are not committed to the repo.

- [ ] **Step 5: Commit**

```bash
git add tests/test_portal_folds_e2e.py
git commit -m "Portal folds: browser check across seven doors, two widths"
```

---

### Task 8: Review, merge, deploy, staged switch-on

This task is operational. It has no new code.

- [ ] **Step 1: Run the named test set** on the branch, then on `origin/main`, and compare failing sets, not counts.

```bash
TESTS=(tests/test_portal_folds_store.py tests/test_portal_folds_routes.py tests/test_portal_fold_ids.py
       tests/test_portal_folds_rules_node.py tests/test_portal_folds_glue.py tests/test_portal_folds_e2e.py
       tests/test_portal_card_folding.py tests/test_client_portal_routes.py tests/test_portal_shell_flag.py)
python3 -m pytest "${TESTS[@]}" -q
```

- [ ] **Step 2: Three blind review rounds.** Each round uses fresh reviewers who see the diff and the spec, not earlier findings. Give each reviewer one question:
  1. Can any request write or read another viewer's record?
  2. Does any card or door end up with no toggle, two toggles, or a hidden toggle?
  3. What does a client see if saving or loading fails?

  Verify each finding before fixing it. Re-run Step 1 after the fixes.

- [ ] **Step 3: Push and open the PR.** Include the spec path, the review summary and the screenshots path. End the body with the attribution line. Ask Glen to merge it in the platform tab.

- [ ] **Step 4: After Glen merges, deploy and verify it landed.** Auto-deploy is off. Follow `SOPs/shipping-code.html`. Check a live marker: the served `/static/js/portal-folds.js` returns 200.

- [ ] **Step 5: Create the test portal.** Mint it through `/admin/portal/upsert` with made-up content and the email `fold-test@remedymatch.test`. Glen agreed to a made-up test portal on 2026-09-25. Set `PORTAL_FOLDS_V2_EMAILS=fold-test@remedymatch.test` in Doppler `prd`, then redeploy so the process reads it. Check live: GET `/api/portal/<test token>/folds` returns 200, and the same call on a real portal returns 404.

- [ ] **Step 6: Ask Glen to look at the test portal on his phone and desktop.** After his yes, set `PORTAL_FOLDS_V2=1` in Doppler `prd`, redeploy and verify on one real portal as staff. Clear `PORTAL_FOLDS_V2_EMAILS`.

- [ ] **Step 7: Update the pillar's records.** Update `platform/STATE.md` and `SESSION_LOG.md`. Update the memory `project_portal_ease_of_use.md` with the live state.
