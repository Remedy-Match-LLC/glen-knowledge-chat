from pathlib import Path


HTML = (Path(__file__).parents[1] / "static" / "invoice.html").read_text()


def test_printed_invoice_uses_original_created_date():
    """Reprinting a July invoice in September must still say July."""
    assert "const createdYmd = String(o.created_at||'').slice(0,10);" in HTML
    assert '<span class="pd-k">Date</span> ${esc(invoiceDate)}' in HTML
    assert '<span class="pd-k">Date</span> ${esc(today)}' not in HTML


def test_printed_invoice_date_avoids_utc_to_hawaii_day_shift():
    assert "new Date(createdParts[0], createdParts[1]-1, createdParts[2], 12)" in HTML
