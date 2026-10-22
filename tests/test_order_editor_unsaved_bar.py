"""Edit Invoice shows a save bar while changes are unsaved, and an edit keeps each
line's billed_for (Glen, 2026-10-22: "I forgot the save button the first time")."""
from pathlib import Path

from dashboard import client_invoice_lines as cil

SRC = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()


def test_unsaved_bar_exists_and_saves():
    assert '<div id="unsaved-bar" role="status">' in SRC
    assert 'This invoice has unsaved changes.</span><button class="primary" onclick="submitOrder()">' in SRC


def test_bar_compares_against_what_was_loaded_or_saved():
    assert "function editSig(){ return JSON.stringify(editBody()); }" in SRC
    assert "editSig()!==SAVED_SIG" in SRC
    assert "await loadOrderForEdit(EDIT_OID); markSaved();" in SRC
    # The baseline is the body that was sent, not the form when the reply lands.
    assert "const sentSig = JSON.stringify(body);" in SRC
    assert "body:sentSig});" in SRC
    assert "SAVED_SIG = sentSig; checkDirty();\n    showInvoice" in SRC
    assert "recalc(); checkDirty();" in SRC
    assert '["input","change","click"].forEach(ev=>document.addEventListener(ev' in SRC


def test_leaving_with_unsaved_changes_warns():
    assert 'window.addEventListener("beforeunload"' in SRC


def test_editor_loads_and_sends_billed_for():
    assert 'billed_for:l.billed_for||""' in SRC
    assert "if(l.billed_for) b.billed_for=l.billed_for;" in SRC


def test_client_edit_keeps_billed_for():
    existing = {"msm-powder": {"slug": "msm-powder", "qty": 1, "note": "For Hershey",
                               "billed_for": "member@example.com"}}
    out = cil.rebuild([{"slug": "msm-powder", "qty": 0}], existing, known=lambda s: True)
    assert out == [{"slug": "msm-powder", "qty": 0, "format": "bottle",
                    "note": "For Hershey", "billed_for": "member@example.com"}]


def test_send_and_print_refuse_while_unsaved():
    assert 'if (isDirty()){ toast("Save your changes first. Printing' in SRC
    assert 'if (isDirty()){ invSendStatus(btn, "Save your changes first. Sending' in SRC


def _two_msm():
    return [{"slug": "msm-powder", "qty": 1, "note": "Hers", "source": "self"},
            {"slug": "msm-powder", "qty": 1, "note": "For Hershey", "source": "biofield",
             "billed_for": "member@example.com"}]


def test_client_edit_keeps_each_shared_slug_line_its_own():
    """Rounds 1 and 3: keyed by slug, both lines took the member's billed_for, and
    billing the member again deleted the caregiver's own line."""
    out = cil.rebuild([{"slug": "msm-powder", "qty": 2}, {"slug": "msm-powder", "qty": 0}],
                      _two_msm(), known=lambda s: True)
    assert out[0] == {"slug": "msm-powder", "qty": 2, "format": "bottle",
                      "note": "Hers", "source": "self"}
    assert out[1]["billed_for"] == "member@example.com" and out[1]["qty"] == 0


def test_a_line_added_beyond_the_stored_ones_copies_nothing():
    out = cil.rebuild([{"slug": "msm-powder", "qty": 1}] * 3, _two_msm(), known=lambda s: True)
    assert out[2] == {"slug": "msm-powder", "qty": 1, "format": "bottle"}


def test_customer_update_route_passes_stored_lines_in_order():
    app_src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    assert 'existing = [it for it in (order.get("items") or []) if (it.get("slug") or "").strip()]' in app_src
