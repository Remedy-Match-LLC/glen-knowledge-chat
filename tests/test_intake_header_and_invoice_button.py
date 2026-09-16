"""Glen, 2026-09-16, on the local Biofield Intake page:

  "I would like a button on Biofield Intake to add the remedies to the Invoice.
   Also, can we show the name of the client in the header (next to Biofield Intake)
   so it is always visible as I scroll."

The raise itself already exists and already updates an open order in place. What was
missing is a way to reach it: once an invoice exists, `detectInvoice` relabelled the
only button "Edit invoice" and pointed it at the console, so the remedies could no
longer be pushed from this page.
"""
from dashboard.biofield_fee import build_fee_state
from dashboard.biofield_report_html import _bar, render_author_html


def _html(name="Michael Hill", email="m@x.com"):
    rep = {"test_id": "a7", "client": {"name": name, "email": email}, "date": "",
           "layers": [], "schedule": []}
    # The invoice buttons live in the Fee card, which only renders with a fee_state.
    # available=False takes an early return that renders no invoice controls.
    fee = build_fee_state(email, lambda _e: {"available": True}) if email else None
    return render_author_html(rep, [], "", fee_state=fee)


def test_bar_shows_the_client_name_beside_the_page_name():
    bar = _bar("Michael Hill")
    assert "Biofield Intake" in bar
    assert "Michael Hill" in bar
    assert bar.index("Biofield Intake") < bar.index("Michael Hill")


def test_bar_escapes_the_client_name():
    assert "<script>" not in _bar('<script>x</script>')


def test_bar_without_a_client_is_unchanged():
    assert _bar() == _bar("")
    assert "opclient" not in _bar()


def test_author_page_carries_the_client_name_in_the_bar():
    h = _html()
    bar = h[h.index("<nav class=opbar"):h.index("</nav>")]
    assert "Michael Hill" in bar


def test_an_unnamed_client_leaves_the_bar_alone():
    h = _html(name="")
    bar = h[h.index("<nav class=opbar"):h.index("</nav>")]
    assert "opclient" not in bar


def test_add_remedies_button_exists_and_raises_rather_than_linking_out():
    h = _html()
    assert "Add remedies to invoice" in h
    assert "id=addremedybtn" in h
    # It must call the raise, not the console deep-link that Edit invoice uses.
    assert "onclick=createInvoice()" in h


def test_the_add_remedies_button_survives_edit_mode():
    """setInvoiceEditMode relabels the primary button when an invoice already
    exists. It must not touch the add-remedies button, or the whole point is lost."""
    h = _html()
    fn = h[h.index("function setInvoiceEditMode"):]
    fn = fn[:fn.index("function detectInvoice")]
    assert "addremedybtn" not in fn
