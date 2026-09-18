"""Three privacy fixes Glen approved on 2026-09-18.

Reported by the primary from a data survey; each verified independently here before the
change, and two were worse than reported.

1. CONSENT IN FRONT OF THE MICROPHONE, static/begin-doorway.html.
   getUserMedia ran while the page's only consent checkbox sat inside `#optin`, which is
   `display:none` at that moment. The client could not see it, let alone agree, before
   their voice was captured and sent to OpenAI. The check at the bottom of the page is in
   the opt-in SUBMIT handler, long after the recording.

   The page also said the recording "is not stored beyond this session". True of the
   audio, which is deleted after transcription. NOT true of the transcript, which persists
   in journal_entries with no purge job. And no service was named.

2. STOP WRITING voice_signals, app.py.
   A copy of whatever a client dictated into the match chat box was stored with their
   email and session id. Grepped independently before removal: the whole repository held
   exactly two references, the CREATE and the INSERT. Nothing read it. The route had no
   auth and took the email from the request body, so any caller could insert under any
   address, and the client was never told. The fix is to stop writing, not to secure it.

3. REMOVE retain_audio, journal_blueprint.py.
   A form flag left the temp audio file on disk, and nothing referenced it again. No front
   end set it, which is not the same as unreachable: the route is public. Glen has
   committed publicly to deleting scan recordings.

THESE TESTS STRIP COMMENTS BEFORE LOOKING. Six checks in this session matched prose
instead of code, including one where a verification found the word "getUserMedia" inside
the comment explaining the fix and reported the gate as being in the wrong place.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOORWAY = ROOT / "static" / "begin-doorway.html"
APP = ROOT / "app.py"
JOURNAL = ROOT / "journal_blueprint.py"


def _no_comments(path, js=True):
    src = path.read_text()
    pat = r"^\s*//.*$" if js else r"^\s*#.*$"
    return "\n".join(re.sub(pat, "", l) for l in src.splitlines())


def _line(text, needle):
    for i, l in enumerate(text.splitlines(), 1):
        if needle in l:
            return i
    return None


# --- 1. consent before the microphone ---------------------------------------------

def test_the_consent_gate_runs_before_getusermedia():
    code = _no_comments(DOORWAY)
    gate = _line(code, "voiceChk').checked")
    mic = _line(code, "mediaDevices.getUserMedia")
    assert gate and mic, (gate, mic)
    assert gate < mic, "the microphone opens before consent is checked"


def test_the_record_button_starts_disabled():
    assert 'id="recBtn" disabled' in DOORWAY.read_text()


def test_the_consent_is_its_own_visible_block_not_buried_in_the_optin():
    """The original checkbox lived inside #optin, which is display:none while recording."""
    src = DOORWAY.read_text()
    assert 'id="voiceConsent"' in src
    i, j = src.index('id="voiceConsent"'), src.index('id="optin"')
    assert i < j, "consent must come before the opt-in section, not inside it"


def test_the_consent_is_revealed_with_the_controls():
    code = _no_comments(DOORWAY)
    assert "voiceConsent').style.display" in code, (
        "the block is never shown, so the button can never be enabled"
    )


def test_openai_is_named_and_linked():
    """'sent for processing' names nobody. The client is told who receives their voice."""
    src = DOORWAY.read_text()
    assert "OpenAI" in src
    assert "openai.com/policies/privacy-policy" in src


def test_the_page_no_longer_claims_the_recording_is_not_stored():
    """The audio is deleted; the TRANSCRIPT is kept. Saying otherwise is the false half."""
    src = DOORWAY.read_text()
    assert "not stored beyond this session" not in src
    assert "deleted straight after" in src
    assert "text is kept" in src or "<strong>text</strong> of what I say is kept" in src


# --- 2. voice_signals ---------------------------------------------------------------

def test_nothing_writes_voice_signals_any_more():
    code = _no_comments(APP, js=False)
    assert "INSERT INTO voice_signals" not in code
    assert "CREATE TABLE IF NOT EXISTS voice_signals" not in code


def test_the_route_still_answers_so_a_cached_page_does_not_break():
    code = _no_comments(APP, js=False)
    assert '@app.route("/begin/match/voice-signal"' in code
    body = code[code.index("def begin_match_voice_signal"):]
    body = body[:body.index("\n@app.route")]
    assert '"stored": False' in body, "the reply should say plainly that nothing was kept"


def test_the_route_no_longer_touches_the_database():
    code = _no_comments(APP, js=False)
    body = code[code.index("def begin_match_voice_signal"):]
    body = body[:body.index("\n@app.route")]
    for danger in ("db.connect", "cx.execute", "commit"):
        assert danger not in body, f"the route still does {danger}"


# --- 3. retain_audio ----------------------------------------------------------------

def test_the_retain_audio_flag_is_gone():
    code = _no_comments(JOURNAL, js=False)
    assert "retain_audio" not in code


def test_the_audio_is_always_deleted():
    code = _no_comments(JOURNAL, js=False)
    body = code[code.index("def analyze"):]
    assert "os.unlink(audio_path)" in body
    assert "if not retain" not in body, "a conditional delete is what was removed"
