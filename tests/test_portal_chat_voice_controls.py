"""Voice on the portal composer, and the five minute cap on continuous conversation.

Glen, 2026-09-16, after the chat fix landed: "It still needs a button for audio input."
Then: "Once audio in and out are working, then also add a button for continuous
conversation (2 way simultaneous - let's limit it to five minutes at a time so it doesn't
run wild.)" Then: "When 5 minutes is about to end in about 30 seconds, give a button to
continue." And on the send behaviour: "Yes, fill the box, click to send (unless continuous
conversation is turned on)."

Almost all of the voice machinery already existed in portal-mentor.js. It was rendered into
the Ask card, which is the hidden panel, so on the hub the mic and the continuous checkbox
could not be reached at all. Two things were genuinely missing: the cap, and its warning.

One real bug had to be fixed for the buttons to do anything. hostHidden() decides whether
the mic may start, and in card mode it asked whether #chatMsgs sat inside a hidden panel.
It did. So the mic would have been visible and inert, with no error.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MENTOR = ROOT / "static" / "portal-mentor.js"
SHELL = ROOT / "static" / "js" / "portal-shell.js"
PAGE = ROOT / "static" / "client-portal.html"


def test_the_composer_carries_every_voice_control_exactly_once():
    """portal-mentor.js resolves each of these by id. Two of any would bind the wrong one."""
    shell = SHELL.read_text()
    for el_id in ("chatMic", "chatSpeaker", "chatAutoGuide", "chatContinuous",
                  "chatContext", "chatContinuousWrap"):
        assert shell.count(f'id="{el_id}"') == 1, f"{el_id} must appear once in the composer"


def test_the_ask_card_no_longer_renders_the_voice_cluster():
    """It moved. If it rendered in both places the ids would collide."""
    page = PAGE.read_text()
    assert 'const _chatVoice = "";' in page
    assert 'id="chatMic"' not in page, "the card must not render a second microphone"
    assert 'id="chatContinuous"' not in page


def test_speech_fills_the_box_and_only_auto_sends_in_continuous():
    """Glen: fill the box, click to send, unless continuous conversation is on."""
    mentor = MENTOR.read_text()
    assert "h.input.value=Array.from(e.results)" in mentor, "the transcript must fill the box"
    assert "if(continuousOn)submit()" in mentor, "auto-send only under continuous"


def test_the_mentor_binds_to_the_visible_thread_not_the_hidden_one():
    """Otherwise hostHidden() is true on the hub and the mic silently refuses to start."""
    mentor = MENTOR.read_text()
    assert "window.chatThreadHost" in mentor, "the mentor must resolve the visible thread"
    assert 'window.chatThreadHost(false)' in mentor, (
        "resolving must not reveal the thread, or an empty one opens at page load"
    )


def test_the_page_exports_the_resolver_without_revealing():
    page = PAGE.read_text()
    assert "window.chatThreadHost = chatThreadHost;" in page
    assert "function chatThreadHost(reveal){" in page
    assert "if(reveal) inline.hidden = false;" in page
    assert "chatThreadHost(true)" in page, "only the bubble writer reveals"


HARNESS = r"""
const assert = require('assert');
const fs = require('fs');

// A clock we control, so five minutes takes no time to test.
let now = 0;
const timers = [];
function setTimeout_(fn, ms){ const t = {at: now + ms, fn, live: true}; timers.push(t); return t; }
function clearTimeout_(t){ if (t) t.live = false; }
function advance(ms){
  now += ms;
  timers.filter(t => t.live && t.at <= now).sort((a,b) => a.at - b.at).forEach(t => {
    if (!t.live) return;
    t.live = false; t.fn();
  });
}

const src = fs.readFileSync('static/portal-mentor.js', 'utf8');

// Drive the real module with a fake DOM. It is an IIFE over window/document, so it is
// executed here with stand-ins rather than re-typed.
function makeEl(id){
  return {
    id: id || '', className: '', textContent: '', type: '', checked: false, hidden: false,
    children: [], listeners: {}, scrollTop: 0, scrollHeight: 0, parentElement: null,
    classList: {toggle(){}, contains(){ return false; }},
    setAttribute(){}, getAttribute(){ return null; }, focus(){},
    appendChild(c){ c.parentElement = this; this.children.push(c); return c; },
    remove(){ if (this.parentElement) {
      const i = this.parentElement.children.indexOf(this);
      if (i >= 0) this.parentElement.children.splice(i, 1);
    } },
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    querySelectorAll(){ return []; }, querySelector(){ return null; }, closest(){ return null; }
  };
}

