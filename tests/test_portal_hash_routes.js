// tests/test_portal_hash_routes.js
// Run: node tests/test_portal_hash_routes.js
//
// Final review I5. A deep link names a card; showTab/showDoor reveal a DOOR. Task
// 10 moved cards out of `current` into doors of their own without moving the
// routes that point at them, so #recs opened the Scans door while recsCard sat in
// Find Solutions, and openPortalOrderBasket() opened Scans while
// #portal-order-basket sat in My Remedies. Both links did nothing visible.
// #recs is not a minor link: dashboard/portal_view.py, dashboard/portal_onboarding.py
// and dashboard/eye_vision_report.py all emit it, and renderHome re-emits those
// hrefs verbatim, so it is the Home landing's own next-step link.
//
// The invariant this file pins, for every route and every programmatic showTab
// target: the door that the named panel belongs to must also own a panel that
// actually contains the target card. Naming a panel that exists is not enough,
// which is exactly why "current" looked correct for four tasks.
//
// A target this file cannot place is a FAILURE, not a skip. An unresolvable id is
// how a route quietly stops being checked.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const shell = require(path.join(__dirname, '..', 'static', 'js', 'portal-shell.js'));

// ---------------------------------------------------------------------------
// Source regions.
// ---------------------------------------------------------------------------
const renderAt = page.indexOf('function render(d, v){');
assert.ok(renderAt !== -1, 'render(d, v) not found');
const regionStart = page.indexOf('let html = `', renderAt);
const wrapStart = page.indexOf('const _wrapPanels =', renderAt);
const wrapEnd = page.indexOf('app.innerHTML = html;', renderAt);
assert.ok(regionStart !== -1 && wrapStart > regionStart && wrapEnd > wrapStart, 'render() bounds not found');
const firstPart = page.indexOf('part("', regionStart);
assert.ok(firstPart > regionStart && firstPart < wrapStart, 'no part() pushes found in the card region');

// End of a JS statement, honouring strings, template literals and ${} nesting.
// Same scanner shape as tests/test_portal_current_split.js: a "next semicolon"
// search stops inside `style="...;..."` on almost every card in this file.
function endOfStatement(src, i) {
  const stack = [{ kind: 'code', depth: 0 }];
  while (i < src.length) {
    const c = src[i];
    const top = stack[stack.length - 1];
    if (top.kind === 'code' || top.kind === 'tplexpr') {
      if (c === '`') stack.push({ kind: 'tpl' });
      else if (c === "'") stack.push({ kind: 'sq' });
      else if (c === '"') stack.push({ kind: 'dq' });
      else if (c === '/' && src[i + 1] === '/') { i = src.indexOf('\n', i); if (i === -1) break; }
      else if (c === '(' || c === '[' || c === '{') top.depth++;
      else if (c === ')' || c === ']' || c === '}') {
        if (c === '}' && top.kind === 'tplexpr' && top.depth === 0) stack.pop();
        else top.depth--;
      } else if (c === ';' && top.kind === 'code' && top.depth === 0 && stack.length === 1) return i;
    } else if (top.kind === 'tpl') {
      if (c === '\\') i++;
      else if (c === '`') stack.pop();
      else if (c === '$' && src[i + 1] === '{') { stack.push({ kind: 'tplexpr', depth: 0 }); i++; }
    } else {
      if (c === '\\') i++;
      else if ((top.kind === 'sq' && c === "'") || (top.kind === 'dq' && c === '"')) stack.pop();
    }
    i++;
  }
  return -1;
}

