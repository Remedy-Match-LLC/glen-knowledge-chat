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
  // Short timers run inline so the mic's restart backoff (250ms, capped at 4000ms) is
  // observable in one tick. LONG ones must not, or the five minute continuous cap fires
  // the instant it is armed and switches continuous straight back off. That is exactly
  // what made this test report "continuous never opened the microphone" when the code
  // was correct.
  setTimeout(fn, ms){ if (!ms || ms <= 5000) fn(); return 0; }, clearTimeout(){},
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


def test_the_mentors_own_messages_open_the_thread():
    """Everything the mentor says on its own used to land invisibly.

    The composer's thread ships `hidden` until the first message, and only the PAGE's
    appendChatBubble revealed it. The mentor's append() wrote into it without opening it,
    so the greeting, the "continuous conversation is on" confirmation, the cap warning
    and its Keep going button were all written where nobody could see them.

    Glen, 2026-09-16: "Continuous two-way isn't working. It's checked, but seems to not
    hear me." He had been told it was on, in a bubble that was invisible.

    Asserted on the source rather than executed: the surrounding behaviour runs through
    asynchronous speech callbacks that a fake models badly, and a fake that models them
    badly is how this file already reported a passing feature as broken once tonight.
    """
    src = MENTOR.read_text()
    body = src[src.index("function append(role,text)"):]
    body = body[:body.index("return b}") + 9]
    assert "h.msgs.hidden=false" in body, (
        "the mentor's append must OPEN the composer thread, or everything it says on its "
        "own is written where the client cannot see it"
    )


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


def test_the_five_element_recorder_takes_the_microphone_back_first():
    """Glen, 2026-09-16: "The 5-element voice analysis has no way to stop.
    (because I am in 5 min. 2-way conversation maybe?)" He was right about the cause.

    SpeechRecognition and MediaRecorder compete for the microphone. With continuous
    conversation running, getUserMedia fails, so `start.hidden=true; stop.hidden=false`
    never runs and the "Finish & analyze" button never appears. There IS a stop button;
    it is only revealed once recording has actually begun.
    """
    page = (ROOT / "static" / "client-portal.html").read_text()
    handler = page[page.index('start.addEventListener("click"'):]
    handler = handler[:handler.index("getUserMedia")]
    assert "PortalVoice.release()" in handler, (
        "the recorder must release the mentor's microphone BEFORE asking for it"
    )


def test_releasing_also_stops_continuous_from_taking_it_straight_back():
    """Stopping recognition alone is not enough: scheduleListening restarts it."""
    src = MENTOR.read_text()
    body = src[src.index("function releaseMicrophone()"):]
    body = body[:body.index("\n  }") + 4]
    assert "disableContinuous()" in body, (
        "release must switch continuous OFF, or the mic is taken back mid-recording"
    )


def test_a_busy_microphone_is_not_reported_as_a_permission_problem():
    """"Microphone access is needed" sends a client to their browser settings.

    The real cause is another part of the same page holding the device.
    """
    page = (ROOT / "static" / "client-portal.html").read_text()
    assert "The microphone is busy" in page
    assert "Turn off continuous conversation" in page
