// tests/test_console_orders_bottle_sizes.js
// Run: node tests/test_console_orders_bottle_sizes.js
//
// Glen, 2026-09-19: "In Orders, list the total number of bottles of each size", on
// unpaid orders too, "so any adjustment in shipping can be determined prior to
// payment", totalled over "all open" orders. Runs the page's own helpers.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const page = fs.readFileSync(path.join(__dirname, "..", "static", "console-orders.html"), "utf8");
function extract(name) {
  const i = page.indexOf("function " + name + "(");
  assert.notStrictEqual(i, -1, name + " not found");
  let depth = 0, j = page.indexOf("{", i);
  for (;; j++) { if (page[j] === "{") depth++; else if (page[j] === "}" && --depth === 0) return page.slice(i, j + 1); }
}
const open = page.match(/var SIZE_OPEN = [^;]*;[^\n]*\n/);
assert.ok(open, "SIZE_OPEN not found");
const esc = "function esc(s){return String(s);}";
const { sizeText, sizeTotals } = new Function(esc + open[0] + extract("sizeText") + extract("sizeTotals")
  + "return {sizeText, sizeTotals};")();

const pb = by => ({ pack_breakdown: { by_size: by } });
const orders = [
  { id: 1, status: "proposed", pay_status: "unpaid", ...pb({ "30 g": 2, "Dropper 50 mL": 1 }) },
  { id: 2, status: "new", pay_status: "paid", ...pb({ "30 g": 1 }) },
  { id: 3, status: "shipped", ...pb({ "30 g": 9 }) },       // gone: not open
  { id: 4, status: "cancelled", ...pb({ "30 g": 9 }) },     // gone: not open
  { id: 5, status: "confirmed", ...pb({ "size not set": 1 }) },
];
assert.deepStrictEqual(sizeTotals(orders), { "30 g": 3, "Dropper 50 mL": 1, "size not set": 1 },
  "unpaid and paid open orders both count; shipped and cancelled do not");
assert.strictEqual(sizeText({ "30 g": 3, "Dropper 50 mL": 1 }), "3 × 30 g, 1 × Dropper 50 mL");
assert.strictEqual(sizeText({}), "");
assert.strictEqual(sizeText({ "30 g": 0 }), "", "a zero size is not listed");
assert.ok(page.includes("sizeTotals(currentOrders.concat(pastOrders))"),
  "the total must include open orders older than a month");
console.log("test_console_orders_bottle_sizes: ok");
