// tests/test_console_orders_store_lane.js
// Run: node tests/test_console_orders_store_lane.js
//
// Seven GrooveKart orders sat unpaid for six weeks, up to $1,647 of real,
// shipped, paid business. They were never hidden: they were in the board's
// "Cart" lane, because a storefront order arrives as status=new / unpaid, the
// same shape as an abandoned checkout.
//
// So nobody was ignoring a queue. They were reading a label that said these were
// carts. A store order that a customer completed and paid for is not a cart, and
// the board has to say so.
//
// This asserts on the LANES table in the page source, executed rather than
// grepped, so a lane that stops matching is a failure and not a silent change.
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const page = fs.readFileSync(
  path.join(__dirname, "..", "static", "console-orders.html"), "utf8");

// Pull the pieces the lanes depend on and run them for real.
function extract(name) {
  const i = page.indexOf("function " + name + "(");
  assert.notStrictEqual(i, -1, "function " + name + " not found");
  let depth = 0, j = page.indexOf("{", i);
  for (;; j++) {
    if (page[j] === "{") depth++;
    else if (page[j] === "}" && --depth === 0) return page.slice(i, j + 1);
  }
}
const lanesSrc = page.match(/var LANES = \[[\s\S]*?\];/);
assert.ok(lanesSrc, "LANES table not found");
const storeSrc = page.match(/var STORE_SOURCES = \[[^\]]*\];/);
assert.ok(storeSrc, "STORE_SOURCES not found: the board cannot tell a store order apart");

const ctx = {};
new Function(extract("isPaidReady") + storeSrc[0] + extract("isStoreOrder")
  + lanesSrc[0] + "return {LANES:LANES, isStoreOrder:isStoreOrder};").call(ctx);
const { LANES, isStoreOrder } = new Function(
  extract("isPaidReady") + storeSrc[0] + extract("isStoreOrder") + lanesSrc[0]
  + "return {LANES:LANES, isStoreOrder:isStoreOrder};")();

function laneOf(o) {
  for (const l of LANES) {
    if (l[2]) { if (l[2](o)) return l[0]; }
    else if (o.status === l[0]) return l[0];
  }
  return null;
}

const storeOrder  = { id: 1, source: "groovekart", status: "new", pay_status: "unpaid" };
const realCart    = { id: 2, source: "portal",     status: "new", pay_status: "unpaid" };
const storePaid   = { id: 3, source: "groovekart", status: "new", pay_status: "paid" };
const storeShipped= { id: 4, source: "groovekart", status: "shipped", pay_status: "paid" };

// 1. A store order awaiting payment confirmation gets its own lane.
const lane = laneOf(storeOrder);
assert.notStrictEqual(lane, "cart",
  "a completed store order is still being shown as a Cart, which is what let seven of them sit for six weeks");
assert.strictEqual(lane, "store-confirm", "store order landed in lane " + lane);

// 2. A genuine unpaid cart is untouched.
assert.strictEqual(laneOf(realCart), "cart", "a real cart stopped being a Cart");

// 3. Once confirmed it leaves the lane entirely.
assert.strictEqual(laneOf(storePaid), "paid");
assert.strictEqual(laneOf(storeShipped), "shipped");

// 4. No order may match two lanes: a duplicate on the board is worse than a
//    mislabelled one, because a payment could be recorded twice.
for (const o of [storeOrder, realCart, storePaid, storeShipped]) {
  const hits = LANES.filter(l => l[2] ? l[2](o) : o.status === l[0]);
  assert.strictEqual(hits.length, 1,
    "order " + o.id + " matches " + hits.length + " lanes: " + hits.map(h => h[0]));
}

// 5. isStoreOrder must not guess from the channel or the total.
assert.ok(isStoreOrder(storeOrder));
assert.ok(!isStoreOrder(realCart));
assert.ok(!isStoreOrder({ source: "", channel: "retail", status: "new" }));

// 6. The board's idea of a storefront source must match the server's, or an
//    order type could be added on one side and go unlabelled on the other.
const appPy = fs.readFileSync(path.join(__dirname, "..", "app.py"), "utf8");
const server = appPy.match(/_STOREFRONT_CHANNEL_SOURCES = \(([^)]*)\)/);
assert.ok(server, "_STOREFRONT_CHANNEL_SOURCES not found in app.py");
const serverSet = (server[1].match(/"([^"]+)"/g) || []).map(s => s.replace(/"/g, "")).sort();
const boardSet = (storeSrc[0].match(/"([^"]+)"/g) || []).map(s => s.replace(/"/g, "")).sort();
assert.deepStrictEqual(boardSet, serverSet,
  "board STORE_SOURCES " + boardSet + " has drifted from app.py " + serverSet);

console.log("test_console_orders_store_lane: ok (store orders leave the Cart lane, "
  + LANES.length + " lanes, no overlaps)");
