"""An expired clip-only appointment message shows a placeholder, to client and staff.

Glen, 2026-10-27, sales tab: show exactly "Voice clip removed after 90 days." where a
message held only a clip that the 90-day job deleted.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboard import appointment_proposals as ap

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TEXT = "Voice clip removed after 90 days."
ROOT = Path(__file__).resolve().parent.parent


def _cx(tmp_path):
    import sqlite3
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    ap.init_tables(cx)
    return cx


def _pid(cx):
    return ap.create(cx, email="client@x.com", session_type="evox",
                     start="2026-11-01T09:00:00", proposed_by="client")


def _clip(cx, assets, pid, text):
    name = f"appt-{pid}-{'a' * 24}.webm"
    (assets / name).write_bytes(b"audio")
    cx.execute("INSERT INTO appointment_proposal_messages "
               "(proposal_id,sender,text,audio_filename,created_at) VALUES (?,?,?,?,?)",
               (pid, "client", text, name, "2026-07-01T00:00:00+00:00"))
    cx.commit()


def test_an_expired_clip_only_message_is_marked_removed(tmp_path):
    cx = _cx(tmp_path)
    (tmp_path / "a").mkdir()
    pid = _pid(cx)
    _clip(cx, tmp_path / "a", pid, "")
    assert [m["clip_removed"] for m in ap.messages(cx, pid)] == [False]
    assert ap.expire_clips(cx, tmp_path / "a", now=NOW) == 1
    assert [m["clip_removed"] for m in ap.messages(cx, pid)] == [True]
    assert ap.list_for_client(cx, "client@x.com")[0]["messages"][0]["clip_removed"] is True


def test_an_expired_clip_with_text_keeps_its_text_and_no_placeholder(tmp_path):
    cx = _cx(tmp_path)
    (tmp_path / "a").mkdir()
    pid = _pid(cx)
    _clip(cx, tmp_path / "a", pid, "see clip")
    ap.expire_clips(cx, tmp_path / "a", now=NOW)
    (m,) = ap.messages(cx, pid)
    assert m["text"] == "see clip" and m["clip_removed"] is False


def test_a_text_message_is_not_marked_removed(tmp_path):
    cx = _cx(tmp_path)
    pid = _pid(cx)
    ap.add_message(cx, pid, "glen", text="hello")
    assert ap.messages(cx, pid)[0]["clip_removed"] is False


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_a_blank_message_is_refused(tmp_path, text):
    # A blank row must only ever mean a deleted clip, so a blank send never lands.
    cx = _cx(tmp_path)
    pid = _pid(cx)
    with pytest.raises(ValueError):
        ap.add_message(cx, pid, "client", text=text)
    assert ap.messages(cx, pid) == []


@pytest.mark.parametrize("page", ["client-portal.html", "appointment-proposals.html"])
def test_both_pages_show_the_exact_words(page):
    html = (ROOT / "static" / page).read_text()
    assert f"${{m.clip_removed?`<div class=\"" in html
    i = html.index("${m.clip_removed?")
    assert TEXT in html[i:i + 120]
