"""The microphone must work before the client has typed anything.

Glen, 2026-09-16, on the portal chat shipped in #1702: "In the client portal, I can't click
on microphone in the chat until I enter something manually."

Cause, and it was mine. hostHidden() gates startListening() and speak(), and it walked up
from the thread element looking for a `hidden` ancestor. The composer's own thread ships
`hidden` and stays that way until the first message, because an empty transcript should not
take up space. So on page load the mentor concluded the client could not see the
conversation and the microphone silently refused to start. Typing and sending revealed the
thread, which is exactly why it began working afterwards.

The flag means "no messages yet", not "off screen". The composer around it is on every door.

This is the shape of bug that does not announce itself: no error, no console message, a
button that simply does nothing. Only a person tapping it finds out.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MENTOR = ROOT / "static" / "portal-mentor.js"


HARNESS = r"""
const assert = require('assert');
const fs = require('fs');

function makeEl(id){
  return {
    id: id || '', className: '', textContent: '', checked: false, hidden: false,
    style: {}, children: [], listeners: {}, parentElement: null,
    scrollTop: 0, scrollHeight: 0,
    classList: {toggle(){}, contains(){ return false; }},
    setAttribute(){}, getAttribute(){ return null; }, focus(){},
    appendChild(c){ c.parentElement = this; this.children.push(c); return c; },
    remove(){},
    addEventListener(t, f){ (this.listeners[t] = this.listeners[t] || []).push(f); },
    querySelectorAll(){ return []; }, querySelector(){ return null; }, closest(){ return null; }
  };
}

const els = {};
for (const id of ['chatMic','chatSpeaker','chatAutoGuide','chatContinuous','chatContext',
                  'chatContinuousWrap','chatInput','chatSend','shellChatThread','chatMsgs']) {
  els[id] = makeEl(id);
}
// The state a client lands on: the composer is visible, its thread is empty and so
// carries `hidden`, and the Ask panel is a different door entirely.
const composer = makeEl('shellComposer');
composer.appendChild(els.shellChatThread);
els.shellChatThread.hidden = true;

let started = 0;
const recognition = {
  start(){ started++; }, stop(){},
  onstart: null, onend: null, onerror: null, onresult: null
};

const document_ = {
  getElementById(id){ return els[id] || null; },
  createElement(){ return makeEl(''); },
  querySelectorAll(){ return []; }, querySelector(){ return null; },
  addEventListener(){}, hidden: false
};
const window_ = {
  chatThreadHost(){ return els.shellChatThread; },
  setTimeout(fn){ fn(); return 0; }, clearTimeout(){},
  addEventListener(){},
  localStorage: { getItem(){ return null; }, setItem(){} },
  SpeechRecognition: function(){ return recognition; }
};
const synth = { cancel(){}, speak(u){ if (u.onend) u.onend(); } };
function Utt(t){ this.text = t; }

const src = fs.readFileSync('static/portal-mentor.js', 'utf8');
new Function('window','document','SpeechSynthesisUtterance','speechSynthesis','console', src)(
  window_, document_, Utt, synth, console);

// Tap the microphone with the transcript still empty, exactly as a client would.
(els.chatMic.listeners.click || []).forEach(f => f());
assert.ok(started > 0,
  'REGRESSION: the microphone did nothing with an empty transcript, which is the bug ' +
  'Glen reported. hostHidden() is reading the thread\'s "no messages yet" flag as ' +
  '"the client cannot see this".');

// And it must still refuse when the composer itself is genuinely off screen.
started = 0;
composer.hidden = true;
(els.chatMic.listeners.click || []).forEach(f => f());
assert.strictEqual(started, 0,
  'the mic must NOT start when the composer around it is actually hidden');

console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_mic_starts_with_an_empty_transcript():
    r = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True,
                       timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_the_page_guide_uses_glens_voice_not_the_browsers():
    """Glen, 2026-09-16: "The page guide voice is ai not mine."

    The guide called speak(), which is ALWAYS the browser voice. onReply carries the
    policy for a spoken assistant line: Dr Glen's recorded voice normally, the browser
    one only under continuous conversation, where hands-free turn taking needs a
    reliable end-of-speech signal to hand the microphone back.
    """
    src = MENTOR.read_text()
    guide = src[src.index("window.mentorPageChanged"):]
    assert "onReply(bubble,text)" in guide, "the page guide must route through onReply"
    assert "speak(text)}" not in guide, "the guide must not call the browser voice directly"


def test_the_guide_interrupts_rather_than_talking_over_a_reply():
    """Glen, minutes earlier: "There may have been two voices (mine and ai) at the same
    time just now."

    speechSynthesis and the TTS <audio> element are independent channels, so a guide
    speaking through one while a reply played through the other ran both at once.
    attachAndSpeak calls stopActive() first, so routing the guide through onReply makes
    it interrupt instead.
    """
    tts = (ROOT / "static" / "tts-output.js").read_text()
    body = tts[tts.index("function attachAndSpeak"):]
    body = body[:body.index("\n  }") + 4]
    assert "stopActive()" in body, (
        "attachAndSpeak must stop whatever is playing, or the guide talks over a reply"
    )


def test_the_reason_is_written_down_where_the_check_lives():
    """The next reader will otherwise 'simplify' the walk and put the bug back."""
    src = MENTOR.read_text()
    assert 'el.id==="shellChatThread"' in src
    assert "no messages yet" in src, "the flag's meaning must be stated at the check"
