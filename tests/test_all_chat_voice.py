"""Contracts for ElevenLabs voice output across customer chat interfaces."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


VOICE_CHAT_FILES = (
    "index.html",
    "embed.html",
    "begin-match.html",
    "begin-buy.html",
    "begin-intake.html",
    "client-portal.html",
    "practitioner-dropship.html",
    "practitioner-client.html",
    "invoice.html",
    "member-scan-analysis.html",
)


def test_every_customer_chat_surface_loads_elevenlabs_voice_output():
    for name in VOICE_CHAT_FILES:
        source = (STATIC / name).read_text()
        assert "/static/tts-output.js" in source, name


def test_every_customer_chat_surface_connects_finalized_replies_to_voice():
    for name in VOICE_CHAT_FILES:
        source = (STATIC / name).read_text()
        assert "window.TTS" in source, name


def test_shared_voice_uses_server_side_elevenlabs_and_safe_live_reply_activation():
    source = (STATIC / "tts-output.js").read_text()
    assert "fetch('/chat/tts'" in source
    assert "navigator.userActivation.hasBeenActive" in source
    assert "function attachReply" in source
    assert "attachAndSpeak(container, text) : attach(container, text)" in source


def test_voice_credentials_remain_server_side():
    source = (ROOT / "app.py").read_text()
    assert 'os.environ.get("ELEVENLABS_API_KEY"' in source
    assert 'os.environ.get("ELEVENLABS_VOICE_ID"' in source
    assert '@app.route("/chat/tts"' in source


def test_internal_chat_interfaces_use_the_same_voice_service():
    for name in ("console.html", "shaira-workspace.html"):
        source = (STATIC / name).read_text()
        assert "/static/tts-output.js" in source
        assert "window.TTS.attachReply" in source
    widget = (STATIC / "justus-widget.js").read_text()
    assert "script.src = '/static/tts-output.js'" in widget
    assert "voiceReply(bubble, acc)" in widget
