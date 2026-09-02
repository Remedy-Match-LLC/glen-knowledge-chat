# Client Portal Shell and IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the client portal's 20-tile hub with a seven-door rail, move the concierge chat to the top of the page, and split the 27 cards currently stacked in the `current` panel out to the door that owns each one.

**Architecture:** The pure parts of the shell (the door map, panel membership, rail markup, chat intent routing) move into a new `static/js/portal-shell.js` following the repo's existing pattern of a dual-module file that the browser loads with a `<script src>` and node unit-tests with `require`. The page keeps `showTab(panel)` for deep links and gains `showDoor(key)`, which reveals every `[data-panel]` whose `data-door` matches. Because a door reveals several sections stacked, splitting the `current` panel later is a matter of giving new sections the right `data-door`, not rewriting the router. Everything ships behind `PORTAL_SHELL_ENABLED` with `PORTAL_HUB_ENABLED` left untouched until the final task.

**Tech Stack:** Vanilla JS (no framework, no build step), Flask, sqlite/Postgres via `dashboard/db.py`, pytest, node's built-in `assert` for JS unit tests.

**Spec:** `docs/superpowers/specs/2026-09-01-portal-shell-ia-design.md`

## Global Constraints

- **Copy rules.** Never use an em dash (`--` or `—`); use a comma or a period. No ALL CAPS in client-facing copy. Never prefix anything with "Hook:". These already appear in `dashboard/portal_concierge.py`'s system prompt and apply to every string a client can see.
- **Client, not patient.** Client-facing copy says "client".
- **Flags are Doppler-first.** Doppler is the source of truth and Render mirrors it. Never set an environment variable on Render directly. Flipping a flag in production is two deploys: merge the code, then set the value in Doppler `prd`.
- **Flag state is checked in `prd`, not `dev`.** Unset in dev does not mean off in prod.
- **`PORTAL_HUB_ENABLED` stays on and unchanged** until Task 12. Every task before it must leave the existing hub fully working.
- **No new data in the portal payload.** `dashboard/portal_view.get_portal_view` gains exactly one new key, `shell_enabled`. If a door appears to need data the payload lacks, stop and raise it rather than fetching around it client-side.
- **Do not run the bare full pytest suite locally.** It sends real email. Run the named test files given in each task.
- **Cite code by content, not only by line number.** Line numbers in this worktree drift from other checkouts; every task below gives a searchable anchor string alongside the line.

## File Structure

| File | Responsibility |
|---|---|
| `static/js/portal-shell.js` (new) | Pure: the door map, panel membership, rail and header markup, chat intent routing. No DOM reads, no fetch. |
| `tests/test_portal_shell.js` (new) | Node unit tests for the above. |
| `static/client-portal.html` (modify) | Mounts the rail, adds `data-door` to the 21 sections, adds `showDoor`, moves the chat composer to the top, and later hosts the split-out sections. |
| `ci/run-tests.sh` (modify) | Runs the node tests, which nothing runs today. |
| `dashboard/portal_view.py` (modify) | Adds `shell_enabled` to the payload. |
| `app.py` (modify) | Reads `PORTAL_SHELL_ENABLED` and passes it through. |
| `tests/test_portal_shell_flag.py` (new) | pytest coverage for the flag plumbing and the chat-at-top regression. |

---

### Task 1: Make the node tests actually run

The repo has 10 `tests/*.js` files. All 10 pass. Nothing runs them: `ci/run-tests.sh` ends with `exec python3 scripts/ci_check.py`, which is pytest only. Every JS test this plan writes is decorative until this is fixed, so it goes first.

**Files:**
- Modify: `ci/run-tests.sh` (anchor: the line `exec python3 scripts/ci_check.py`)

**Interfaces:**
- Consumes: nothing
- Produces: a CI gate that fails when any `tests/*.js` file throws. Later tasks rely on this.

- [ ] **Step 1: Confirm the tests pass now and are unrun**

```bash
cd /tmp/wt-deploy-chat-9a896303
for f in tests/*.js; do printf "%-46s " "$f"; node "$f" >/dev/null 2>&1 && echo PASS || echo FAIL; done
grep -c "node" ci/run-tests.sh
```

Expected: 10 PASS, and `0` occurrences of `node` in the runner.

- [ ] **Step 2: Add a deliberately failing JS test to prove the gate bites**

Create `tests/test_ci_gate_probe.js`:

```js
// Temporary probe. Deleted in step 5 of this task.
const assert = require('assert');
assert.strictEqual(1, 2, 'probe: the node gate is wired');
```

- [ ] **Step 3: Add the node run to the CI script**

In `ci/run-tests.sh`, insert immediately **before** the final `exec python3 scripts/ci_check.py` line:

```bash
# --- front-end unit tests --------------------------------------------------------------
# tests/*.js are plain node scripts using the built-in assert module (see
# tests/test_portal_documents_tile.js for the pattern). They were present but unrun for
# months: this runner was pytest-only, so a broken renderer shipped green. ubuntu-latest
# ships node, and the files have no dependencies, so there is nothing to install.
if command -v node >/dev/null 2>&1; then
  for f in tests/*.js; do
    [ -e "$f" ] || continue
    echo "node $f"
    node "$f" || exit 1
  done
else
  echo "node not found, skipping front-end unit tests" >&2
fi
```

- [ ] **Step 4: Run the gate and verify it fails on the probe**

```bash
cd /tmp/wt-deploy-chat-9a896303 && bash -c '
for f in tests/*.js; do echo "node $f"; node "$f" || exit 1; done'
```

Expected: FAIL on `tests/test_ci_gate_probe.js` with `probe: the node gate is wired`.

- [ ] **Step 5: Delete the probe and re-run**

```bash
rm tests/test_ci_gate_probe.js
cd /tmp/wt-deploy-chat-9a896303 && for f in tests/*.js; do node "$f" || exit 1; done && echo ALL PASS
```

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add ci/run-tests.sh
git commit -m "ci: run the front-end unit tests

