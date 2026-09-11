"""The lesson page prints its own <h1> from the lesson title, and Practice Better
bodies sometimes open with that same title again. Strip that leading duplicate --
and ONLY an exact duplicate, because 15 of the 64 live lessons open with a heading
that is real content ("Week 1: Body" under a lesson titled "Minding Body 1")."""

from dashboard.courses_sanitize import strip_duplicate_lead_heading as strip


def test_exact_h1_duplicate_is_removed():
    out = strip("<h1>Minding Body Discussion 1A</h1><p>body</p>",
                "Minding Body Discussion 1A")
    assert out == "<p>body</p>"


def test_exact_paragraph_duplicate_is_removed():
    out = strip("<p>Family History Discussion 1B</p><p>real</p>",
                "Family History Discussion 1B")
    assert out == "<p>real</p>"


def test_trademark_and_case_and_entities_still_count_as_exact():
    out = strip("<p>Accelerated Self Healing&trade; MasterClass 1</p><p>x</p>",
                "Accelerated Self Healing™ MasterClass 1")
    assert out == "<p>x</p>"
    out = strip("<h1>Causality &amp; the Arrow of Time TV Series</h1><p>x</p>",
                "Causality & the Arrow of Time TV Series")
    assert out == "<p>x</p>"


def test_different_heading_is_kept():
    """The 15-lesson case. Removing this would delete real content."""
    html = "<h1>Week 1: Body</h1><p>body</p>"
    assert strip(html, "Minding Body 1") == html


def test_partial_overlap_is_kept():
    """'Architecture of Coherence - NotebookLM video' carries information the
    title does not. Near is not exact."""
    html = "<h1>Architecture of Coherence - NotebookLM video</h1><p>b</p>"
    assert strip(html, "Architecture of Coherence") == html


def test_only_the_first_element_is_considered():
    """A later heading that happens to match the title is left alone."""
    html = "<p>intro</p><h1>Minding Body 1</h1><p>b</p>"
    assert strip(html, "Minding Body 1") == html


def test_a_heading_wrapping_a_video_is_kept():
    """Text may match while the element also carries an embed. Never drop media."""
    html = ('<p>Spirit Matters 1<iframe src="https://www.youtube.com/embed/a">'
            '</iframe></p><p>b</p>')
    assert strip(html, "Spirit Matters 1") == html


def test_empty_and_missing_inputs_are_safe():
    assert strip("", "Anything") == ""
    assert strip("<p>x</p>", "") == "<p>x</p>"
    assert strip("<p></p><p>x</p>", "") == "<p></p><p>x</p>"
