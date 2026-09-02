// tests/test_portal_one_chat.js
// Run: node tests/test_portal_one_chat.js
//
// Task 7 (portal-shell-ia): with the shell on, the portal has exactly one chat
// surface, the "Messages & Order Help" card. The floating launcher stops being an
// entry point and the mentor's voice controls move onto the card, so nothing that
// spoke before goes silent.
//
// The two renderers and the mentor module are EXECUTED here, not grepped. A source
// regex passes on a commented-out implementation, and this plan has already had one
// green test that asserted nothing. Only the CSS rule and the "is the markup still
// unconditional" checks read source, because neither can be executed without a DOM.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const page = fs.readFileSync(path.join(ROOT, 'static', 'client-portal.html'), 'utf8');
const mentorSrc = fs.readFileSync(path.join(ROOT, 'static', 'portal-mentor.js'), 'utf8');
const css = (page.match(/<style>[\s\S]*?<\/style>/g) || []).join('\n');

// ---------------------------------------------------------------------------
// A very small DOM, big enough to run the real code against.
// ---------------------------------------------------------------------------
function El(id, tag) {
  const el = {
    id: id || '', tagName: (tag || 'div').toUpperCase(),
    hidden: false, checked: false, value: '', textContent: '', innerHTML: '',
    className: '', children: [], listeners: {}, attrs: {}, parent: null,
    dataset: {}, scrollTop: 0, scrollHeight: 0, offsetParent: {}, disabled: false,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { if (on === undefined) on = !this._s.has(c); if (on) this._s.add(c); else this._s.delete(c); }
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    querySelectorAll() { return []; },
    focus() {},
    closest(sel) { let n = this; while (n) { if (n._sel === sel) return n; n = n.parent; } return null; }
  };
  return el;
}

function makeDoc(ids) {
  const reg = {};
  ids.forEach(function (id) { reg[id] = El(id); });
  return {
    reg: reg,
    hidden: false,
    getElementById(id) { return reg[id] || null; },
    createElement(tag) { return El('', tag); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}
  };
}

const FLOATING = ['mentorLauncher', 'mentorPanel', 'mentorClose', 'mentorInput', 'mentorSend',
  'mentorMsgs', 'mentorMic', 'mentorSpeaker', 'mentorAutoGuide', 'mentorContext',
  'mentorContinuous', 'mentorContinuousWrap'];
const CARD = ['chatMsgs', 'chatInput', 'chatSend', 'chatMic', 'chatSpeaker',
  'chatAutoGuide', 'chatContext', 'chatContinuous', 'chatContinuousWrap'];

// Runs the real portal-mentor.js against a document holding the given element ids.
function runMentor(ids, opts) {
  opts = opts || {};
  const doc = makeDoc(ids);
  doc.reg.mentorPanel && (doc.reg.mentorPanel.hidden = true);
  const sandbox = {
    console: console,
    document: doc,
    location: { hash: '' },
    localStorage: { _v: {}, getItem(k) { return this._v[k] || null; }, setItem(k, v) { this._v[k] = v; } },
    chatHistory: opts.chatHistory || [],
    // run timers inline so behaviour is observable in one tick
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
    addEventListener() {},
    sendChatMessage: opts.sendChatMessage || undefined
  };
  // A faithful SpeechRecognition: start() really does fire onstart and stop()
  // really does fire onend. Without that the module never sees micActivated go
  // true, syncContinuousControl() disables continuous mode, and the fake quietly
  // fails code that is correct.
  sandbox.SpeechRecognition = function () {
    const self = this;
    this.stopped = 0; this.live = false;
    this.start = function () { self.live = true; if (self.onstart) self.onstart(); };
    this.stop = function () {
      self.stopped++;
      if (self.live) { self.live = false; if (self.onend) self.onend(); }
    };
    sandbox.__recognition = this;          // so the test can fire a transcript
  };
  if (opts.deferTimers) {
    // real timers do not run inline. Deferring them is the only way to observe the
    // state where a continuous restart is SCHEDULED but recognition is not running.
    const pending = []; let nextId = 1;
    sandbox.__pending = pending;
    sandbox.setTimeout = function (fn) { const id = nextId++; pending.push({ id: id, fn: fn }); return id; };
    sandbox.clearTimeout = function (id) {
      for (let i = 0; i < pending.length; i++) { if (pending[i].id === id) { pending.splice(i, 1); return; } }
    };
  }
  if (opts.speech) {
    // a speech engine that actually records what was said, so "did it speak?" is
    // an observation rather than an assumption
    sandbox.speechSynthesis = {
      spoken: [], cancelled: 0,
      cancel() { this.cancelled++; },
      speak(u) { this.spoken.push(u.text); if (u.onend) u.onend(); }
    };
    sandbox.SpeechSynthesisUtterance = function (t) { this.text = t; };
    sandbox.__synth = sandbox.speechSynthesis;
  }
  vm.createContext(sandbox);
  sandbox.window = sandbox;
  vm.runInContext(mentorSrc, sandbox, { filename: 'portal-mentor.js' });
  return { doc: doc, win: sandbox };
}