tests/*.js has held 10 passing node test files that nothing executed. The
runner was pytest-only, so a broken renderer could ship green."
```

---

### Task 2: The door map

**Files:**
- Create: `static/js/portal-shell.js`
- Create: `tests/test_portal_shell.js`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DOORS`, an array of `{key, label, icon, panels}`; `icon` is an SVG path-data string
  - `panelsForDoor(key)` → `string[]` (empty array for an unknown key)
  - `doorForPanel(panel)` → door key string, or `null`
  - `allPanels()` → `string[]`, every panel across every door

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_shell.js`:

```js
// tests/test_portal_shell.js
// Run: node tests/test_portal_shell.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { DOORS, panelsForDoor, doorForPanel, allPanels } = require('../static/js/portal-shell.js');

// seven doors, in rail order
assert.strictEqual(DOORS.length, 7);
assert.deepStrictEqual(DOORS.map(d => d.key),
  ['home', 'scans', 'solutions', 'remedies', 'billing', 'learn', 'account']);
DOORS.forEach(d => {
  assert.ok(d.label && typeof d.label === 'string', d.key + ' needs a label');
  assert.ok(d.icon && d.icon.indexOf('<') === -1, d.key + ' icon is path data, not markup');
  assert.ok(Array.isArray(d.panels) && d.panels.length, d.key + ' needs panels');
});

// no em dashes in any client-facing label
DOORS.forEach(d => {
  assert.ok(!/—|--/.test(d.label), d.key + ' label must not contain an em dash');
});

// membership resolves both ways
assert.deepStrictEqual(panelsForDoor('billing'), ['orders']);
assert.deepStrictEqual(panelsForDoor('nope'), []);
assert.strictEqual(doorForPanel('voice'), 'scans');
assert.strictEqual(doorForPanel('cart'), 'remedies');
assert.strictEqual(doorForPanel('nope'), null);

// every panel belongs to exactly one door
const seen = {};
allPanels().forEach(p => {
  assert.ok(!seen[p], p + ' is claimed by more than one door');
  seen[p] = true;
});

// THE load-bearing assertion: the door map and the page agree, as SETS.
// A door pointing at a panel that does not exist silently bounces to the hub,
// and a panel no door claims is unreachable. Counting is not enough; compare
// the sets so both directions fail loudly.
const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const inPage = Array.from(new Set(
  (page.match(/data-panel="[a-z]+"/g) || []).map(s => s.slice(13, -1))
)).sort();
const inMap = allPanels().slice().sort();
assert.deepStrictEqual(inMap, inPage,
  'door map and [data-panel] sections disagree.\n  map: ' + inMap.join(',') +
  '\n  page: ' + inPage.join(','));

console.log('test_portal_shell: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell.js`
Expected: FAIL with `Cannot find module '../static/js/portal-shell.js'`

- [ ] **Step 3: Write the implementation**

Create `static/js/portal-shell.js`:

```js
// Portal shell: the seven doors, their panel membership, and the rail markup.
//
// Pure by design (no DOM reads, no fetch) so node can unit-test it, following the
// same dual-module pattern as static/js/portal-documents.js. The page loads it with
// a <script src> and calls into it; tests require() it.
//
// A door reveals every [data-panel] section that carries its key as data-door, so a
// door can hold several sections stacked. That is what lets the oversized `current`
// panel be split into sections later without touching the router.

var DOORS = [
  { key: 'home', label: 'Home',
    icon: 'M3 10.5 12 3l9 7.5M5.5 9.5V21h13V9.5',
    panels: ['hub'] },
  { key: 'scans', label: 'Scans & Reports',
    icon: 'M2 12h3l2.5-7 3 14 3-10.5L16 15l2-3h4',
    panels: ['current', 'voice', 'history'] },
  { key: 'solutions', label: 'Find Solutions',
    icon: 'M15.5 15.5 21 21M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0',
    panels: ['shop'] },
  { key: 'remedies', label: 'My Remedies',
    icon: 'M10 2h4v4l3.5 5.5A4 4 0 0 1 14 18h-4a4 4 0 0 1-3.5-6.5L10 6zM6.8 13h10.4',
    panels: ['remedies', 'oasis', 'cart'] },
  { key: 'billing', label: 'Billing',
    icon: 'M6 2.5h12v19l-3-2-3 2-3-2-3 2zM9.5 8h5M9.5 12h5',
    panels: ['orders'] },
  { key: 'learn', label: 'Learn & Ask',
    icon: 'M3 4.5h6a3 3 0 0 1 3 3v12a2.5 2.5 0 0 0-2.5-2.5H3zM21 4.5h-6a3 3 0 0 0-3 3v12a2.5 2.5 0 0 1 2.5-2.5H21z',
    panels: ['ask', 'bodymap', 'classes', 'calendar'] },
  { key: 'account', label: 'Account',
    icon: 'M15.6 8a3.6 3.6 0 1 1-7.2 0 3.6 3.6 0 0 1 7.2 0M4.5 20.5a7.5 7.5 0 0 1 15 0',
    panels: ['account', 'photo', 'intake', 'records', 'refer', 'referrals', 'offers', 'finder'] }
];

function panelsForDoor(key) {
  for (var i = 0; i < DOORS.length; i++) {
    if (DOORS[i].key === key) return DOORS[i].panels.slice();
  }
  return [];
}

function doorForPanel(panel) {
  for (var i = 0; i < DOORS.length; i++) {
    if (DOORS[i].panels.indexOf(panel) !== -1) return DOORS[i].key;
  }
  return null;
}

function allPanels() {
  var out = [];
  DOORS.forEach(function (d) { d.panels.forEach(function (p) { out.push(p); }); });
  return out;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels
  };
}
if (typeof window !== 'undefined') {
  window.PortalShell = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_portal_shell.js`
Expected: `test_portal_shell: ok`

If the set comparison fails, the door map is wrong, not the test. The 21 panels are: `account ask bodymap calendar cart classes current finder history hub intake oasis offers orders photo records refer referrals remedies shop voice`.

- [ ] **Step 5: Commit**

```bash
git add static/js/portal-shell.js tests/test_portal_shell.js
git commit -m "feat(portal): seven-door map for the portal shell

The door map is asserted against the page's [data-panel] sections as sets, so
a door pointing at a missing panel and a panel no door claims both fail loudly."
```

---

### Task 3: Rail and phone-header markup

**Files:**
- Modify: `static/js/portal-shell.js`
- Modify: `tests/test_portal_shell.js`

**Interfaces:**
- Consumes: `DOORS` from Task 2
- Produces:
  - `renderRail(activeDoor, opts)` → HTML string. `opts` is `{open: bool}`. Each door is a `<button class="rail-item" data-door="KEY">`.
  - `renderPhoneHeader()` → HTML string containing `<button class="shell-burger" data-shell-open="1">`
  - `escapeHtml(s)` → string

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portal_shell.js`, above the final `console.log`:

```js
const { renderRail, renderPhoneHeader, escapeHtml } = require('../static/js/portal-shell.js');

const rail = renderRail('billing', { open: false });
// one control per door, each addressable by its key
DOORS.forEach(d => {
  assert.ok(rail.indexOf('data-door="' + d.key + '"') !== -1, 'rail is missing ' + d.key);
  assert.ok(rail.indexOf('>' + d.label + '<') !== -1, 'rail is missing the label for ' + d.key);
});
// exactly one active door, and it is the one asked for
assert.strictEqual((rail.match(/is-active/g) || []).length, 1);
assert.ok(/data-door="billing"[^>]*class="[^"]*is-active/.test(rail) ||
          /class="[^"]*is-active[^"]*"[^>]*data-door="billing"/.test(rail));
// collapsed by default, open when asked
assert.ok(rail.indexOf('is-open') === -1);
assert.ok(renderRail('home', { open: true }).indexOf('is-open') !== -1);
// labels are always in the DOM, hidden by CSS, so screen readers keep them
assert.ok(rail.indexOf('aria-label="Portal sections"') !== -1);

const header = renderPhoneHeader();
assert.ok(header.indexOf('data-shell-open="1"') !== -1);
assert.ok(header.indexOf('aria-label="Open the menu"') !== -1);

// escaping is real, not decorative
assert.strictEqual(escapeHtml('<img src=x onerror=alert(1)>'),
  '&lt;img src=x onerror=alert(1)&gt;');
assert.strictEqual(escapeHtml(null), '');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell.js`
Expected: FAIL with `TypeError: renderRail is not a function`

- [ ] **Step 3: Write the implementation**

In `static/js/portal-shell.js`, add before the export block:

```js
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderRail(activeDoor, opts) {
  opts = opts || {};
  var items = DOORS.map(function (d) {
    var active = (d.key === activeDoor) ? ' is-active' : '';
    return '<button type="button" class="rail-item' + active + '" data-door="' +
      escapeHtml(d.key) + '">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + escapeHtml(d.icon) + '"/></svg>' +
      '<span>' + escapeHtml(d.label) + '</span></button>';
  }).join('');
  return '<nav class="portal-rail' + (opts.open ? ' is-open' : '') +
    '" id="portalRail" aria-label="Portal sections">' + items +
    '<button type="button" class="rail-item rail-toggle" data-shell-toggle="1" ' +
    'aria-label="Open and close the menu">' +
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>' +
    '<span>Close</span></button></nav>';
}

function renderPhoneHeader() {
  return '<header class="shell-bar" id="shellBar">' +
    '<button type="button" class="shell-burger" data-shell-open="1" aria-label="Open the menu">' +
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>' +
    '</button><span class="shell-title">Your healing home</span></header>';
}
```

Add all three to both the `module.exports` object and the `window.PortalShell` object.

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_portal_shell.js`
Expected: `test_portal_shell: ok`

- [ ] **Step 5: Commit**

```bash
git add static/js/portal-shell.js tests/test_portal_shell.js
git commit -m "feat(portal): rail and phone-header markup"
```

---

### Task 4: The flag

**Files:**
- Modify: `app.py` (anchor: `_PORTAL_HUB_ENABLED = os.environ.get("PORTAL_HUB_ENABLED"`, near line 6768)
- Modify: `app.py` (anchor: `view = _pv.get_portal_view(cx, ident.person_id,`, near line 32963)
- Modify: `dashboard/portal_view.py` (anchors: the `hub_enabled=False,` keyword in `get_portal_view`'s signature, and `"hub_enabled": bool(hub_enabled),` in the returned dict)
- Create: `tests/test_portal_shell_flag.py`

**Interfaces:**
- Consumes: nothing
- Produces: `shell_enabled` boolean in the portal payload; `_PORTAL_SHELL_ENABLED` in `app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_shell_flag.py`:

```python
"""The shell flag reaches the portal payload, and does so independently of the hub flag.

Both flags on at once is a real state during rollout: the shell ships dark while the
hub is still serving clients. A test that only checks the shell flag in isolation would
miss the two of them being wired to the same value.
"""
import sqlite3

from dashboard import portal_view as pv


def _person(cx):
    cx.executescript("""
        CREATE TABLE people(id INTEGER PRIMARY KEY, email TEXT, name TEXT,
            first_name TEXT, last_name TEXT, roles TEXT,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zip TEXT, country TEXT);
        INSERT INTO people(id,email,name,roles) VALUES(1,'c@example.com','A Client','["client"]');
    """)
    return 1


def test_shell_flag_defaults_off():
    with sqlite3.connect(":memory:") as cx:
        pid = _person(cx)
        view = pv.get_portal_view(cx, pid)
    assert view["shell_enabled"] is False


def test_shell_flag_is_independent_of_the_hub_flag():
    with sqlite3.connect(":memory:") as cx:
        pid = _person(cx)
        both = pv.get_portal_view(cx, pid, hub_enabled=True, shell_enabled=True)
        shell_only = pv.get_portal_view(cx, pid, hub_enabled=False, shell_enabled=True)
        hub_only = pv.get_portal_view(cx, pid, hub_enabled=True, shell_enabled=False)
    assert (both["hub_enabled"], both["shell_enabled"]) == (True, True)
    assert (shell_only["hub_enabled"], shell_only["shell_enabled"]) == (False, True)
    assert (hub_only["hub_enabled"], hub_only["shell_enabled"]) == (True, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_portal_shell_flag.py -v`
Expected: FAIL with `TypeError: get_portal_view() got an unexpected keyword argument 'shell_enabled'`

- [ ] **Step 3: Write the implementation**

In `dashboard/portal_view.py`, add the keyword to `get_portal_view`'s signature next to `hub_enabled=False,`:

```python
                    hub_enabled=False, shell_enabled=False, health_profile_enabled=False,
```

and add to the returned dict, directly under `"hub_enabled": bool(hub_enabled),`:

```python
        "shell_enabled": bool(shell_enabled),
```

In `app.py`, next to the other portal flags:

```python
# The seven-door shell. Ships dark: PORTAL_HUB_ENABLED stays on and unchanged until the
# shell has been walked on a real portal. Both flags on at once is the expected rollout
# state, not a conflict, and the page prefers the shell when both are set.
_PORTAL_SHELL_ENABLED = os.environ.get("PORTAL_SHELL_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
```

At the `get_portal_view` call site, add the argument alongside `hub_enabled=_PORTAL_HUB_ENABLED,`:

```python
                                       shell_enabled=_PORTAL_SHELL_ENABLED,
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_portal_shell_flag.py tests/test_portal_hub_flag.py -v
```

Expected: PASS, and the existing hub-flag tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app.py dashboard/portal_view.py tests/test_portal_shell_flag.py
git commit -m "feat(portal): PORTAL_SHELL_ENABLED flag, dark by default"
```

---

### Task 5: Mount the rail and route by door

**Files:**
- Modify: `static/client-portal.html` (anchors: `<script src="/static/js/portal-library.js"></script>` for the script tag; `function showTab(name, options){` near line 1071; the panel wrap beginning `<section data-panel="hub">` near line 4698)

**Interfaces:**
- Consumes: `window.PortalShell` from Tasks 2 and 3
- Produces:
  - `showDoor(key)` on `window`. Hides every `[data-panel]`, reveals those whose `data-door` matches, in DOM order
  - `panelShown(name)` on `window`. The per-panel side-effect dispatch that `showTab` used to do inline
  - every `<section data-panel="x">` carries `data-door="..."`

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_shell_wiring.js`:

```js
// tests/test_portal_shell_wiring.js
// Run: node tests/test_portal_shell_wiring.js
//
// Asserts on the page source only for structural facts that cannot be executed
// without a DOM (which sections exist, and what door each declares). The behaviour
// of showDoor is covered by executing it, below.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { DOORS, doorForPanel } = require('../static/js/portal-shell.js');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

// every section declares the door the map assigns it
const sections = page.match(/<section[^>]*data-panel="[a-z]+"[^>]*>/g) || [];
assert.ok(sections.length >= 21, 'expected at least 21 panel sections, found ' + sections.length);
sections.forEach(function (tag) {
  const panel = /data-panel="([a-z]+)"/.exec(tag)[1];
  const door = /data-door="([a-z]+)"/.exec(tag);
  assert.ok(door, panel + ' section is missing data-door');
  assert.strictEqual(door[1], doorForPanel(panel),
    panel + ' declares door ' + door[1] + ' but the map says ' + doorForPanel(panel));
});

// the shell script is loaded before the inline code that calls into it
const shellTag = page.indexOf('/static/js/portal-shell.js');
assert.ok(shellTag !== -1, 'portal-shell.js is not loaded by the page');
assert.ok(shellTag < page.indexOf('function showDoor'), 'portal-shell.js loads too late');

// --- behaviour: extract showDoor and run it against a fake DOM ---------------------
// Extracted and executed rather than regex-matched. A source regex would pass on a
// commented-out implementation.
const src = /function showDoor\(key\)\{[\s\S]*?\n\}/.exec(page);
assert.ok(src, 'showDoor not found in the page');

const panels = DOORS.reduce(function (all, d) {
  return all.concat(d.panels.map(function (p) {
    return { dataset: { panel: p, door: d.key }, hidden: false };
  }));
}, []);
const shown = [];
const document = {
  querySelectorAll: function () { return panels; },
  querySelector: function () { return null; }
};
const window = { PortalShell: require('../static/js/portal-shell.js') };
const sessionStorage = { setItem: function () {} };
const panelShown = function (n) { shown.push(n); };
eval(src[0] + '\nshowDoor("remedies");');

const visible = panels.filter(function (p) { return !p.hidden; })
  .map(function (p) { return p.dataset.panel; });
assert.deepStrictEqual(visible.sort(), ['cart', 'oasis', 'remedies'],
  'a door must reveal all of its panels and nothing else');
assert.deepStrictEqual(shown.sort(), ['cart', 'oasis', 'remedies'],
  'every newly visible panel must get its panelShown side effect');

console.log('test_portal_shell_wiring: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell_wiring.js`
Expected: FAIL with `portal-shell.js is not loaded by the page`

- [ ] **Step 3: Write the implementation**

Load the script alongside the others:

```html
<script src="/static/js/portal-shell.js"></script>
```

Pull the per-panel side effects out of `showTab` into their own function, and add `showDoor` next to it:

```js
// The per-panel side effects that showTab used to run inline. A door reveals several
// panels at once, so each newly visible panel needs its own call.
function panelShown(name){
  if (name === 'health') loadHealthSuggestions();
  if (name === 'cart') loadCart();
  if (name === 'orders') renderOrdersBasket();
  if (name === 'intake') initPortalIntakeCard(true);
}

// Reveal every section belonging to `key`, in DOM order. Unknown keys fall back to
// home, matching showTab's existing behaviour for an unknown panel.
function showDoor(key){
  var shell = window.PortalShell;
  if(!shell || !shell.panelsForDoor(key).length){ key = 'home'; }
  var members = shell.panelsForDoor(key);
  document.querySelectorAll('[data-panel]').forEach(function(p){
    var inDoor = members.indexOf(p.dataset.panel) !== -1;
    p.hidden = !inDoor;
    if(inDoor) panelShown(p.dataset.panel);
  });
  document.querySelectorAll('.rail-item[data-door]').forEach(function(b){
    b.classList.toggle('is-active', b.dataset.door === key);
  });
  try{ sessionStorage.setItem('rm_portal_door', key); }catch(e){}
  if(typeof window.mentorPageChanged === "function") window.mentorPageChanged(key);
}
window.showDoor = showDoor;
window.panelShown = panelShown;
```

Replace the body of `showTab`'s side-effect block with a call to `panelShown(name)` so the two paths cannot drift, and make `showTab` open the panel's door first so a deep link still lands somewhere visible:

```js
  if (window.PortalShell && document.querySelector('[data-door]')) {
    var door = window.PortalShell.doorForPanel(name);
    if (door) { showDoor(door); }
    var target = document.querySelector('[data-panel="'+name+'"]');
    if (target && target.scrollIntoView) target.scrollIntoView({block:'start'});
    return;
  }
```

Place that block immediately after `showTab`'s existing fallback line so the legacy hub path is untouched when the shell is off.

Add `data-door` to all 21 sections. Each `<section data-panel="X"` gains `data-door="Y"` using the map from Task 2:

| `data-panel` | `data-door` |
|---|---|
| `hub` | `home` |
| `current`, `voice`, `history` | `scans` |
| `shop` | `solutions` |
| `remedies`, `oasis`, `cart` | `remedies` |
| `orders` | `billing` |
| `ask`, `bodymap`, `classes`, `calendar` | `learn` |
| `account`, `photo`, `intake`, `records`, `refer`, `referrals`, `offers`, `finder` | `account` |

Mount the rail when the flag is on, immediately inside the portal wrapper, and delegate clicks:

```js
if (v.shell_enabled) {
  document.getElementById('portalShellMount').innerHTML =
    window.PortalShell.renderPhoneHeader() + window.PortalShell.renderRail('home', {open:false});
  document.addEventListener('click', function(e){
    var d = e.target.closest('.rail-item[data-door]');
    if (d) { showDoor(d.dataset.door); return; }
    if (e.target.closest('[data-shell-toggle],[data-shell-open],.shell-scrim')) {
      document.getElementById('portalRail').classList.toggle('is-open');
    }
  });
  showDoor('home');
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_shell_wiring.js
node tests/test_portal_shell.js
python3 -m pytest tests/test_portal_hub_flag.py tests/test_client_portal_routes.py tests/test_portal_load_resilience.py -v
```

Expected: all PASS. The hub tests passing is the point: the shell is dark, so nothing a client sees has changed yet.

- [ ] **Step 5: Commit**

```bash
git add static/client-portal.html tests/test_portal_shell_wiring.js
git commit -m "feat(portal): mount the rail and route by door behind the shell flag"
```

---

### Task 6: Phone drawer, desktop rail

**Files:**
- Modify: `static/client-portal.html` (CSS, anchor: `.hub-tiles { display:grid;`)

**Interfaces:**
- Consumes: `.portal-rail`, `.shell-bar`, `.rail-item` from Tasks 3 and 5
- Produces: the responsive behaviour. No new JS.

The decision from the spec: below 760px the rail is hidden and reached through the header's menu button, opening as an overlay drawer. At 760px and above the rail is always on screen, collapsed to icons, and widens in place when opened.

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_shell_responsive.js`:

```js
// tests/test_portal_shell_responsive.js
// Run: node tests/test_portal_shell_responsive.js
//
// The rules live in a media query, so assert on the parsed rule text rather than on
// a rendered box. Render verification against a real portal happens in Task 12.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const css = (page.match(/<style>[\s\S]*?<\/style>/g) || []).join('\n');

function ruleFor(selector, within) {
  const scope = within ? (new RegExp('@media[^{]*' + within + '[^{]*\\{([\\s\\S]*?)\\n  \\}')
    .exec(css) || [, ''])[1] : css;
  const m = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
    '\\s*\\{([^}]*)\\}').exec(scope);
  return m ? m[1] : '';
}

// phone: the bar is shown, the rail is off-canvas until opened
const phoneBar = ruleFor('.shell-bar', 'max-width:760px');
assert.ok(/display\s*:\s*flex/.test(phoneBar), 'the phone header must show under 760px');
const phoneRail = ruleFor('.portal-rail', 'max-width:760px');
assert.ok(/position\s*:\s*fixed|position\s*:\s*absolute/.test(phoneRail),
  'the phone rail must overlay, not take layout width');
assert.ok(/translateX\(-100%\)|left\s*:\s*-/.test(phoneRail),
  'the phone rail must start off-canvas');

// desktop: the bar is gone, the rail holds layout width
assert.ok(/display\s*:\s*none/.test(ruleFor('.shell-bar')),
  'the header must be hidden at desktop width by default');
const deskRail = ruleFor('.portal-rail');
assert.ok(/width\s*:\s*52px/.test(deskRail), 'the desktop rail is 52px collapsed');
assert.ok(/\.portal-rail\.is-open[^{]*\{[^}]*width\s*:\s*176px/.test(css),
  'the open rail is 176px');

// reduced motion is honoured
assert.ok(/prefers-reduced-motion/.test(css));

console.log('test_portal_shell_responsive: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell_responsive.js`
Expected: FAIL with `the phone header must show under 760px`

- [ ] **Step 3: Write the implementation**

Add to the page's `<style>` block, after the `.hub-tiles` rules:

```css
  /* Seven-door shell. Desktop first: the rail holds layout width and widens in place.
     Under 760px it leaves the flow entirely, because a permanent 44px rail leaves
     331px of content and remedy names already truncate at that width. */
  .shell-bar{display:none}
  .portal-rail{
    position:fixed; top:0; bottom:0; left:0; width:52px; z-index:40;
    display:flex; flex-direction:column; gap:2px; padding:8px 0; overflow:hidden;
    background:var(--card2); border-right:1px solid var(--line);
    transition:width .22s ease;
  }
  .portal-rail.is-open{width:176px}
  .rail-item{
    display:flex; align-items:center; gap:10px; padding:9px 17px; width:100%;
    background:none; border:0; border-left:2px solid transparent; color:var(--muted);
    font:inherit; text-align:left; white-space:nowrap; cursor:pointer;
  }
  .rail-item svg{flex:0 0 18px;width:18px;height:18px;stroke:currentColor;fill:none;
    stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
  .rail-item span{opacity:0;transition:opacity .16s ease;font-size:.82rem}
  .portal-rail.is-open .rail-item span{opacity:1}
  .rail-item.is-active{color:var(--brand);border-left-color:var(--brand);background:var(--brand-soft)}
  .rail-item:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}
  .rail-toggle{margin-top:auto}
  body.has-shell .wrap{margin-left:52px}
  .shell-scrim{position:fixed;inset:0;z-index:39;background:rgba(0,0,0,.42);
    opacity:0;pointer-events:none;transition:opacity .2s ease}
  .portal-rail.is-open ~ .shell-scrim{opacity:1;pointer-events:auto}

  @media (max-width:760px){
    .shell-bar{
      display:flex; align-items:center; gap:9px; padding:8px 11px;
      position:sticky; top:0; z-index:38;
      background:var(--card2); border-bottom:1px solid var(--line);
    }
    .shell-burger{background:none;border:0;padding:0;color:var(--ink);display:flex;cursor:pointer}
    .shell-burger svg{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round}
    .shell-title{font-size:.82rem;color:var(--muted)}
    .portal-rail{
      position:fixed; width:176px; transform:translateX(-100%);
      transition:transform .22s ease;
    }
    .portal-rail.is-open{transform:translateX(0);width:176px}
    .portal-rail .rail-item span{opacity:1}
    body.has-shell .wrap{margin-left:0}
  }
  @media (prefers-reduced-motion: reduce){
    .portal-rail,.rail-item span,.shell-scrim{transition:none}
  }
