// Tests heroWithRef and heroLinkify inside static/begin.html.
// A chat product link opens on the client host, where the funnel host's rm_ref cookie
// cannot reach, so the link's address carries ?ref= (Glen, 2026-10-13).
// Extracts the real definitions from the HTML and asserts their behaviour.
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "begin.html"), "utf8");

function grab(re, label) {
  const m = html.match(re);
  if (!m) { console.error(`FAIL: ${label} not found in begin.html`); process.exit(1); }
  return m[0];
}
const escDef = grab(/function escapeHtml\(s\)\{[\s\S]*?\n    \}/, "escapeHtml");
const refDef = grab(/function heroWithRef\(u\)\{[\s\S]*?\n    \}/, "heroWithRef");
const linkDef = grab(/window\.heroLinkify = function\(text\)\{[\s\S]*?\n    \};/, "heroLinkify");

function build(ref) {
  const window = {};
  const getRef = () => ref;
  eval(escDef + "\n" + refDef + "\n" + linkDef);
  return window.heroLinkify;
}

let failed = 0;
function eq(got, want, label) {
  if (got !== want) { failed++; console.error(`FAIL ${label}\n  got:  ${JSON.stringify(got)}\n  want: ${JSON.stringify(want)}`); }
  else console.log(`ok   ${label}`);
}
const P = "https://myhealingoasis.com/begin/product/coq10";
const a = (href, text) => `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;

eq(build("janedoe")(P), a(P + "?ref=janedoe", P), "product link carries the referral");
eq(build("janedoe")(`[CoQ10](${P})`), "[CoQ10](" + a(P + "?ref=janedoe", P) + ")",
   "markdown link: the URL inside the brackets carries it");
eq(build("")(P), a(P, P), "no referral: link unchanged");
eq(build("bad ref<x>")(P), a(P, P), "an invalid referral is never written");
eq(build("janedoe")("https://illtowell.com/begin/match"), a("https://illtowell.com/begin/match", "https://illtowell.com/begin/match"),
   "a non-product link is unchanged");
eq(build("janedoe")(P + "?ref=other"), a(P + "?ref=other", P + "?ref=other"), "an existing ref is kept");
eq(build("janedoe")(P + "?utm_source=x&y=1"), a(P + "?utm_source=x&amp;y=1&amp;ref=janedoe", P + "?utm_source=x&amp;y=1"),
   "a query string gets &ref");

if (failed) { console.error(`${failed} failed`); process.exit(1); }
console.log("all passed");
