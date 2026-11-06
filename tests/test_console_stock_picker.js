// tests/test_console_stock_picker.js — run: node tests/test_console_stock_picker.js
// The Stock page picker (Glen, 2026-11-06): 10 rows visible, counted products hidden
// until two letters are typed. Extracts pickFilter from the page and checks it.
const assert = require("assert");
const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../static/console-stock.html", "utf8");
const m = html.match(/function pickFilter\(items, counted, q\)\{[\s\S]*?\n  \}\n/);
assert.ok(m, "pickFilter is on the page");
const pickFilter = new Function(m[0] + "; return pickFilter;")();

const items = [{key: "binder-complex", name: "Binder Complex"},
               {key: "blood-cleanse", name: "Blood Cleanse"},
               {key: "bone-builder", name: "Bone Builder"}];
const counted = {"blood-cleanse": true};
const names = r => r.map(x => x.key);

assert.deepStrictEqual(names(pickFilter(items, counted, "")), ["binder-complex", "bone-builder"],
  "empty: counted products are left off");
assert.deepStrictEqual(names(pickFilter(items, counted, "b")), ["binder-complex", "bone-builder"],
  "one letter: still left off");
assert.deepStrictEqual(names(pickFilter(items, counted, "bl")), ["blood-cleanse"],
  "two letters: a matching counted product is found");
assert.deepStrictEqual(names(pickFilter(items, counted, "bo")), ["bone-builder"],
  "typed text narrows the list");
assert.ok(/max-height:calc\(10 \* 32px\)/.test(html) && /\.pick li\{ height:32px;/.test(html),
  "the list shows 10 rows of 32px, then scrolls");
assert.ok(!/<datalist/.test(html), "the unsizeable datalist is gone");
console.log("stock picker OK");