```

Add `document.body.classList.add('has-shell')` inside the `if (v.shell_enabled)` block from Task 5, and append `<div class="shell-scrim"></div>` after the rail markup.

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_shell_responsive.js && node tests/test_portal_shell_wiring.js
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add static/client-portal.html tests/test_portal_shell_responsive.js
git commit -m "feat(portal): phone drawer, desktop rail"
```

---

### Task 7: One chat, not two

The portal ships two Ask Dr. Glen surfaces. The floating launcher (`#mentorLauncher` / `#mentorPanel`) guides a client through the current page and is ungated. The panel chat (`#chatCard`) persists to `portal_chat_messages`, is grounded in the client's findings and owned remedies, and reaches Glen and Rae in the console. Glen's decision: the panel chat survives.

**Files:**
- Modify: `static/client-portal.html` (anchors: `<button class="mentor-launcher" id="mentorLauncher"`, `<aside class="mentor-panel" id="mentorPanel"`, `const _askCard = ` near line 3675)

**Interfaces:**
- Consumes: nothing
- Produces: `#mentorPanel` renders the `portal_chat_messages` thread. `window.mentorPageChanged` is kept, since `showTab` and `showDoor` both call it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_one_chat.js`:

```js
// tests/test_portal_one_chat.js
// Run: node tests/test_portal_one_chat.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

