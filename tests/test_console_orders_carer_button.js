// tests/test_console_orders_carer_button.js
// Run: node tests/test_console_orders_carer_button.js
//
// Bill with caregiver (Glen, 2026-09-19): the Orders board offers "Add to caregiver's
// order" only on an unpaid, unreplaced order whose client a caregiver may pay for.
// Runs the page's own carerBtn rather than grepping for the label.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const page = fs.readFileSync(path.join(__dirname, "..", "static", "console-orders.html"), "utf8");
const i = page.indexOf("function carerBtn(");
assert.notStrictEqual(i, -1, "carerBtn not found");
let depth = 0, j = page.indexOf("{", i);
for (;; j++) { if (page[j] === "{") depth++; else if (page[j] === "}" && --depth === 0) break; }
const carerBtn = new Function(page.slice(i, j + 1) + "; return carerBtn;")();

const pet = { id: 192, pay_status: "unpaid", billing_caregivers: ["sharon@x.com"] };
assert.ok(carerBtn(pet).includes("billCarer(192)"), "the button is missing for a pet order");
assert.strictEqual(carerBtn({ ...pet, billing_caregivers: [] }), "", "shown with no caregiver");
assert.strictEqual(carerBtn({ ...pet, pay_status: "paid" }), "", "shown on a paid order");
assert.strictEqual(carerBtn({ ...pet, superseded_by_order_id: 194 }), "", "shown on a replaced order");
assert.strictEqual(carerBtn({ id: 1, pay_status: "unpaid" }), "", "shown with no caregiver field");
console.log("test_console_orders_carer_button: ok");