const els = {};
for (const id of ['chatMic','chatSpeaker','chatAutoGuide','chatContinuous','chatContext',
                  'chatContinuousWrap','chatInput','chatSend','shellChatThread','chatMsgs']) {
  els[id] = makeEl(id);
}
const thread = els.shellChatThread;

const spoken = [];
const document_ = {
  getElementById(id){ return els[id] || null; },
  createElement(tag){ return makeEl(''); },
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  addEventListener(){},
  hidden: false
};
const window_ = {
  chatThreadHost(){ return thread; },
  setTimeout: setTimeout_, clearTimeout: clearTimeout_,
  addEventListener(){},
  speechSynthesis: { cancel(){}, speak(u){ spoken.push(u.text); if (u.onend) u.onend(); } },
  localStorage: { getItem(){ return null; }, setItem(){} },
  SpeechRecognition: function(){
    return { start(){}, stop(){}, onstart: null, onend: null, onerror: null, onresult: null };
  }
};
window_.SpeechSynthesisUtterance = function(t){ this.text = t; this.rate = 1; };

// The module reaches for BARE globals in places (speechSynthesis, SpeechSynthesisUtterance),
// not window.*, and its speak() wraps them in a try/catch. Omit either and the ReferenceError
// is swallowed, speak() silently does nothing, and the test passes for the wrong reason.
const fn = new Function('window','document','SpeechSynthesisUtterance','speechSynthesis',
                        'console', src);
fn(window_, document_, window_.SpeechSynthesisUtterance, window_.speechSynthesis, console);

const clock = window_.PortalVoiceClock;
assert.ok(clock, 'the module must expose its continuous clock for testing');
assert.strictEqual(clock.maxMs, 5 * 60 * 1000, 'the cap must be five minutes');
assert.strictEqual(clock.warnMs, 5 * 60 * 1000 - 30 * 1000, 'the warning must be 30s before');

// Turn continuous on the way a client does, through the checkbox's own handler. Setting
// the module's private continuousOn is not possible from here, and faking it would test a
// path no client takes.
function toggleContinuous(on){
  els.chatContinuous.checked = on;
  (els.chatContinuous.listeners.change || []).forEach(f => f());
}

toggleContinuous(true);
assert.ok(spoken.some(t => /five minutes/i.test(t)),
  'switching it on must say the limit out loud');
const afterOn = thread.children.length;

advance(4 * 60 * 1000);
assert.strictEqual(thread.children.length, afterOn, 'nothing new must appear before 4:30');

advance(30 * 1000);   // now 4:30
const offer = thread.children[thread.children.length - 1];
assert.ok(offer, 'a continue offer must appear 30 seconds before the cap');
assert.ok(/30 seconds/.test(offer.textContent), 'the offer must say how long is left');
const button = offer.children.find(c => c.textContent === 'Keep going');
assert.ok(button, 'the offer must carry a Keep going button');

// --- Keep going buys another five minutes -----------------------------------
(button.listeners.click || []).forEach(f => f());
advance(60 * 1000);   // 5:30 overall, past the original cap
assert.ok(els.chatContinuous.checked,
  'REGRESSION: the cap fired anyway after Keep going was tapped');

// --- left alone, it stops at five minutes -----------------------------------
toggleContinuous(true);
advance(5 * 60 * 1000);
assert.strictEqual(els.chatContinuous.checked, false,
  'continuous must switch itself off at the cap');
assert.ok(spoken.some(t => /paused after five minutes/i.test(t)),
  'the client must be told why it stopped');

console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_cap_warns_thirty_seconds_early_with_a_continue_button():
    r = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True,
                       timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_turning_continuous_off_stops_its_clock():
    """A stale timer would pause a conversation the client had already restarted."""
    mentor = MENTOR.read_text()
    block = mentor[mentor.index("function disableContinuous()"):]
    block = block[:block.index("\n  }") + 4]
    assert "clearContinuousClock()" in block, (
        "disableContinuous must clear the cap timers, or they fire into a later session"
    )
