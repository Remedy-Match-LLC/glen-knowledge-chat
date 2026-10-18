// tests/test_console_orders_packing_photos.js
// Run: node tests/test_console_orders_packing_photos.js
//
// Packing photos on the order card (Glen, 2026-09-25). A drop-ship order being packed
// with no photo shows a reminder; nothing about it blocks packing. Runs the page's own
// packingPhotosHtml rather than grepping for the label.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const page = fs.readFileSync(path.join(__dirname, "..", "static", "console-orders.html"), "utf8");
const i = page.indexOf("function packingPhotosHtml(");
assert.notStrictEqual(i, -1, "packingPhotosHtml not found");
let depth = 0, j = page.indexOf("{", i);
for (;; j++) { if (page[j] === "{") depth++; else if (page[j] === "}" && --depth === 0) break; }
const html = new Function(page.slice(i, j + 1) + "; return packingPhotosHtml;")();

const ds = { id: 203, source: "dropship", status: "new", packing_photo_count: 0 };
assert.ok(html(ds).includes("pk-remind"), "no reminder on an unphotographed drop-ship order");
assert.ok(html({ ...ds, status: "packed" }).includes("pk-remind"), "no reminder once packed");
assert.ok(/pk-remind" style="display:none"/.test(html({ ...ds, packing_photo_count: 7 })),
          "reminder showing despite photos");
assert.ok(!/pk-remind" style="display:none"/.test(html(ds)), "reminder hidden with no photo");
assert.ok(!html({ ...ds, source: "in-house" }).includes("pk-remind"), "reminder on a non-drop-ship order");
assert.ok(!html({ ...ds, status: "shipped" }).includes("pk-remind"), "reminder after shipping");
assert.ok(html({ ...ds, packing_photo_count: 7 }).includes("loadPackingPhotos(203)"), "no Show photos button");
assert.ok(!html(ds).includes("loadPackingPhotos(203)"), "Show photos with none to show");
assert.ok(html(ds).includes("uploadPackingPhotos(203, this)"), "no upload control");
assert.ok(!/disabled|preventDefault/.test(html(ds)), "the reminder must never block anything");
// The Pack button is built elsewhere and does not read the photo count.
const k = page.indexOf("function cardHtml(");
const cardSrc = page.slice(k, page.indexOf("function toggleCard(", k));
assert.ok(!/packing_photo_count[^;]*mark_packed|mark_packed[^;]*packing_photo_count/.test(cardSrc),
          "packing must not depend on photos");
// Deleting the last photo brings the reminder back: loadPackingPhotos follows the count.
(async () => {
  const li = page.indexOf("async function loadPackingPhotos(");
  let d = 0, e = page.indexOf("{", li);
  for (;; e++) { if (page[e] === "{") d++; else if (page[e] === "}" && --d === 0) break; }
  const el = () => ({ textContent: "", innerHTML: "", style: { display: "none" } });
  const parts = { ".pk-thumbs": el(), ".pk-status": el(), ".pk-n": el(), ".pk-remind": el() };
  const box = { querySelector: (q) => parts[q] };
  const load = new Function("document", "fetch", "hdr", "_pkBlobUrl",
    page.slice(li, e + 1) + "; return loadPackingPhotos;")(
    { getElementById: () => box },
    async () => ({ ok: true, json: async () => ({ photos: [] }) }), () => ({}), async () => "");
  await load(203);
  assert.strictEqual(parts[".pk-remind"].style.display, "", "reminder not restored at zero photos");
  console.log("test_console_orders_packing_photos: ok");
})().catch((err) => { console.error(err); process.exit(1); });
