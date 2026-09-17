"""One cached sentence must be correctable without regenerating the paragraph.

Glen, 2026-09-17: "fix the vitamin A dose and the copy."

THE GAP THIS CLOSES. A cached narrative section is served to the public as soon as it
exists: begin_product_page_data reads it and does NOT check `state`, so a draft is live.
The console had a GET for sales pages and no write at all, so the only way to change a
live sentence was to regenerate the whole section. That rewrites copy nobody asked to
touch, and re-derives it from the sources that produced the error.

THE CASE. The Fulvic Acid page publicly read "Combined with 100 mg of vitamin A to
support vision and healthy skin". 100 mg is the WEIGHT OF THE MATERIAL, which is 13.75%
retinyl palmitate, so the activity is 25,000 IU. The source row states the equivalence
outright: "7,500-25,000 IU = 30-100 mg". The dose was right and the sentence was wrong.

WHY THESE TESTS READ SOURCE RATHER THAN CALLING THE APP. Importing and reloading `app` in
a test process resets module state that app.py registers at import. I did exactly that
earlier on 2026-09-17 and broke twenty unrelated CI tests on payments and pricing. The
behaviour that genuinely needs a running server, that an unauthenticated caller is
refused, is verified against PRODUCTION after deploy and recorded in the PR, not faked
here.
"""
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SRC = APP.read_text()
FN = "def api_console_sales_page_edit_section"


@pytest.fixture(scope="module")
def body():
    i = SRC.index(FN)
    nxt = SRC.find("\n@app.route", i)
    return SRC[i:nxt if nxt != -1 else len(SRC)]


def test_the_route_is_registered_for_post_only():
    """A GET falling through to this handler would be a read carrying a write's authority."""
    i = SRC.index(FN)
    dec = SRC[SRC.rindex("@app.route", 0, i):i]
    assert '"/api/console/sales-page/<slug>/section"' in dec
    assert re.search(r'methods=\["POST"\]', dec), dec


def test_it_is_owner_gated_by_the_same_check_as_the_read(body):
    """This route writes copy the public reads. It must not be gated more weakly than the
    GET beside it, and must return before touching anything."""
    assert "_sales_console_ok()" in body
    gate = body.index("_sales_console_ok()")
    for danger in ("upsert_section", "db.connect"):
        assert body.index(danger) > gate, f"{danger} runs before the auth check"


def test_the_section_name_is_checked_against_the_real_list(body):
    """A typo'd section writes a key nothing reads, and reports success."""
    from dashboard import sales_copy as sc
    assert "_sc.NARRATIVE_SECTIONS" in body, (
        "the allow-list must come from sales_copy, never a second hand-kept copy"
    )
    assert sc.NARRATIVE_SECTIONS == ("intro", "description", "research")


def test_an_unknown_product_is_refused(body):
    """Otherwise a typo'd slug creates a sales page for a product that does not exist."""
    assert "_get_product(slug)" in body
    assert "404" in body


def test_an_absent_text_is_refused_but_an_empty_one_clears(body):
    """A client that forgets the field must not silently wipe live copy. Sending "" on
    purpose must still clear, because a wrong claim should come down before a new one
    goes up."""
    assert 'text = body.get("text")' in body
    assert "if text is None:" in body, "a missing key must be an error, not a clear"


def test_it_reports_whether_anything_actually_changed(body):
    """A no-op returning ok:true reads as a successful edit. The response must be computed
    by RE-READING the row, not by assuming the write took."""
    assert '"changed":' in body
    before = body.index("before = _sp.get_section")
    after = body.index("after = _sp.get_section")
    assert before < body.index("upsert_section") < after, (
        "changed must compare a read from before the write with one from after it"
    )


def test_the_public_page_still_serves_a_cached_section_regardless_of_state():
    """The premise of the whole route. If a state check ever gates the swap, a draft edit
    stops being live and this route's urgency changes."""
    i = SRC.index('_draft = _sp.get_section(_cx, slug, _s["id"])')
    seg = SRC[i:i + 400]
    assert '_s["ai"] = "cached"' in seg
    assert "approved" not in seg.split("_ai_state")[0], (
        "a state check appeared before the body swap; re-read this test's premise"
    )
