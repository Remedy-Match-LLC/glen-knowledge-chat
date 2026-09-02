// tests/test_portal_chat_never_navigates.js
// Run: node tests/test_portal_chat_never_navigates.js
//
// Final review I4, a design change on Glen's ruling. sendChatMessage() used to
// route BEFORE answering: on a routeIntent match it opened the door, wrote
// "Taking you to X." and returned, so the client's message was never sent and
// never answered. Adversarial probing found 119 of 147 sentences that should have
// been answered in prose were navigated instead. The worst:
//
//   "I need emotional support for what I am going through, not another product."
//       routed to the shop, and the sentence was discarded.
//   "I woke up with a symptom I have never had before and I am scared."
//       routed to solutions.
//   "I owe my recovery to the biofield work, honestly."
//       routed to billing.
//
// routeIntent is unchanged and keeps its own tests. What changed is what the page
// does with the result: the concierge always answers, and a match only adds a link
// next to the answer. A wrong link is ignorable; a wrong navigation that eats the
// question is not.
//
// So this file executes sendChatMessage() against a fake DOM and a fake streaming
// fetch, and asserts on what happened to the client's message. A source grep
// cannot tell whether a message was sent.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const page = fs.readFileSync(path.join(ROOT, 'static', 'client-portal.html'), 'utf8');
const PortalShell = require(path.join(ROOT, 'static', 'js', 'portal-shell.js'));

function fnSource(name) {
  const at = page.indexOf('\nfunction ' + name + '(');
  const alt = at === -1 ? page.indexOf('\nasync function ' + name + '(') : at;
  assert.ok(alt !== -1, 'function ' + name + '() not found');
  const end = page.indexOf('\n}', alt);
  assert.ok(end !== -1, 'unterminated function ' + name + '()');
  return page.slice(alt, end + 2);
}

// --- fake DOM ---------------------------------------------------------------
function makeEl(tag) {
  return {
    tagName: tag, className: '', textContent: '', value: '', type: '',
    disabled: false, hidden: false, href: '',
    children: [], listeners: {}, scrollTop: 0, scrollHeight: 0,
    appendChild: function (c) { this.children.push(c); return c; },
    addEventListener: function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    focus: function () {}
  };
}

// --- fake streaming fetch ---------------------------------------------------
function sseFetch(answer, calls) {
  return function (url, opts) {
    calls.push({ url: url, body: JSON.parse(opts.body) });
    const frames = [
      'data: ' + JSON.stringify({ token: answer }) + '\n',
      'data: ' + JSON.stringify({ done: true }) + '\n'
    ];
    let i = 0;
    const enc = new TextEncoder();
    return Promise.resolve({
      ok: true,
      body: { getReader: function () {
        return { read: function () {
          return Promise.resolve(i < frames.length
            ? { done: false, value: enc.encode(frames[i++]) }
            : { done: true });
        } };
      } }
    });
  };
}

async function run(query, opts) {
  opts = opts || {};
  const els = {
    chatInput: makeEl('input'), chatSend: makeEl('button'), chatMsgs: makeEl('div')
  };
  els.chatInput.value = query;
  const calls = [];
  const doorsOpened = [];
  const window = {
    PortalShell: PortalShell,
    __portalView: { shell_enabled: opts.shell !== false },
    showDoor: function (k) { doorsOpened.push(k); }
  };
  const document = {
    getElementById: function (id) { return els[id] || null; },
    createElement: makeEl
  };
  const sandbox = {
    window: window, document: document, calls: calls, doorsOpened: doorsOpened,
    fetch: sseFetch(opts.answer || 'Here is the answer.', calls),
    token: 'TOK', chatHistory: [], TextDecoder: TextDecoder,
    renderSuggestion: function () { return null; },
    renderRelated: function () { return null; },
    loadCart: function () {}
  };
  const names = Object.keys(sandbox);
  const body = [
    fnSource('appendChatBubble'),
    fnSource('appendChatDoorLink'),
    fnSource('sendChatMessage'),
    'return sendChatMessage();'
  ].join('\n');
  // eslint-disable-next-line no-new-func
  await new Function(names.join(','), body).apply(null, names.map(function (n) { return sandbox[n]; }));
  return { els: els, calls: calls, doorsOpened: doorsOpened };
}

function bubbles(r) {
  return r.els.chatMsgs.children.filter(function (c) {
    return String(c.className).indexOf('chat-bubble') === 0;
  });
}
function doorLinks(r) {
  return r.els.chatMsgs.children
    .filter(function (c) { return c.className === 'chat-related'; })
    .map(function (row) { return row.children[0]; })
    .filter(function (b) { return b && String(b.className).indexOf('chat-door-link') !== -1; });
}