// Every part(door, html) push, with the span of source it covers.
const PUSHES = [];
{
  const re = /\bpart\((?:"([a-z-]+)"|null)\s*,/g;
  re.lastIndex = regionStart;
  let m;
  while ((m = re.exec(page)) && m.index < wrapStart) {
    const end = endOfStatement(page, m.index);
    assert.ok(end !== -1, 'unterminated part() at ' + m.index);
    PUSHES.push({ door: m[1] === undefined ? null : m[1], start: m.index, end: end });
    re.lastIndex = end;
  }
}
assert.ok(PUSHES.length > 40, 'implausibly few part() pushes parsed: ' + PUSHES.length);

// Each door's detail panel, i.e. the panel that renders partsFor(door).
const DETAIL_OF = {};
{
  const re = /<section data-panel="([a-z-]+)"[^>]*>[^<]*?partsFor\("([a-z-]+)"\)/g;
  let m;
  while ((m = re.exec(page))) DETAIL_OF[m[2]] = m[1];
  assert.strictEqual(Object.keys(DETAIL_OF).length, 6,
    'expected 6 door detail panels, found ' + JSON.stringify(DETAIL_OF));
}

// The panel section that encloses an offset inside the wrap.
function sectionPanelAt(i) {
  const open = page.lastIndexOf('<section ', i);
  if (open === -1 || open < wrapStart) return null;
  const tag = page.slice(open, page.indexOf('>', open));
  const m = /data-panel="([a-z-]+)"/.exec(tag);
  return m ? m[1] : null;
}

// ---------------------------------------------------------------------------
// Where does a card id actually render? Returns the set of panels that can hold
// it. Four emission shapes exist in this file and all four are resolved; an id
// matching none of them throws, rather than being skipped.
// ---------------------------------------------------------------------------
function panelsHolding(id, seen) {
  seen = seen || {};
  const needle = 'id="' + id + '"';
  const out = {};
  let i = page.indexOf(needle);
  assert.ok(i !== -1, 'no element in the page carries ' + needle);
  for (; i !== -1; i = page.indexOf(needle, i + 1)) {
    if (i >= wrapStart && i < wrapEnd) {
      // 2. inside the panel wrap: the section that encloses it.
      const p = sectionPanelAt(i);
      if (p) { out[p] = true; continue; }
    }
    if (i >= regionStart && i < wrapStart) {
      // 1. inside a part(door, ...) push.
      const push = PUSHES.find(function (p) { return i > p.start && i < p.end; });
      if (push && push.door && DETAIL_OF[push.door]) { out[DETAIL_OF[push.door]] = true; continue; }
      if (push) continue;                       // part(null, ...): legacy-only arm
      // 4. assigned into an accumulator that the wrap renders into one section.
      const acc = /_(\w+)Html\s*\+=/.exec(page.slice(i, wrapStart));
      if (acc) {
        const at = page.indexOf('${_' + acc[1] + 'Html', wrapStart);
        const p = at !== -1 ? sectionPanelAt(at) : null;
        if (p) { out[p] = true; continue; }
      }
      if (i < firstPart) continue;              // the opening `let html` hero template
    }
    // 3. inside a helper: resolve the helper's own call sites.
    const fnAt = page.lastIndexOf('\nfunction ', i);
    assert.ok(fnAt !== -1, 'cannot place ' + needle + ' at offset ' + i);
    const fnName = /\nfunction (\w+)\(/.exec(page.slice(fnAt))[1];
    assert.ok(!seen[fnName], 'recursive helper resolution on ' + fnName);
    seen[fnName] = true;
    let found = false;
    let c = page.indexOf(fnName + '(', regionStart);
    for (; c !== -1 && c < wrapEnd; c = page.indexOf(fnName + '(', c + 1)) {
      if (c >= wrapStart) { const p = sectionPanelAt(c); if (p) { out[p] = true; found = true; } continue; }
      const push = PUSHES.find(function (p) { return c > p.start && c < p.end; });
      if (push && push.door && DETAIL_OF[push.door]) { out[DETAIL_OF[push.door]] = true; found = true; }
    }
    assert.ok(found, 'helper ' + fnName + '() emits ' + needle + ' but is never rendered into a panel');
  }
  const panels = Object.keys(out);
  assert.ok(panels.length, 'could not place ' + needle + ' in any panel');
  return panels;
}

// ---------------------------------------------------------------------------
// The routes table, read from the source it ships in.
// ---------------------------------------------------------------------------
const tableAt = page.indexOf('const PORTAL_HASH_ROUTES = {');
assert.ok(tableAt !== -1, 'PORTAL_HASH_ROUTES not found');
const tableEnd = page.indexOf('\n};', tableAt);
const ROUTES = (0, eval)('(' + page.slice(page.indexOf('{', tableAt), tableEnd + 2) + ')');
assert.ok(Object.keys(ROUTES).length >= 12, 'implausibly few hash routes parsed');

// Under the shell, showTab(name) hands off to showDoor(doorForPanel(name)), which
// reveals every panel of that door and hides everything else. So the destination
// a route really opens is a door, and the question is whether the card is in it.
function doorRevealedBy(panel) {
  const door = shell.doorForPanel(panel);
  assert.ok(door, 'panel ' + JSON.stringify(panel) + ' belongs to no door, so no route can open it');
  return door;
}

const failures = [];
Object.keys(ROUTES).forEach(function (key) {
  const route = ROUTES[key];
  const shellPanel = route.shellPanel || route.panel;
  const door = doorRevealedBy(shellPanel);
  const holders = panelsHolding(route.target);
  const reachable = holders.filter(function (p) { return shell.doorForPanel(p) === door; });
  if (!reachable.length) {
    failures.push('#' + key + ' opens the ' + door + ' door (panel ' + shellPanel +
      '), but its target #' + route.target + ' renders in ' + JSON.stringify(holders) +
      ', owned by ' + JSON.stringify(holders.map(shell.doorForPanel)));
  }
  // The legacy panel must still exist for the shell-off page.
  assert.ok(page.indexOf('data-panel="' + route.panel + '"') !== -1,
    '#' + key + ' names panel ' + route.panel + ', which no section renders');
});
assert.strictEqual(failures.length, 0,
  'hash route(s) resolve to a door that does not reveal the target:\n  ' + failures.join('\n  '));

// ---------------------------------------------------------------------------
// Programmatic showTab targets. Every literal panel name handed to showTab must
// be a real panel that some door owns, so no code path can strand a client on a
// door that reveals nothing.
// ---------------------------------------------------------------------------
{
  const re = /showTab\(\s*["']([a-z-]+)["']/g;
  const names = {};
  let m;
  while ((m = re.exec(page))) names[m[1]] = true;
  const list = Object.keys(names);
  assert.ok(list.length >= 2, 'no literal showTab() targets found: ' + JSON.stringify(list));
  list.forEach(function (n) {
    assert.ok(page.indexOf('data-panel="' + n + '"') !== -1,
      'showTab(' + JSON.stringify(n) + ') names a panel no section renders');
    doorRevealedBy(n);
  });
}

// openPortalOrderBasket() scrolls to #portal-order-basket after switching panels,
// so the panel it switches to has to be in that card's door. It is the "review
// your order" path from the shop and the chat cart, so a wrong door here loses
// the client's basket.
{
  const fn = /function openPortalOrderBasket\(captureContext\)\{[\s\S]*?\n\}/.exec(page);
  assert.ok(fn, 'openPortalOrderBasket() not found');
  const route = /portalPanelFor\((\{[^}]*\})\)/.exec(fn[0]);
  assert.ok(route, 'openPortalOrderBasket() must resolve its panel through portalPanelFor(), got:\n' + fn[0]);
  const r = (0, eval)('(' + route[1] + ')');
  const door = doorRevealedBy(r.shellPanel || r.panel);
  const holders = panelsHolding('portal-order-basket');
  assert.ok(holders.some(function (p) { return shell.doorForPanel(p) === door; }),
    'openPortalOrderBasket() opens the ' + door + ' door, but #portal-order-basket renders in ' +
    JSON.stringify(holders));
  assert.ok(page.indexOf('data-panel="' + r.panel + '"') !== -1,
    'openPortalOrderBasket()\'s shell-off panel ' + r.panel + ' is not rendered by any section');
}

// portalPanelFor must fall back to the legacy panel when the shell is not live,
// so none of the above changes anything on a production page today.
{
  const fn = /function portalPanelFor\(route\)\{[\s\S]*?\n\}/.exec(page);
  assert.ok(fn, 'portalPanelFor() not found');
  assert.ok(/portalShellLive\(\) && route\.shellPanel/.test(fn[0]),
    'portalPanelFor() must use shellPanel only while the shell is live');
  assert.ok(/return .*route\.panel;/.test(fn[0]),
    'portalPanelFor() must fall back to route.panel when the shell panel is absent');
}

console.log('test_portal_hash_routes: ok (' + Object.keys(ROUTES).length +
            ' hash routes + showTab targets + the order basket placed in a door that reveals them)');