// The two chats had separate send paths. After the merge there is one, and the
// floating panel posts to the same persisted endpoint as the card.
const sends = page.match(/\/api\/portal\/\$\{[^}]*\}\/chat\b/g) || [];
assert.ok(sends.length >= 1, 'the persisted chat endpoint must still be called');

// the mentor panel no longer has its own separate ask endpoint
assert.ok(!/mentorAsk|\/api\/mentor\/ask/.test(page),
  'the floating chat must not keep a second brain');

// mentorPageChanged survives: showTab and showDoor both call it
assert.ok(/function mentorPageChanged|window\.mentorPageChanged\s*=/.test(page),
  'mentorPageChanged is called by the router and must still exist');

// one composer id, not two competing ones
assert.strictEqual((page.match(/id="chatInput"/g) || []).length, 1);

console.log('test_portal_one_chat: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_one_chat.js`
Expected: FAIL on the second assertion, because the mentor panel has its own ask path.

- [ ] **Step 3: Write the implementation**

Point `#mentorPanel`'s send handler at the same function the card's Send button uses, so both write to `portal_chat_messages` and both render the same thread. Delete the mentor panel's separate request path and its separate message store. Keep `#mentorLauncher`, `#mentorPanel`, the microphone, the speaker toggle, the auto-guide checkbox and the continuous-conversation checkbox: those controls move onto the surviving thread rather than being removed.

Keep `mentorPageChanged` and keep passing it the current door or panel name; the concierge uses it for context and both routers call it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_one_chat.js
python3 -m pytest tests/test_portal_chat_retry_ui.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add static/client-portal.html tests/test_portal_one_chat.js
git commit -m "fix(portal): one Ask Dr. Glen thread, not two

The floating launcher kept a second, unpersisted context. A client could ask the
same question twice and only one answer reached the practice."
```

---

### Task 8: The composer at the top

The page carries a comment saying the chat card was promoted to the top of the portal so it would be the first thing a client sees, always shown. The line underneath routes it into a tab whenever the hub flag is on, and the hub flag is on in production. This task makes the behaviour match the comment, and gives it a test so a flag cannot quietly undo it again.

**Files:**
- Modify: `static/client-portal.html` (anchor: `if (_hub) { _askHtml += _askCard; } else { html += _askCard; }`)
- Modify: `static/js/portal-shell.js`
- Modify: `tests/test_portal_shell.js`

**Interfaces:**
- Consumes: `escapeHtml` from Task 3
- Produces: `renderComposer()` → HTML string for the single-line composer, containing `id="chatInput"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portal_shell.js`, above the final `console.log`:

```js
const { renderComposer } = require('../static/js/portal-shell.js');
const composer = renderComposer();
assert.ok(composer.indexOf('id="chatInput"') !== -1);
assert.ok(composer.indexOf('Ask me anything, or tell me what you need') !== -1);
assert.ok(!/—|--/.test(composer), 'no em dashes in client copy');
// one line, not a transcript: the composer must not ship message bubbles
assert.ok(composer.indexOf('chat-msgs') === -1,
  'the top composer is a single line; the thread expands from it');
```

Append to `tests/test_portal_one_chat.js`:

```js
// The regression this file exists for. The composer sits at the top of the page in
// EVERY flag state. It was demoted once by `if (_hub)`; a comment did not prevent it.
const composerAt = page.indexOf('id="chatInput"');
const hubBranch = page.indexOf('if (_hub) { _askHtml += _askCard;');
assert.strictEqual(hubBranch, -1,
  'the chat card must not be routed into a tab by the hub flag');
assert.ok(composerAt !== -1, 'the composer must be present');
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
node tests/test_portal_shell.js
node tests/test_portal_one_chat.js
```

Expected: the first fails with `renderComposer is not a function`; the second fails on the `if (_hub)` branch still being present.

- [ ] **Step 3: Write the implementation**

Add to `static/js/portal-shell.js` and both export objects:

```js
function renderComposer() {
  return '<button type="button" class="chat-bar" id="chatBar">' +
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6.5A8 8 0 1 1 21 12z"/></svg>' +
    '<input id="chatInput" type="text" autocomplete="off" ' +
    'placeholder="Ask me anything, or tell me what you need">' +
    '<span class="chat-send" id="chatSend">Send</span></button>';
}
```

In the page, delete the `if (_hub) { _askHtml += _askCard; } else { html += _askCard; }` line. Render the composer above the where-you-are banner in the home door, in both flag states, and keep the full thread in the `ask` panel where the Learn & Ask door reveals it. Tapping the composer expands the thread in place.

Style it as a 42px line:

```css
  .chat-bar{
    display:flex; align-items:center; gap:8px; width:100%; height:42px; padding:0 11px;
    border:1px solid var(--line); border-radius:21px; background:var(--card);
    color:var(--muted); font:inherit; font-size:.82rem; cursor:pointer; text-align:left;
  }
  .chat-bar svg{flex:0 0 15px;width:15px;height:15px;stroke:var(--brand);fill:none;
    stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
  .chat-bar input{flex:1 1 auto;min-width:0;background:none;border:0;color:var(--ink);
    font:inherit;outline:none}
  .chat-send{margin-left:auto;color:var(--brand);font-weight:600;font-size:.76rem}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_shell.js && node tests/test_portal_one_chat.js
python3 -m pytest tests/test_portal_hub_flag.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add static/js/portal-shell.js static/client-portal.html tests/test_portal_shell.js tests/test_portal_one_chat.js
git commit -m "fix(portal): the chat composer sits at the top in every flag state

A comment claimed this since the card was written; the line underneath it
routed the card into a tab whenever the hub flag was on. Now tested."
```

---

### Task 9: The chat routes

Asked where an invoice is, the chat opens Billing. It does not write a paragraph about invoices. This is the load-bearing part of putting a chat at the top of the page.

**Files:**
- Modify: `static/js/portal-shell.js`
- Modify: `tests/test_portal_shell.js`

**Interfaces:**
- Consumes: `DOORS` from Task 2
- Produces: `routeIntent(text)` → a door key string, or `null` when nothing matches

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portal_shell.js`, above the final `console.log`:

```js
const { routeIntent } = require('../static/js/portal-shell.js');

// the four jobs the portal exists to serve
assert.strictEqual(routeIntent('where is my invoice'), 'billing');
assert.strictEqual(routeIntent("What do I owe?"), 'billing');
assert.strictEqual(routeIntent('I want to see my biofield report'), 'scans');
assert.strictEqual(routeIntent('can I see my scan'), 'scans');
assert.strictEqual(routeIntent('reorder my remedies'), 'remedies');
assert.strictEqual(routeIntent('what helps with dry eyes'), 'solutions');
assert.strictEqual(routeIntent('how do I finish setting up'), 'home');

// every destination it can return is a real door
DOORS.forEach(function () {});
['where is my invoice', 'show my scan', 'reorder', 'what helps with floaters']
  .forEach(function (q) {
    const d = routeIntent(q);
    assert.ok(DOORS.some(function (x) { return x.key === d; }),
      q + ' routed to ' + d + ', which is not a door');
  });

// An unknown intent must return null, never a guess. A wrong destination is worse
// than no destination: the client lands somewhere irrelevant and stops trusting it.
assert.strictEqual(routeIntent('is Dr. Glen in Hawaii'), null);
assert.strictEqual(routeIntent(''), null);
assert.strictEqual(routeIntent(null), null);
assert.strictEqual(routeIntent('thank you'), null);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell.js`
Expected: FAIL with `routeIntent is not a function`

- [ ] **Step 3: Write the implementation**

Add to `static/js/portal-shell.js` and both export objects:

```js
// Question text to a door. Deliberately conservative: an unmatched question returns
// null and the concierge answers in prose. A wrong destination costs more than no
// destination, because the client lands somewhere irrelevant and stops asking.
var INTENTS = [
  ['billing',   /\b(invoice|bill|billing|receipt|owe|owed|pay|payment|paid|charge|refund)\b/i],
  ['scans',     /\b(scan|scans|report|biofield|voice analysis|5-element|five element|healing path|findings)\b/i],
  ['remedies',  /\b(reorder|re-order|refill|my remedies|what do i take|protocol|dose|dosage|cart|wishlist)\b/i],
  ['solutions', /\b(help(s)? with|what should i take|recommend|suggest|good for|support for|symptom|condition)\b/i],
  ['learn',     /\b(course|courses|class|classes|masterclass|body map|calendar|event|webinar)\b/i],
  ['account',   /\b(my account|address|password|profile|photo|referral|ambassador|membership|preferences)\b/i],
  ['home',      /\b(next step|what.s next|set ?up|setting up|get started|onboarding|where am i)\b/i]
];

function routeIntent(text) {
  if (!text || typeof text !== 'string') return null;
  for (var i = 0; i < INTENTS.length; i++) {
    if (INTENTS[i][1].test(text)) return INTENTS[i][0];
  }
  return null;
}
```

Wire it into the chat send path: when `routeIntent` returns a door, call `showDoor(door)` and render a short confirming line in the thread. When it returns null, send to the concierge unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_portal_shell.js`
Expected: `test_portal_shell: ok`

- [ ] **Step 5: Commit**

```bash
git add static/js/portal-shell.js tests/test_portal_shell.js
git commit -m "feat(portal): the chat routes to a door instead of describing it"
```

---

### Task 10: Split the `current` panel

27 cards land in the `current` panel today. Each moves to the door that owns it. Nothing is deleted.

**Files:**
- Modify: `static/client-portal.html` (anchor: the `<section data-panel="current"` wrap near line 4701, and the 63 `html +=` statements feeding it between roughly lines 3493 and 4690)

**Interfaces:**
- Consumes: `data-door` from Task 5
- Produces: five new sections, `data-panel="scan-report" | "billing-detail" | "remedy-detail" | "solutions-detail" | "account-detail"`, each with the matching `data-door`. Task 2's door map is updated to include them, which its set assertion will require.

- [ ] **Step 1: Write the failing test**

Create `tests/test_portal_current_split.js`:

```js
// tests/test_portal_current_split.js
// Run: node tests/test_portal_current_split.js
//
// Asserts BOTH halves for every card: it renders under its new door, and it no longer
// renders under `current`. A card present in both places is exactly the duplication
// this work exists to remove, and a one-sided assertion would pass on it.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');

const DESTINATIONS = {
  'Preparing your Biofield Analysis': 'scans',
  'Your scan analysis': 'scans',
  'Your healing path': 'scans',
  'Your formulation matches': 'scans',
  'Your written report': 'scans',
  'Your audio walkthrough': 'scans',
  'Your personal message from Dr. Glen': 'scans',
  'Scan history': 'scans',
  'Curious what your body is asking for': 'scans',
  'Order your first Biofield Analysis': 'scans',
  'Biofield Consult': 'scans',
  'Your invoice': 'billing',
  'Your options &amp; pricing': 'billing',
  'Your orders': 'billing',
  'Your Remedies': 'remedies',
  'Order your remedies': 'remedies',
  'Your wishlist': 'remedies',
  'Your Life Stress Essences': 'remedies',
  'Premier Research Labs options': 'remedies',
  'Fullscript': 'remedies',
  'Your practitioner recommends': 'solutions',
  'Your practitioner account': 'account',
  'Sharing': 'account',
  'Free Product Review': 'account',
  'See everything your membership unlocks': 'account',
  'Everything your membership unlocks': 'account'
};

// 26 keys: "More savings ahead" is merged into "Everything your membership unlocks"
// in Task 11, so it is asserted absent rather than placed.
assert.strictEqual(Object.keys(DESTINATIONS).length, 26);

// the builder for each door, keyed by the variable each card is appended to
const BUILDERS = {
  scans: 'scanReportHtml', billing: 'billingHtml', remedies: 'remedyDetailHtml',
  solutions: 'solutionsHtml', account: 'accountDetailHtml'
};

function bodyOf(varName) {
  const re = new RegExp('(?:^|\\n)\\s*' + varName + '\\s*\\+?=[\\s\\S]*?(?=\\n\\s*(?:let|const|var|function)\\s)', 'g');
  return (page.match(re) || []).join('\n');
}

Object.keys(DESTINATIONS).forEach(function (card) {
  const door = DESTINATIONS[card];
  const dest = bodyOf(BUILDERS[door]);
  assert.ok(dest.indexOf(card) !== -1,
    '"' + card + '" should render under ' + door + ' (' + BUILDERS[door] + ')');
});

// nothing may still be appended to the old `current` builder
const legacy = (page.match(/\n\s*html \+=/g) || []).length;
assert.ok(legacy === 0,
  'the current panel still has ' + legacy + ' appends; every card must have moved');

console.log('test_portal_current_split: ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_current_split.js`
Expected: FAIL on the first card, because `scanReportHtml` does not exist yet.

- [ ] **Step 3: Write the implementation**

Introduce the five builders next to the existing `_askHtml` / `_voiceHtml` variables:

```js
  // The `current` panel had grown to 27 cards across five unrelated concerns: the
  // biofield report, the invoice for it, remedies, discovery and account settings.
  // One builder per door, each rendered into its own section, so a client tapping
  // Scans & Reports gets their report and not a wishlist.
  let scanReportHtml = "", billingHtml = "", remedyDetailHtml = "",
      solutionsHtml = "", accountDetailHtml = "";
```

Change each of the 63 `html +=` statements to append to the builder its card belongs to, using the DESTINATIONS table above. Work card by card and re-run the test after each group of five; the test names the first card still in the wrong place.

Add the five sections to the panel wrap:

```html
      <section data-panel="scan-report" data-door="scans" hidden>${scanReportHtml}</section>
      <section data-panel="billing-detail" data-door="billing" hidden>${billingHtml}</section>
      <section data-panel="remedy-detail" data-door="remedies" hidden>${remedyDetailHtml}</section>
      <section data-panel="solutions-detail" data-door="solutions" hidden>${solutionsHtml}</section>
      <section data-panel="account-detail" data-door="account" hidden>${accountDetailHtml}</section>
```

Update `DOORS` in `static/js/portal-shell.js` so each door claims its new panel:

```js
  scans:     ['current', 'voice', 'history', 'scan-report']
  solutions: ['shop', 'solutions-detail']
  remedies:  ['remedies', 'oasis', 'cart', 'remedy-detail']
  billing:   ['orders', 'billing-detail']
  account:   ['account', 'photo', 'intake', 'records', 'refer', 'referrals', 'offers', 'finder', 'account-detail']
```

`current` stays in the map and stays in the page as an empty section until Task 12; the hub still routes to it while the hub flag is on.

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_current_split.js
node tests/test_portal_shell.js
node tests/test_portal_shell_wiring.js
python3 -m pytest tests/test_portal_biofield_block_reveal.py tests/test_portal_reorder_ui.py tests/test_portal_offers.py tests/test_portal_view_supplement_reviews.py -v
```

Expected: all PASS. `test_portal_shell.js`'s set assertion will fail until the door map lists the five new panels, which is the intended coupling.

- [ ] **Step 5: Commit**

```bash
git add static/client-portal.html static/js/portal-shell.js tests/test_portal_current_split.js
git commit -m "refactor(portal): split the current panel across five doors

27 cards spanning the biofield report, its invoice, remedies, discovery and
account settings shared one panel reached from a tile called My Analysis."
```

---

### Task 11: Merge the duplicate upsell cards

"More savings ahead" and "Everything your membership unlocks" say substantially the same thing, on the same screen, one above the other.

**Files:**
- Modify: `static/client-portal.html` (anchors: `<h2>More savings ahead</h2>`, `<h2>Everything your membership unlocks</h2>`)
- Modify: `tests/test_portal_current_split.js`

**Interfaces:**
- Consumes: `accountDetailHtml` from Task 10
- Produces: one membership card

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portal_current_split.js`, above the final `console.log`:

```js
assert.strictEqual((page.match(/More savings ahead/g) || []).length, 0,
  '"More savings ahead" is merged into the membership card');
assert.strictEqual((page.match(/<h2>Everything your membership unlocks<\/h2>/g) || []).length, 1,
  'exactly one membership card survives');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_current_split.js`
Expected: FAIL, `"More savings ahead" is merged into the membership card`

- [ ] **Step 3: Write the implementation**

Fold the savings copy and any offer rows from "More savings ahead" into the "Everything your membership unlocks" card, and delete the former. Keep every offer row that was reachable from either card; the merge removes a heading, not an offer.

- [ ] **Step 4: Run tests to verify they pass**

```bash
node tests/test_portal_current_split.js
python3 -m pytest tests/test_portal_offers.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add static/client-portal.html tests/test_portal_current_split.js
git commit -m "refactor(portal): one membership card, not two"
```

---

### Task 12: Thin the home door, retire the hub grid

**Files:**
- Modify: `static/client-portal.html` (anchor: `function buildHubHtml(d, v){`)
- Modify: `static/js/portal-shell.js`
- Modify: `tests/test_portal_shell.js`

**Interfaces:**
- Consumes: `v.journey.phases` from `dashboard/portal_onboarding.build_status`
- Produces: `renderHome(view)` → HTML string: the where-you-are banner, one next action, and time-sensitive items only

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portal_shell.js`, above the final `console.log`:

```js
const { renderHome } = require('../static/js/portal-shell.js');

const midSetup = renderHome({
  journey: { phases: [
    { key: 'be_read', title: 'Discover What Your Body Is Saying', steps: [
      { key: 'voice', label: 'Voice analysis', done: true },
      { key: 'intake', label: 'Intake', done: false, in_progress: true },
      { key: 'photo', label: 'Photo', done: false },
      { key: 'biofield', label: 'Biofield Analysis', done: false }
    ]}
  ]},
  unpaid_invoice: { amount_dollars: '200.00', ref: 'BA-1' }
});
assert.ok(midSetup.indexOf('Discover What Your Body Is Saying') !== -1);
assert.ok(midSetup.indexOf('$200.00') !== -1, 'an unpaid invoice must surface on home');
assert.ok(midSetup.indexOf('Intake') !== -1, 'mid-setup shows the step list');

// finished and nothing outstanding: a short page, not an empty one
const settled = renderHome({
  journey: { phases: [
    { key: 'be_read', title: 'Discover What Your Body Is Saying', steps: [
      { key: 'voice', label: 'Voice analysis', done: true },
      { key: 'intake', label: 'Intake', done: true },
      { key: 'photo', label: 'Photo', done: true },
      { key: 'biofield', label: 'Biofield Analysis', done: true }
    ]}
  ]}
});
assert.ok(settled.indexOf('$') === -1, 'no invoice line when nothing is owed');
assert.ok(settled.length < midSetup.length, 'a settled client sees a shorter page');

// home never becomes a menu again
assert.ok(settled.indexOf('hub-tile') === -1, 'home must not render tiles');
assert.ok(!/—|--/.test(settled), 'no em dashes in client copy');
assert.strictEqual(renderHome({}).indexOf('undefined'), -1,
  'an empty payload must not leak undefined into the page');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_portal_shell.js`
Expected: FAIL with `renderHome is not a function`

- [ ] **Step 3: Write the implementation**

Add `renderHome(view)` to `static/js/portal-shell.js` and both export objects. It renders, in order: the where-you-are banner with the current phase title and a progress bar computed from the phase's `done` steps; the step list only while the phase is unfinished; an unpaid-invoice row when `view.unpaid_invoice` is present; an appointment row when one falls in the next seven days; nothing else. Every value goes through `escapeHtml`, and a missing key renders nothing rather than `undefined`.

Render it into the `home` door in place of `buildHubHtml`. Delete `buildHubHtml`, the `hub-tile` and `hub-grid-group` CSS, and the `hub` panel section. Remove `hub` from the door map and add `home` in its place, with a `<section data-panel="home" data-door="home">`. `test_portal_shell.js`'s set assertion enforces that the map and the page agree after the change.

Remove `_PORTAL_HUB_ENABLED` and every `_hub` branch, and delete the now-empty `current` section. The shell flag alone decides what renders.

- [ ] **Step 4: Run tests to verify they pass**

```bash
for f in tests/*.js; do node "$f" || exit 1; done
python3 -m pytest tests/test_portal_shell_flag.py tests/test_client_portal_routes.py \
  tests/test_portal_card_state.py tests/test_portal_load_resilience.py \
  tests/test_portal_library_render.py tests/test_portal_identity.py -v
```

Expected: all PASS. `tests/test_portal_hub_flag.py` now pins removed behaviour: invert it to assert the hub is gone rather than deleting it, so a reintroduction fails.

- [ ] **Step 5: Verify against a real portal, not a payload**

```bash
doppler secrets get PORTAL_TEST_LINK --project remedy-match --config prd --plain
```

Open that link and confirm, at desktop width and at 375px:

- the rail shows seven doors and opens to labels
- under 760px the rail is off-canvas and the header's menu button opens it
- the composer is the first thing on the page
- typing "where is my invoice" opens Billing
- the biofield report, its invoice and the remedy list are each one tap from the rail

A green suite is not evidence the page renders. This step is the evidence.

- [ ] **Step 6: Commit**

```bash
git add static/client-portal.html static/js/portal-shell.js tests/
git commit -m "feat(portal): thin home door, retire the hub grid"
```

- [ ] **Step 7: The rollout, which is two deploys**

1. Merge. `PORTAL_SHELL_ENABLED` is still unset in `prd`, so nothing changes for clients.
2. Confirm CI is green on `main`, and confirm the deploy actually landed. This repo has had a merged, CI-green change never reach production because autodeploy was broken by a repo transfer, so check the running commit rather than assuming.
3. Set `PORTAL_SHELL_ENABLED=on` in Doppler `prd`. Never set it on Render.
4. Re-open `PORTAL_TEST_LINK` and repeat step 5's checks against production.

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: the seven doors (2), the rail and its phone behaviour (3, 5, 6), the chat merge (7), the composer at the top (8), routing (9), the `current` split with all 27 cards (10), the upsell merge (11), the thin home and hub retirement (12), the staged flag rollout (4, 12). Find Solutions is deferred to its own spec, as the spec states. The prerequisite the spec did not know about, that no JS test in this repo has ever run in CI, is Task 1.

**Types and names are consistent across tasks.** `renderRail`, `renderPhoneHeader`, `renderComposer`, `renderHome`, `routeIntent`, `panelsForDoor`, `doorForPanel`, `allPanels`, `escapeHtml`, `showDoor`, `panelShown`, and the five builder variables are each defined once and used under the same name everywhere after.

**Two deliberate couplings.** Task 10 breaks Task 2's set assertion until the door map lists the five new panels, and Task 12 breaks it again until `hub` becomes `home`. That is the assertion doing its job: it is the one check that catches a door pointing at nothing, which fails silently in the browser by bouncing to home.

**One risk worth naming.** Task 10 edits 63 append statements in a 7,891-line file by hand. The test names the first card in the wrong place but cannot catch a card whose surrounding conditional was dropped along with it. Re-run `tests/test_portal_biofield_block_reveal.py` and `tests/test_portal_offers.py` after every group of five, not only at the end.
