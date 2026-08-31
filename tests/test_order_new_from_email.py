from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_new_order_has_customer_email_button_and_gmail_query():
    body = (ROOT / "static" / "order-new.html").read_text()
    assert 'id="order-from-email-wrap"' in body
    assert "Enter order from email" in body
    assert "function openCustomerEmail()" in body
    assert "from:${email} OR to:${email}" in body
    assert '(p.id && p.email) ? "block" : "none"' in body


def test_inbox_accepts_prefilled_gmail_query_from_url():
    body = (ROOT / "static" / "console-inbox.html").read_text()
    assert 'const _urlParams = new URLSearchParams(location.search)' in body
    assert '_urlParams.get("q")' in body
    assert 'document.getElementById("search").value = initialQuery' in body