(async function () {
  // -------------------------------------------------------------------------
  // 1. The three sentences from the review. Each still matches routeIntent, and
  //    each must reach the concierge and be answered anyway.
  // -------------------------------------------------------------------------
  const HARM = [
    'I need emotional support for what I am going through, not another product.',
    'I woke up with a symptom I have never had before and I am scared.',
    'I owe my recovery to the biofield work, honestly.'
  ];
  for (const q of HARM) {
    assert.ok(PortalShell.routeIntent(q),
      'fixture check: ' + JSON.stringify(q) + ' must still match routeIntent, ' +
      'otherwise this case proves nothing about what a match does');
    const r = await run(q);
    assert.strictEqual(r.calls.length, 1,
      JSON.stringify(q) + ' was never sent to the concierge');
    assert.strictEqual(r.calls[0].body.query, q,
      'the client\'s own words must reach the concierge, unaltered');
    assert.deepStrictEqual(r.doorsOpened, [],
      JSON.stringify(q) + ' navigated away instead of being answered');
    const said = bubbles(r).map(function (b) { return b.textContent; });
    assert.ok(said.indexOf(q) !== -1, 'the client\'s message must appear in the thread');
    assert.ok(said.indexOf('Here is the answer.') !== -1, 'the answer must appear in the thread');
    assert.ok(!said.some(function (t) { return /^Taking you to /.test(t); }),
      'nothing may announce a navigation any more');
  }

  // -------------------------------------------------------------------------
  // 2. A genuine door question: still answered, and offered the door as a link.
  // -------------------------------------------------------------------------
  {
    const r = await run('where is my invoice');
    assert.strictEqual(r.calls.length, 1, 'a door question is still a question');
    assert.deepStrictEqual(r.doorsOpened, [], 'a match must not navigate');
    const links = doorLinks(r);
    assert.strictEqual(links.length, 1, 'a matched question must offer exactly one door link');
    assert.strictEqual(links[0].textContent, 'Open Billing');
    assert.strictEqual(links[0].type, 'button');
    // the link is a real control: clicking it is what opens the door
    links[0].listeners.click[0]();
    assert.deepStrictEqual(r.doorsOpened, ['billing'],
      'clicking the link must open that door');
    // and it comes after the answer, not before it
    const kinds = r.els.chatMsgs.children.map(function (c) { return c.className; });
    assert.ok(kinds.lastIndexOf('chat-related') > kinds.lastIndexOf('chat-bubble assistant'),
      'the door link must land after the answer, not in place of it');
  }

  // -------------------------------------------------------------------------
  // 3. No match: answered, no link.
  // -------------------------------------------------------------------------
  {
    const q = 'is Dr. Glen in Hawaii';
    assert.strictEqual(PortalShell.routeIntent(q), null, 'fixture check: this must not match');
    const r = await run(q);
    assert.strictEqual(r.calls.length, 1);
    assert.strictEqual(doorLinks(r).length, 0, 'an unmatched question must offer no door');
  }

  // -------------------------------------------------------------------------
  // 4. Shell off, which is production today: answered, and no door link at all,
  //    since there are no doors to open.
  // -------------------------------------------------------------------------
  {
    const r = await run('where is my invoice', { shell: false });
    assert.strictEqual(r.calls.length, 1, 'the concierge answers with the flag off, as always');
    assert.strictEqual(doorLinks(r).length, 0, 'no door link may render with the shell off');
    assert.deepStrictEqual(r.doorsOpened, []);
  }

  // -------------------------------------------------------------------------
  // 5. The navigate-and-return shape must not come back. Checked on
  //    comment-stripped source, since the comment above the code describes it.
  // -------------------------------------------------------------------------
  {
    const live = fnSource('sendChatMessage').split('\n')
      .filter(function (l) { return l.trim().slice(0, 2) !== '//'; }).join('\n');
    assert.strictEqual(live.indexOf('window.showDoor('), -1,
      'sendChatMessage() must not open a door itself; the client clicks the link');
    assert.strictEqual(live.indexOf('Taking you to'), -1,
      'the navigation announcement must be gone');
    assert.ok(/routeIntent\(query\)/.test(live),
      'routeIntent is kept: only what is done with the result changed');
  }

  console.log('test_portal_chat_never_navigates: ok (' + (HARM.length + 3) +
              ' cases, every message reaches the concierge)');
})().catch(function (e) { console.error(e); process.exit(1); });
