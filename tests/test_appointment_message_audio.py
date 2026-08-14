import io


def test_empty_browser_file_part_is_ignored_for_text_only_message(monkeypatch):
    import app

    called = []
    monkeypatch.setattr(app, "_save_appointment_audio",
                        lambda file_obj, proposal_id: called.append(proposal_id))
    with app.app.test_request_context(
            "/messages", method="POST",
            data={"text": "Does this time work?", "audio": (io.BytesIO(b""), "")}):
        assert app._appointment_audio_from_request(7) == ""
    assert called == []


def test_selected_audio_still_uses_normal_validation_path(monkeypatch):
    import app

    captured = []

    def save(file_obj, proposal_id):
        captured.append((file_obj.filename, file_obj.mimetype, proposal_id))
        return "saved.webm"

    monkeypatch.setattr(app, "_save_appointment_audio", save)
    with app.app.test_request_context(
            "/messages", method="POST",
            data={"text": "Voice note",
                  "audio": (io.BytesIO(b"audio"), "note.webm", "audio/webm")}):
        assert app._appointment_audio_from_request(9) == "saved.webm"
    assert captured == [("note.webm", "audio/webm", 9)]
