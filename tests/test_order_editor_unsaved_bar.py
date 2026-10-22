"""Edit Invoice shows a save bar while changes are unsaved, and an edit keeps each
line's billed_for (Glen, 2026-10-22: "I forgot the save button the first time")."""
from pathlib import Path

from dashboard import client_invoice_lines as cil

SRC = (Path(__file__).resolve().parent.parent / "static" / "order-new.html").read_text()


def test_unsaved_bar_exists_and_saves():
    assert '<div id="unsaved-bar" role="status">' in SRC
    assert 'This invoice has unsaved changes.</span><button class="primary" onclick="submitOrder()">' in SRC


def test_bar_compares_against_what_was_loaded_or_saved():
    assert "editSig()!==SAVED_SIG" in SRC
    assert "await loadOrderForEdit(EDIT_OID); markSaved();" in SRC
    assert "markSaved();\n    showInvoice" in SRC
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
