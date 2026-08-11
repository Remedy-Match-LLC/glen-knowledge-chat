from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_failed_chat_restores_message_for_retry():
    assert "input.value = query" in HTML
    assert "Your message is ready to retry" in HTML
    assert "String(evt.error)" not in HTML


def test_interrupted_stream_does_not_enter_chat_history():
    assert "if(!completed && !failed)" in HTML
    assert "completed = true" in HTML
