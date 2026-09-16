"""The client must see their own message, and see that a reply is coming.

Glen, 2026-09-16, signed in on /portal/me: "I type, hit send, it disappears, no response."
Then: "I think the input should stay, the field should expand to show response, or at least
activity preparing a response."

The cause was not a dead listener. Under PORTAL_SHELL_ENABLED (on in prd) the composer is
rendered by PortalShell.renderComposer() into #portalShellMount, at the top of EVERY door.
The thread it wrote into was not there: #chatMsgs lives inside

    <section data-panel="ask" hidden data-door="learn">

so a client on the hub cleared their input and rendered both their question and the streamed
answer into a panel they cannot see. Nothing errored and nothing was lost. The conversation
was happening out of sight.

These tests execute the page's real chatThreadHost() and appendChatBubble() against a fake
DOM in which the Ask section is hidden, exactly as it is on the hub. A source grep cannot
tell which container a bubble landed in.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "client-portal.html"
SHELL = ROOT / "static" / "js" / "portal-shell.js"


def _fn_source(name: str) -> str:
    page = PAGE.read_text()
    at = page.find("\nfunction " + name + "(")
    if at == -1:
        at = page.find("\nasync function " + name + "(")
    assert at != -1, f"function {name}() not found in client-portal.html"
    end = page.find("\n}", at)
    assert end != -1, f"unterminated function {name}()"
    return page[at:end + 2]


HARNESS = r"""
const assert = require('assert');

function makeEl(tag){
  return {
    tagName: tag || 'DIV', id: '', className: '', textContent: '', hidden: false,
    style: {}, children: [], parentElement: null, scrollTop: 0, scrollHeight: 100,
    appendChild(c){ c.parentElement = this; this.children.push(c); return c; }
  };
}

// The Ask section is hidden, as it is on every door except Ask itself.
const askSection = makeEl('SECTION'); askSection.hidden = true;
const chatMsgs = makeEl('DIV'); chatMsgs.id = 'chatMsgs';
askSection.appendChild(chatMsgs);

const shellThread = makeEl('DIV'); shellThread.id = 'shellChatThread'; shellThread.hidden = true;

const els = { chatMsgs: chatMsgs, shellChatThread: shellThread };
const document = {
  getElementById(id){ return els[id] || null; },
  createElement(tag){ return makeEl(tag); }
};

__FNS__

// --- on the hub, the Ask panel is hidden ------------------------------------
const host = chatThreadHost();
assert.strictEqual(host, shellThread,
  'with the Ask panel hidden the thread must be the composer\'s own, not the hidden #chatMsgs');
assert.strictEqual(shellThread.hidden, false, 'the composer thread must be revealed');

const bubble = appendChatBubble('user', 'my knees ache in the morning');
assert.ok(bubble, 'appendChatBubble returned null, the message vanished');
assert.strictEqual(bubble.parentElement, shellThread, 'the message landed in the hidden panel');
assert.strictEqual(bubble.textContent, 'my knees ache in the morning',
  'the client must see the words they typed');
assert.strictEqual(chatMsgs.children.length, 0, 'nothing should go to the hidden thread');

// --- on the Ask door itself, nothing changes --------------------------------
askSection.hidden = false;
shellThread.hidden = true;
assert.strictEqual(chatThreadHost(), chatMsgs,
  'with the Ask door open the canonical #chatMsgs must still be the thread');
assert.strictEqual(shellThread.hidden, true,
  'the composer thread must stay closed when the real one is visible');

// --- a display:none ancestor counts as hidden too ---------------------------
askSection.hidden = false;
askSection.style.display = 'none';
shellThread.hidden = true;
assert.strictEqual(chatThreadHost(), shellThread, 'display:none ancestor must count as hidden');

console.log('OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_client_sees_their_message_when_the_ask_panel_is_hidden():
    fns = "\n".join(_fn_source(n) for n in
                    ("chatThreadHost", "_isChatHostHidden", "appendChatBubble"))
    script = HARNESS.replace("__FNS__", fns)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_the_composer_carries_its_own_thread_container():
    """renderComposer() must ship the container, or there is nowhere visible to render."""
    shell = SHELL.read_text()
    assert 'id="shellChatThread"' in shell, "the composer has no thread container"
    assert 'chat-msgs' in shell, "the inline thread must reuse the .chat-msgs styling"
    # One id, one thread. Two elements sharing #chatMsgs is why the card's composer was
    # removed in the first place, and sendChatMessage() resolves its host by id.
    assert 'id="chatMsgs"' not in shell, "renderComposer must not duplicate #chatMsgs"


def test_a_waiting_state_is_shown_before_the_first_token():
    """An empty assistant bubble reads as nothing happening."""
    page = PAGE.read_text()
    assert 'chat-bubble assistant pending' in page, "no waiting state between Send and the reply"
    assert '.chat-bubble.pending{' in page, "the waiting state has no styling"
