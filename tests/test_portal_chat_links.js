// tests/test_portal_chat_links.js
// Run: node tests/test_portal_chat_links.js
//
// Agnes Verches, 2026-11-12: the portal chat told her "truly.vip/help" for her
// invoices. It was not clickable, because chat bubbles were drawn with textContent.
// This runs the REAL setChatText from static/client-portal.html against a tiny DOM.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const page = fs.readFileSync(path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const start = page.indexOf('var CHAT_LINK_RE');
const end = page.indexOf('window.setChatText = setChatText;');
assert.ok(start !== -1 && end > start, 'setChatText region not found');

function Node(tag, text) { return { tag: tag, text: text || '', attrs: {}, children: [] }; }
const document = {
  createTextNode(t) { return Node('#text', t); },
  createElement(tag) {
    const n = Node(tag);
    Object.defineProperty(n, 'textContent', { set(v) { this.text = v; }, get() { return this.text; } });
    Object.defineProperty(n, 'href', { set(v) { this.attrs.href = v; }, get() { return this.attrs.href; } });
    return n;
  }
};
const ctx = { document: document };
vm.createContext(ctx);
vm.runInContext(page.slice(start, end) + '\nthis.setChatText = setChatText;', ctx);

function el() {
  const e = Node('div');
  e.appendChild = function (c) { this.children.push(c); };
  e.removeChild = function (c) { this.children.splice(this.children.indexOf(c), 1); };
  Object.defineProperty(e, 'firstChild', { get() { return this.children[0] || null; } });
  return e;
}
function render(text) { const e = el(); ctx.setChatText(e, text); return e; }
function links(e) { return e.children.filter(c => c.tag === 'a').map(c => [c.attrs.href, c.text]); }
function plain(e) { return e.children.map(c => c.text).join(''); }

// 1. A full link is clickable, and a trailing full stop stays outside it.
let e = render('Open https://myhealingoasis.com/portal/me#orders.');
assert.deepStrictEqual(links(e), [['https://myhealingoasis.com/portal/me#orders',
                                   'https://myhealingoasis.com/portal/me#orders']]);
assert.strictEqual(plain(e), 'Open https://myhealingoasis.com/portal/me#orders.');

// 2. The exact failure: a bare address on our own domain gets https://.
e = render('Reach us at truly.vip/help.');
assert.deepStrictEqual(links(e), [['https://truly.vip/help', 'truly.vip/help']]);
assert.strictEqual(plain(e), 'Reach us at truly.vip/help.');
e = render('See myhealingoasis.com/portal/me#orders, then pay.');
assert.deepStrictEqual(links(e), [['https://myhealingoasis.com/portal/me#orders',
                                   'myhealingoasis.com/portal/me#orders']]);

// 3. Someone else's bare domain stays plain text.
e = render('Try example.com/help today');
assert.deepStrictEqual(links(e), []);
assert.strictEqual(plain(e), 'Try example.com/help today');

// 4. An email address on our domain is not turned into a link.
e = render('Write to support@myhealingoasis.com for help.');
assert.deepStrictEqual(links(e), []);
assert.strictEqual(plain(e), 'Write to support@myhealingoasis.com for help.');

// 5. Markup in the reply stays text: nothing is parsed as HTML, and only http(s) links.
e = render('<img src=x onerror=alert(1)> javascript:alert(1)');
assert.deepStrictEqual(links(e), []);
assert.strictEqual(plain(e), '<img src=x onerror=alert(1)> javascript:alert(1)');

// 6. Re-rendering (each streamed token) replaces the old content.
e = el(); ctx.setChatText(e, 'one truly.vip/E4L'); ctx.setChatText(e, 'two');
assert.strictEqual(plain(e), 'two');

// 7. Two links in one reply, text between them kept.
e = render('Scan: https://Truly.VIP/E4L or orders: myhealingoasis.com/portal/me#orders');
assert.strictEqual(links(e).length, 2);
assert.strictEqual(plain(e), 'Scan: https://Truly.VIP/E4L or orders: myhealingoasis.com/portal/me#orders');

// 8. Another host that starts with our domain is not ours, and is not linked.
e = render('Go to truly.vip.example.com/help now');
assert.deepStrictEqual(links(e), []);
assert.strictEqual(plain(e), 'Go to truly.vip.example.com/help now');
e = render('Or truly.vipers.com today');
assert.deepStrictEqual(links(e), []);

// 9. A bare address keeps its fragment or query.
e = render('Open myhealingoasis.com#orders now');
assert.deepStrictEqual(links(e), [['https://myhealingoasis.com#orders', 'myhealingoasis.com#orders']]);

// 10. A bracket the link opened stays in it; prose brackets stay out.
e = render('See https://example.com/info_(billing) please');
assert.deepStrictEqual(links(e), [['https://example.com/info_(billing)', 'https://example.com/info_(billing)']]);
e = render('(see https://truly.vip/E4L).');
assert.deepStrictEqual(links(e), [['https://truly.vip/E4L', 'https://truly.vip/E4L']]);
assert.strictEqual(plain(e), '(see https://truly.vip/E4L).');

// 11. Streaming draws plain text; links are drawn once the reply is done, before the
// voice controls attach. Restored history is linked; a user bubble is not.
assert.ok(/assistantBubble\.textContent = assistantText;/.test(page), 'stream should draw plain text');
const done = page.indexOf('else if(evt.done)');
assert.ok(done !== -1, 'done branch not found');
const doneSrc = page.slice(done, page.indexOf('appendChatDoorLink(routedDoor', done));
const linkAt = doneSrc.indexOf('setChatText(assistantBubble, assistantText)');
assert.ok(linkAt !== -1, 'done branch does not draw links');
assert.ok(linkAt < doneSrc.indexOf('PortalVoice.onReply'), 'links must be drawn before voice controls attach');
assert.ok(/if\(cls === "user"\) body\.textContent = entry\.content \|\| "";\s*else setChatText\(body/.test(page),
          'history bubbles not linked');
console.log('OK');
