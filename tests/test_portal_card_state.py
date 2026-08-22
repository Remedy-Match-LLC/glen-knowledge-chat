import sqlite3
from pathlib import Path

from dashboard import portal_card_state as states


def test_card_open_is_durable_and_counts_revisits():
    cx = sqlite3.connect(":memory:")
    states.init_table(cx)

    first = states.mark_opened(cx, 42, "intake")
    second = states.mark_opened(cx, 42, "intake")

    assert first["visited"] is True
    assert first["open_count"] == 1
    assert second["open_count"] == 2
    saved = states.list_for_person(cx, 42)["intake"]
    assert saved["first_opened_at"] == first["first_opened_at"]
    assert saved["open_count"] == 2


def test_card_state_is_scoped_by_person():
    cx = sqlite3.connect(":memory:")
    states.init_table(cx)
    states.mark_opened(cx, 1, "health")

    assert "health" in states.list_for_person(cx, 1)
    assert states.list_for_person(cx, 2) == {}


def test_first_click_can_reveal_progress_without_a_reload():
    html = (Path(__file__).parents[1] / "static" / "client-portal.html").read_text()
    assert '.hub-tile:not(.is-visited) .tile-progress' in html
    assert 'const progressHtml = measurable' in html
    assert 'el.classList.add("is-visited")' in html