// ---------------------------------------------------------------------------
// 1. Practitioner replies stay distinguishable in the surviving surface.
// ---------------------------------------------------------------------------
const repopSrc = /function repopulateChatHistory\(\)\{[\s\S]*?\n\}/.exec(page);
assert.ok(repopSrc, 'repopulateChatHistory not found in the page');

function renderThread(history) {
  const msgs = El('chatMsgs');
  const sandbox = {
    chatHistory: history,
    document: {
      getElementById(id) { return id === 'chatMsgs' ? msgs : null; },
      createElement(tag) { return El('', tag); }
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(repopSrc[0] + '\nrepopulateChatHistory();', sandbox);
  return msgs;
}

const thread = renderThread([
  { role: 'client', author: '', content: 'Is the tincture safe with my magnesium?' },
  { role: 'assistant', author: '', content: 'Here is what the research says.' },
  { role: 'practitioner', author: 'Dr. Glen', content: 'Take it with food, and skip the evening dose.' }
]);
assert.strictEqual(thread.children.length, 3);
assert.strictEqual(thread.children[0].className, 'chat-bubble user');
assert.strictEqual(thread.children[1].className, 'chat-bubble assistant');
assert.strictEqual(thread.children[2].className, 'chat-bubble practitioner',
  'a practitioner reply must get its own class, not be collapsed into assistant');
const byline = thread.children[2].children[0];
assert.ok(byline, 'a practitioner reply must carry an author byline');
assert.strictEqual(byline.className, 'chat-author');
assert.strictEqual(byline.textContent, 'Dr. Glen',
  'the byline must name the person who wrote the reply');
// the AI reply has no byline to steal, and the client bubble never gets one
assert.strictEqual(thread.children[1].children.length, 1);
assert.strictEqual(thread.children[0].children.length, 1);

// ...and the mentor must not paint over it. Its own renderer collapses every
// non-user role into "assistant", which is the defect. On the card it must not run.
{
  const r = runMentor(FLOATING.concat(CARD), {
    chatHistory: [{ role: 'practitioner', author: 'Dr. Glen', content: 'Take it with food.' }]
  });
  const cardMsgs = r.doc.reg.chatMsgs;
  cardMsgs.innerHTML = 'SENTINEL';
  cardMsgs.appendChild(El('kept'));
  assert.strictEqual(typeof r.win.syncMentorHistory, 'function');
  r.win.syncMentorHistory();
  assert.strictEqual(cardMsgs.innerHTML, 'SENTINEL',
    'syncMentorHistory must not wipe the card thread, which owns the practitioner byline');
  assert.strictEqual(cardMsgs.children.length, 1,
    'syncMentorHistory must not re-render the card thread without bylines');
}

// ---------------------------------------------------------------------------
// 2. One entry point under the shell.
// ---------------------------------------------------------------------------
// (a) the mechanism: a CSS rule keyed off the body.has-shell class the mount block adds
const hideRule = /body\.has-shell\s+\.mentor-launcher\s*,\s*body\.has-shell\s+\.mentor-panel\s*\{([^}]*)\}/.exec(css);
assert.ok(hideRule, 'no body.has-shell rule hides the floating launcher and panel');
assert.ok(/display\s*:\s*none/.test(hideRule[1]),
  'the shell rule must actually hide the launcher and panel');
assert.ok(page.indexOf("classList.add('has-shell')") !== -1,
  'nothing adds the has-shell class the hide rule is keyed off');

// (b) the behaviour: with the card cluster present the mentor binds the card, and
// the floating launcher gets no click handler at all, so it is not an entry point
// even for a client who somehow reaches it.
{
  const r = runMentor(FLOATING.concat(CARD));
  assert.ok(!r.doc.reg.mentorLauncher.listeners.click,
    'the floating launcher must not be wired as an entry point under the shell');
  assert.ok(!r.doc.reg.mentorInput.listeners.keydown,
    'the floating composer must not be wired under the shell');
  assert.ok(r.doc.reg.chatMic.listeners.click && r.doc.reg.chatMic.listeners.click.length === 1,
    'the card microphone must be wired under the shell');
  assert.ok(!r.doc.reg.chatSend.listeners.click,
    'the mentor must not bind the card Send button, the page already owns it');
  assert.strictEqual(r.win.PortalVoice.armed(), true,
    'PortalVoice must be armed once the card host is bound');
}

// ---------------------------------------------------------------------------
// 3. Flag off changes nothing.
// ---------------------------------------------------------------------------
assert.strictEqual(page.split('id="mentorLauncher"').length - 1, 1,
  'the floating launcher markup must still be in the page, exactly once');
assert.strictEqual(page.split('id="mentorPanel"').length - 1, 1,
  'the floating panel markup must still be in the page, exactly once');
// unconditional: it sits in the static body, ahead of every line of render() code
assert.ok(page.indexOf('id="mentorLauncher"') < page.indexOf('function render(d, v)'),
  'the launcher must be static body markup, not rendered conditionally');
// and nothing hides it except the shell rule
const launcherHides = css.split('}').filter(function (b) {
  return /\.mentor-launcher/.test(b) && /display\s*:\s*none/.test(b);
});
assert.strictEqual(launcherHides.length, 1,
  'exactly one rule may hide the floating launcher');
assert.ok(/body\.has-shell/.test(launcherHides[0]),
  'the launcher may only be hidden under the shell, never unconditionally');

// behaviour with the flag off: no card cluster exists, so the mentor still owns
// the floating panel exactly as it does in production today.
{
  const r = runMentor(FLOATING, { speech: true });
  assert.ok(r.doc.reg.mentorLauncher.listeners.click,
    'with the flag off the launcher must still open the mentor');
  assert.ok(r.doc.reg.mentorSend.listeners.click,
    'with the flag off the floating composer must still send');
  assert.ok(r.doc.reg.mentorInput.listeners.keydown);
  assert.strictEqual(r.win.PortalVoice.armed(), false,
    'PortalVoice must stay disarmed with the flag off, so replies keep using TTS as before');

  // and the floating mentor still opens, greets and closes the way it does in
  // production today. This host was refactored, so drive it, do not assume it.
  const panel = r.doc.reg.mentorPanel;
  assert.strictEqual(panel.hidden, true, 'the panel starts closed');
  r.doc.reg.mentorLauncher.listeners.click[0]();
  assert.strictEqual(panel.hidden, false, 'the launcher must still open the floating panel');
  assert.strictEqual(r.doc.reg.mentorLauncher.getAttribute('aria-expanded'), 'true');
  const greeting = r.doc.reg.mentorMsgs.children[0];
  assert.ok(greeting, 'the floating panel must still greet the client on open');
  assert.strictEqual(greeting.className, 'mentor-bubble assistant',
    'the floating panel keeps its own bubble class, not the card one');

  // The host-visibility guard added for the load-time window must be a no-op here.
  // hostHidden() reports the floating panel's own hidden flag, which is exactly
  // what every flag-off speak() call site already guarantees, so speech is
  // unchanged: the panel is open, so it speaks.
  const spokenBefore = r.win.__synth.spoken.length;
  r.doc.reg.mentorContinuous.checked = true;
  r.doc.reg.mentorContinuous.listeners.change[0]();
  assert.ok(r.win.__synth.spoken.length > spokenBefore,
    'with the flag off the open floating panel must still speak, exactly as today');

  // ...and the teardown must be unreachable. With the flag off no card cluster is
  // ever rendered, so resolveHost() can only return the floating host and
  // h.card !== next.card can never become true. Re-attaching must change nothing.
  const stopped = r.win.__recognition.stopped;
  const cancelled = r.win.__synth.cancelled;
  r.win.mentorAttachHost();
  assert.strictEqual(panel.hidden, false, 'a flag-off re-attach must not close the panel');
  assert.strictEqual(r.doc.reg.mentorContinuous.checked, true,
    'a flag-off re-attach must not end a conversation');
  assert.strictEqual(r.win.__recognition.stopped, stopped,
    'a flag-off re-attach must not release the microphone');
  assert.strictEqual(r.win.__synth.cancelled, cancelled,
    'a flag-off re-attach must not cancel speech');

  r.doc.reg.mentorLauncher.listeners.click[0]();
  assert.strictEqual(panel.hidden, true, 'the launcher must still close the floating panel');
}

// ---------------------------------------------------------------------------
// 4. The voice controls have a home under the shell.
// ---------------------------------------------------------------------------
const tpl = /const _chatVoice = [\s\S]*?const _askCard = `[\s\S]*?`;/.exec(page);
assert.ok(tpl, 'the ask card template was not found');
const buildCard = new Function('v', 'window', tpl[0] + '\nreturn _askCard;');
const SHELL_UP = { PortalShell: {} };

const cardOn = buildCard({ shell_enabled: true }, SHELL_UP);
const cardOff = buildCard({ shell_enabled: false }, SHELL_UP);
// the flag alone is not enough: the shell mount, which is what hides the floating
// launcher, is skipped when portal-shell.js failed to load. Rendering the card
// controls then would leave the client with a launcher that no longer does anything.
assert.strictEqual(buildCard({ shell_enabled: true }, {}), cardOff,
  'the card controls must ride the same predicate as the shell mount');

['chatMic', 'chatSpeaker', 'chatAutoGuide', 'chatContinuous', 'chatContinuousWrap'].forEach(function (id) {
  assert.ok(cardOn.indexOf('id="' + id + '"') !== -1,
    'the card is missing the ' + id + ' control under the shell');
  assert.strictEqual(cardOff.indexOf('id="' + id + '"'), -1,
    id + ' must not appear with the flag off');
});
// the cluster is inside the card, not floating next to it
assert.ok(cardOn.indexOf('id="chatCard"') !== -1 && cardOn.trim().slice(-6) === '</div>');
assert.ok(cardOn.indexOf('id="chatVoice"') > cardOn.indexOf('id="chatCard"'));
// the card itself is unchanged with the flag off
['chatCard', 'chatMsgs', 'chatInput', 'chatSend'].forEach(function (id) {
  assert.ok(cardOff.indexOf('id="' + id + '"') !== -1, id + ' disappeared from the card');
});
// Task 8 (portal-shell-ia): under the shell the composer (chatInput/chatSend) moves
// to the top of the page, so the card must NOT also render it, only the thread and
// the voice cluster stay here. Two elements sharing id="chatInput" would silently
// wire Send to the wrong node.
['chatCard', 'chatMsgs'].forEach(function (id) {
  assert.ok(cardOn.indexOf('id="' + id + '"') !== -1, id + ' disappeared from the card under the shell');
});
['chatInput', 'chatSend'].forEach(function (id) {
  assert.strictEqual(cardOn.indexOf('id="' + id + '"'), -1,
    id + ' must not appear on the card under the shell, it moved to the top composer');
});
assert.strictEqual(cardOff, buildCard({}, SHELL_UP), 'a payload without the flag must render the legacy card');
assert.strictEqual(cardOff.indexOf('chat-voice'), -1, 'no voice markup may leak with the flag off');
// client-facing copy rules
assert.ok(!/—|--/.test(cardOn), 'the voice cluster copy must not contain an em dash');
assert.ok(!/patient/i.test(cardOn), 'client-facing copy says client, never patient');

// the page has to hand the rebuilt card to the mentor after every render(), and the
// card's sender has to route a finished reply through the mentor's voice controls.
// Comment lines are stripped first: a comment naming the call would otherwise
// satisfy an assertion about the call.
const code = page.split('\n').filter(function (l) { return l.trim().slice(0, 2) !== '//'; }).join('\n');
assert.ok(/v && v\.shell_enabled && typeof window\.mentorAttachHost === "function"/.test(code),
  'render() must re-bind the mentor to the rebuilt card, and only under the shell');
assert.ok(/window\.PortalVoice && window\.PortalVoice\.armed\(\)/.test(code),
  'the card sender must route a finished reply through the mentor voice controls');

// and the controls actually drive speech once bound.
{
  const r = runMentor(FLOATING.concat(CARD));
  assert.strictEqual(r.doc.reg.chatMic.hidden, false,
    'the card microphone must stay visible when the browser supports speech');
  assert.ok(r.doc.reg.chatSpeaker.listeners.click, 'spoken replies need an off switch on the card');
  assert.ok(r.doc.reg.chatContinuous.listeners.change, 'continuous conversation needs a control on the card');
  assert.ok(r.doc.reg.chatAutoGuide.listeners.change, 'the automatic guide needs a control on the card');

  // dictation lands in the card composer, the input the card's own sender reads
  r.win.__recognition.onresult({ results: [[{ transcript: 'is the tincture safe with magnesium' }]] });
  assert.strictEqual(r.doc.reg.chatInput.value, 'is the tincture safe with magnesium',
    'the microphone must dictate into the card composer');
  assert.strictEqual(r.doc.reg.mentorInput.value, '',
    'dictation must not land in the hidden floating composer');

  // spoken replies are on by default and the card speaker button turns them off
  const spoken = [];
  r.win.TTS = { attach: function (b, t) { spoken.push(['silent', t]); },
                attachAndSpeak: function (b, t) { spoken.push(['spoken', t]); } };
  r.win.PortalVoice.onReply(El('bubble'), 'Take it with food.');
  assert.deepStrictEqual(spoken, [['spoken', 'Take it with food.']],
    'a reply must still be spoken aloud under the shell');
  r.doc.reg.chatSpeaker.listeners.click[0]();
  r.win.PortalVoice.onReply(El('bubble'), 'Skip the evening dose.');
  assert.deepStrictEqual(spoken[1], ['silent', 'Skip the evening dose.'],
    'the card speaker button must be a real off switch for spoken replies');

  // turning continuous conversation on says so in the card thread, not in the
  // floating panel nobody can see any more
  r.doc.reg.chatContinuous.checked = true;
  r.doc.reg.chatContinuous.listeners.change[0]();
  const notice = r.doc.reg.chatMsgs.children[r.doc.reg.chatMsgs.children.length - 1];
  assert.ok(notice && notice.textContent.indexOf('Continuous conversation is on') === 0,
    'the continuous conversation notice must land in the card thread');
  assert.strictEqual(notice.className, 'chat-bubble assistant');
  assert.strictEqual(r.doc.reg.mentorMsgs.children.length, 0,
    'nothing may be written into the floating panel once the card is the host');
}

// ---------------------------------------------------------------------------
// 5. window.mentorPageChanged still exists, and works on the card host.
// ---------------------------------------------------------------------------
assert.ok(page.indexOf('mentorPageChanged') !== -1, 'the routers no longer call mentorPageChanged');
{
  const r = runMentor(FLOATING);
  assert.strictEqual(typeof r.win.mentorPageChanged, 'function',
    'window.mentorPageChanged must survive: both showTab and showDoor call it');
}
{
  const r = runMentor(FLOATING.concat(CARD));
  assert.strictEqual(typeof r.win.mentorPageChanged, 'function');
  // it re-reads the page context onto whichever host is bound
  r.win.mentorPageChanged('cart');
  assert.ok(/^Aware you’re viewing /.test(r.doc.reg.chatContext.textContent),
    'the page context must be written onto the card, not the hidden panel');
  assert.strictEqual(r.doc.reg.mentorContext.textContent, '',
    'the hidden panel must not be the one being updated');

  // with the automatic guide on, the guidance lands in the card thread and points
  // the client at the card, not at a launcher that is no longer there.
  r.doc.reg.chatAutoGuide.checked = true;
  r.win.mentorPageChanged('orders');
  const last = r.doc.reg.chatMsgs.children[r.doc.reg.chatMsgs.children.length - 1];
  assert.ok(last, 'the automatic guide must have somewhere to speak on the card');
  assert.strictEqual(last.className, 'chat-bubble assistant');
  assert.ok(last.textContent.indexOf('your orders and invoices') !== -1);
  assert.ok(last.textContent.indexOf('Ask me here') !== -1,
    'the guide must not tell the client to open a launcher the shell has removed');
  assert.ok(last.textContent.indexOf('Open me') === -1);
}

// ---------------------------------------------------------------------------
// 6. The load-time window: the floating host is bound and live before the card
//    exists, even with the shell on, and switching away must tear it down.
// ---------------------------------------------------------------------------
// portal-mentor.js runs at script load. At that moment #app is still empty and
// body.has-shell is not set, because render() only runs once load()'s fetches
// resolve. So resolveHost() finds no card, binds the floating panel, and for the
// length of that fetch the launcher is visible and fully working. The window
// cannot be closed: the page is static and shell_enabled only arrives with the
// payload. So the switch has to be safe instead.
//
// Every case above seeds the floating and card ids at the same instant, so none
// of them can reach this. This one models the real sequence.
{
  const r = runMentor(FLOATING, { speech: true });
  assert.strictEqual(r.win.PortalVoice.armed(), false,
    'before the payload arrives the floating panel is the only host there is');

  // the client opens it and starts a continuous voice session in that window
  r.doc.reg.mentorLauncher.listeners.click[0]();
  r.doc.reg.mentorContinuous.checked = true;
  r.doc.reg.mentorContinuous.listeners.change[0]();
  assert.strictEqual(r.doc.reg.mentorPanel.hidden, false, 'the panel is open in the load window');
  assert.ok(r.win.__synth.spoken.length > 0, 'and it is genuinely speaking');
  const cancelledBefore = r.win.__synth.cancelled;
  const stoppedBefore = r.win.__recognition.stopped;

  // the payload lands, render() builds the card, the page re-attaches
  CARD.forEach(function (id) { r.doc.reg[id] = El(id); });
  r.win.mentorAttachHost();

  assert.strictEqual(r.win.PortalVoice.armed(), true, 'the card must take the host over');
  assert.ok(r.win.__synth.cancelled > cancelledBefore,
    'speech in flight must be cancelled when the host switches, not left talking');
  assert.ok(r.win.__recognition.stopped > stoppedBefore,
    'the microphone must be released when the host switches, not left listening');
  assert.strictEqual(r.doc.reg.mentorContinuous.checked, false,
    'continuous mode must not survive a host switch');
  assert.strictEqual(r.doc.reg.mentorPanel.hidden, true,
    'the floating panel must be closed when the card takes over');
  assert.strictEqual(r.doc.reg.mentorLauncher.getAttribute('aria-expanded'), 'false');
  assert.strictEqual(r.doc.reg.mentorMic.getAttribute('aria-pressed'), 'false',
    'the abandoned microphone must not still read as live');
}

// ...but a re-render of the SAME host is not a switch. render() rebuilds the card
// on every background poll, and tearing down there would cut off a client
// mid-conversation.
{
  const r = runMentor(FLOATING.concat(CARD), { speech: true });
  r.doc.reg.chatContinuous.checked = true;
  r.doc.reg.chatContinuous.listeners.change[0]();
  const stoppedBefore = r.win.__recognition.stopped;
  CARD.forEach(function (id) { r.doc.reg[id] = El(id); });   // render() runs again
  r.win.mentorAttachHost();
  assert.strictEqual(r.doc.reg.chatContinuous.checked, true,
    'a background re-render must not end a conversation in progress');
  assert.strictEqual(r.win.__recognition.stopped, stoppedBefore,
    'a background re-render must not release the microphone');
}

// The microphone can READ as active while a continuous restart is only scheduled,
// with recognition not yet running. Nothing in the stop path fires an onend then,
// so the teardown has to clear the indicator itself. Without this case the mic
// assertion above passes for the wrong reason: recognition.stop()'s own onend
// happens to repaint it.
{
  const r = runMentor(FLOATING, { speech: true, deferTimers: true });
  r.doc.reg.mentorLauncher.listeners.click[0]();
  r.doc.reg.mentorContinuous.checked = true;
  r.doc.reg.mentorContinuous.listeners.change[0]();
  assert.strictEqual(r.doc.reg.mentorMic.getAttribute('aria-pressed'), 'true',
    'a scheduled restart paints the microphone active');
  assert.strictEqual(r.win.__recognition.live, false,
    'recognition is not running yet, so stopping it fires no onend to repaint');
  assert.ok(r.win.__pending.length > 0, 'a restart really is queued');

  CARD.forEach(function (id) { r.doc.reg[id] = El(id); });
  r.win.mentorAttachHost();
  assert.strictEqual(r.doc.reg.mentorMic.getAttribute('aria-pressed'), 'false',
    'the abandoned microphone must not still read as live');
  assert.strictEqual(r.win.__pending.length, 0,
    'the queued restart must be cancelled, not left to fire at the dead host');
}

// ---------------------------------------------------------------------------
// 7. A surface the client cannot see must not speak.
// ---------------------------------------------------------------------------
// document.hidden only answers "is the tab in the background". Under the shell the
// card sits inside a [data-panel] section that is hidden whenever the client is at
// another door, and during the load window the bound host can be a panel that is
// about to disappear. hostHidden() is the question that actually matters.
{
  const r = runMentor(FLOATING.concat(CARD), { speech: true });
  const section = El('askSection');
  section._sel = '[data-panel]';
  section.hidden = true;
  r.doc.reg.chatMsgs.parent = section;      // the card is behind a closed door

  const before = r.win.__synth.spoken.length;
  r.doc.reg.chatContinuous.checked = true;
  r.doc.reg.chatContinuous.listeners.change[0]();
  assert.strictEqual(r.win.__synth.spoken.length, before,
    'the mentor must not speak from a surface the client cannot see');

  // and it does speak once that door is open, so this is a guard, not a mute
  section.hidden = false;
  r.doc.reg.chatContinuous.listeners.change[0]();
  assert.ok(r.win.__synth.spoken.length > before,
    'the guard must be about visibility, not a blanket silence');
}

// ---------------------------------------------------------------------------
// 8. Task 8 (portal-shell-ia): the composer moves to the top under the shell.
// ---------------------------------------------------------------------------
// (a) exactly one id="chatInput" TEMPLATE in the page source. The legacy card row
// (static/client-portal.html) and the top composer (static/js/portal-shell.js) are
// two different files, and only one of the two markup strings is ever chosen for a
// given build, so the page source itself must carry exactly one.
assert.strictEqual((page.match(/id="chatInput"/g) || []).length, 1,
  'exactly one id="chatInput" template must exist in the page source');

// (b) exactly one id="chatInput" in the RENDERED markup under the shell, built by
// actually EXECUTING both real renderers (buildCard above, and the real
// portal-shell.js), not by grepping source. A source-only count (a) would miss a
// duplicate that only appears once the shell mounts; this would catch it.
const PortalShell = require('../static/js/portal-shell.js');
const renderedUnderShell = cardOn + PortalShell.renderComposer();
assert.strictEqual((renderedUnderShell.match(/id="chatInput"/g) || []).length, 1,
  'exactly one chatInput must exist once the card and the top composer are both rendered');
// with the flag off the shell mount never runs, so the legacy card alone must
// carry the only one.
assert.strictEqual((cardOff.match(/id="chatInput"/g) || []).length, 1,
  'exactly one chatInput must exist with the flag off, on the legacy card alone');

// (c) position: the composer must sit OUTSIDE every [data-panel] section, which is
// what makes it present on every door, rather than trusting a class name. It is
// rendered into #portalShellMount, so that mount must sit ahead of #app in the
// static body, since every [data-panel] section lives inside #app.
const mountIdx = page.indexOf('id="portalShellMount"');
const appIdx = page.indexOf('id="app"');
assert.ok(mountIdx !== -1 && appIdx !== -1 && mountIdx < appIdx,
  '#portalShellMount must sit ahead of #app, outside every [data-panel] section');
// ...and the mount block that fills #portalShellMount really does call
// renderComposer(), extracted from the exact assignment rather than "the string
// appears somewhere in the file" (comment lines already stripped out of `code`).
const mountAssign = /shellMount\.innerHTML =[\s\S]*?;/.exec(code);
assert.ok(mountAssign, 'the shell-mount innerHTML assignment was not found');
assert.ok(/window\.PortalShell\.renderComposer\(\)/.test(mountAssign[0]),
  'the shell mount must render the composer into #portalShellMount');

// (d) flag off: the _hub routing line for the ask card is unchanged.
assert.ok(page.indexOf('if (_hub) { _askHtml += _askCard; } else { html += _askCard; }') !== -1,
  'the _hub routing line for the ask card must be unchanged');

// (e) no em dash anywhere in the composer copy
assert.ok(!/—|--/.test(PortalShell.renderComposer()), 'composer copy must not contain an em dash');

console.log('test_portal_one_chat: ok');
