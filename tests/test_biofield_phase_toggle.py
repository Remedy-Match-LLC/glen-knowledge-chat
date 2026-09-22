from dashboard.biofield_report_html import render_author_html


def _html():
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": [], "schedule": []}
    return render_author_html(rep, [], "")


def _button_text(h, step):
    """The visible text of the live-session button that calls setPhase(step). Read from the
    button itself, never from the whole page: the terrain narrative on this same page may
    legitimately say "Phase 2, Rejuvenate", which is the terrain map's meaning and is
    correct there."""
    import re
    m = re.search(r"onclick='setPhase\(%d\)'>([^<]*)</button>" % step, h)
    assert m, f"no live-session button for step {step}"
    return m.group(1)


def test_phase_toggle_present():
    h = _html()
    assert "setPhase(" in h            # toggle handler
    assert "function captureStresses" in h


def test_the_live_session_steps_are_steps_not_phases():
    """Glen, 2026-09-21: "Phase 1" and "Phase 2" are confusing next to the 5 Phases of
    Healing, so the buttons are Step 1 and Step 2. And "Rejuvenate" is Phase 2 of the
    TERRAIN map, nothing to do with step 2 here. Step 2 runs interpret(), which the action
    button already labels "Interpret -> fill fields", so that is its name."""
    h = _html()
    assert _button_text(h, 1) == "Step 1 &middot; Capture stresses"
    assert _button_text(h, 2) == "Step 2 &middot; Interpret"


def test_no_live_session_button_borrows_a_terrain_phase_name():
    h = _html()
    for step in (1, 2):
        text = _button_text(h, step)
        assert "Phase" not in text, text
        for terrain in ("Energize", "Rejuvenate", "Regenerate", "Cleanse", "Balance"):
            assert terrain not in text, f"step {step} button says {text!r}"


def test_capture_posts_to_route_and_reloads_panel():
    h = _html()
    assert "/author/a7/capture-stresses" in h
    assert "loadStress()" in h         # panel refresh after capture
