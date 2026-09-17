"""Send on /begin/match must explain itself where the client is looking.

Glen, 2026-09-16: "On https://myhealingoasis.com/begin/match clicking Send on the chat
takes me to top of page, and doesn't process the message."

It was doing the right thing for the wrong-looking reason. sendQuery() refuses until "Me"
or "Someone else" has been chosen, writes a warning into #whom-note, and scrolls the
chooser into view. The chooser sits about fifty lines above the input, so on a phone the
page jumps, the message appears to vanish, and the explanation is off screen behind you.

The refusal is correct and stays. What changed is that it now also says so AT THE INPUT,
and says the message is still there, because "nothing happened and the page moved" is
indistinguishable from a broken button.
"""
import pathlib

PAGE = pathlib.Path(__file__).resolve().parents[1] / "static" / "begin-match.html"


def test_the_refusal_is_explained_at_the_input():
    page = PAGE.read_text()
    assert 'id="input-note"' in page, "there must be a note beside the composer"
    assert "First choose who this is for, just above." in page
    assert "Your message is still here." in page, (
        "a client whose message appears to vanish needs telling it did not"
    )


def test_the_note_is_announced_to_a_screen_reader():
    page = PAGE.read_text()
    block = page[page.index('id="input-note"') - 120:page.index('id="input-note"') + 120]
    assert 'role="status"' in block and 'aria-live="polite"' in block


def test_the_note_clears_once_a_choice_is_made():
    """Left up, it reads as a standing warning against a message that is now fine."""
    page = PAGE.read_text()
    handler = page[page.index("r.addEventListener('change'"):]
    handler = handler[:handler.index("recipientFields")]
    assert "clearInputNote()" in handler


def test_the_note_clears_when_a_send_is_retried():
    page = PAGE.read_text()
    body = page[page.index("async function sendQuery()"):]
    body = body[:body.index("const query")]
    assert "clearInputNote()" in body


def test_the_send_button_cannot_submit_anything():
    """A bare <button> defaults to type=submit. There is no form here today, and a
    later one wrapping this markup would turn Send into a page reload."""
    page = PAGE.read_text()
    assert '<button type="button" id="send-btn"' in page


def test_the_chooser_is_still_required():
    """The refusal itself is correct and must not be softened into a default."""
    page = PAGE.read_text()
    body = page[page.index("async function sendQuery()"):]
    assert "if (!forWhom) {" in body
    assert "First, choose who this is for" in body
