// tests/test_remedy_tier_render.js
//
// First and second order remedies on two pages, executed against a small fake DOM:
//  - static/pattern-page.html: second order remedies are listed apart, under their
//    own heading, each with its conditions; a second order row with no condition
//    is not shown.
//  - static/console-biofield-reveals.html: the "Second match" note shows for a
//    second order remedy, clears when the name is retyped, is set by an
//    alternative chip, and travels through collectLayers() to Save.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

function el(tag) {
  const e = {
    tagName: tag, children: [], textContent: '', className: '', style: {}, dataset: {},
    listeners: {}, value: '',
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    fire(t) { (this.listeners[t] || []).forEach(f => f()); },
  };
  return e;
}
function text(e) {
  return (e.textContent || '') + e.children.map(text).join('');
}
function find(e, pred) {
  if (pred(e)) return e;
  for (const c of e.children) { const r = find(c, pred); if (r) return r; }
  return null;
}
function between(src, a, b) {
  const i = src.indexOf(a);
  const j = src.indexOf(b, i);
  assert.ok(i !== -1 && j > i, 'slice bounds not found: ' + a);
  return src.slice(i, j + b.length);
}

// ── pattern page ─────────────────────────────────────────────────────────────
{
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'pattern-page.html'), 'utf8');
  const body = between(src, 'var remAll = d.remedies || [];',
                       "sec.appendChild(note);\n        }");
  const host = el('div');
  const document = { createElement: el, createTextNode: t => ({ textContent: t, children: [] }),
                     getElementById: () => host };
  const run = new Function('d', 'document', body);
  run({ remedies: [
    { name: 'Mucosa Syntropy', order_tier: 1, conditions: [], product_slug: 'mucosa-syntropy' },
    { name: 'Terrain Restore', order_tier: 2, conditions: ['leaky gut', 'bloating'] },
    { name: 'Hidden One', order_tier: 2, conditions: [] },
  ] }, document);
  const all = text(host);
  assert.ok(all.includes('What may help'), 'first order heading');
  assert.ok(all.includes('Mucosa Syntropy'), 'first order listed');
  assert.ok(all.includes('May also help, depending on your history'), 'second order heading');
  assert.ok(all.includes('Terrain Restore: leaky gut, bloating'), 'second order with conditions');
  assert.ok(!all.includes('Hidden One'), 'second order with no condition is not shown');
  const lists = host.children.filter(c => c.tagName === 'ul');
  assert.strictEqual(lists.length, 2, 'two separate lists');
  assert.ok(!text(lists[0]).includes('Terrain Restore'), 'second order not in the first list');

  const host2 = el('div');
  run({ remedies: [{ name: 'Only Second', order_tier: 2, conditions: ['x'] }] },
      { createElement: el, createTextNode: t => ({ textContent: t, children: [] }),
        getElementById: () => host2 });
  assert.ok(text(host2).includes('Only Second: x'), 'section renders with only second order');
}

// ── reveal review ────────────────────────────────────────────────────────────
{
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'console-biofield-reveals.html'), 'utf8');
  const note = between(src, "var secondNote = document.createElement('div');",
                       "nameInput.addEventListener('input', function(){ setSecond([]); });");
  const document = { createElement: el };
  const row = el('div');
  const nameInput = el('input');
  const run = new Function('document', 'row', 'nameInput', 'rem',
                           note + '\nreturn setSecond;');
  const setSecond = run(document, row, nameInput, { name: 'Terrain Restore', second_match: ['leaky gut'] });
  const shown = find(row, e => e.className === 'second-match');
  assert.strictEqual(shown.textContent, 'Second match: leaky gut');
  assert.deepStrictEqual(JSON.parse(row.dataset.secondMatch), ['leaky gut']);
  nameInput.fire('input');
  assert.strictEqual(shown.textContent, '', 'retyping the name clears the note');
  assert.deepStrictEqual(JSON.parse(row.dataset.secondMatch), []);
  setSecond(['bloating']);
  assert.strictEqual(shown.textContent, 'Second match: bloating', 'an alternative sets its own');

  const chip = between(src, "var secondTxt = (Array.isArray(a.second_match)", "setSecond(a.second_match);");
  assert.ok(chip.includes("' (second match: '"), 'alternative chips name their conditions');

  const collect = between(src, 'function collectLayers(card){', '\n  return out;\n}');
  const collectLayers = new Function(collect + '\nreturn collectLayers;')();
  function field(v) { return { value: v, checked: false }; }
  const r = {
    dataset: { patterns: '["EI3"]', patternLabels: '[]', alternatives: '[]',
               secondMatch: '["leaky gut"]' },
    querySelector(sel) {
      return { '.remedy-name': field('Terrain Restore'), '.remedy-slug': field('terrain-restore'),
               '.remedy-meaning': field('m'), '.remedy-remember': field(false),
               '.layer-title-field': field('T'), '.layer-summary-field': field('S') }[sel] || null;
    },
  };
  const card = { querySelectorAll: () => [r] };
  assert.deepStrictEqual(collectLayers(card)[0].remedy.second_match, ['leaky gut'], 'kept through Save');
  r.dataset.secondMatch = '[]';
  assert.ok(!('second_match' in collectLayers(card)[0].remedy), 'absent when cleared');
}

console.log('test_remedy_tier_render: ok');
