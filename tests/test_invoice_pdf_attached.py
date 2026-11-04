"""The invoice email carries the formal invoice as a PDF.

Glen, 2026-11-04: the email held only a link, the link opens the pay-and-adjust page,
and the formal invoice sat behind a button. He could not check the invoice he sent.
"""
import base64

import pytest

from dashboard import invoice_pdf as P
from tests.test_phase3_paylink import _orders_db, _capture_send_email


def _summary(**kw):
    o = {"name": "Advanced Eyecare & Therapies", "external_ref": "INH-ABC",
         "created_at": "2026-10-30T01:36:12+00:00", "pay_status": "unpaid",
         "lines": [{"slug": "aces-eye-drops", "name": "ACES Eye Drops", "qty": 7,
                    "unit_cents": 2500, "srp_cents": 8000, "regular_cents": 7000},
                   {"slug": "biofield", "name": "Biofield Analysis", "qty": 1, "service": True,
                    "unit_cents": 30000, "srp_cents": 50000, "regular_cents": 40000,
                    "note": "Hawaiʻi time"}],
         "subtotal_cents": 47500, "shipping_cents": 1300, "total_cents": 48800,
         "balance_due_cents": 48800, "payments": []}
    o.update(kw)
    return o


def test_the_pdf_is_a_pdf():
    b = P.render_invoice_pdf(_summary())
    assert b[:5] == b"%PDF-" and len(b) > 5000


def test_a_long_invoice_runs_to_a_second_page():
    lines = [{"slug": f"p{i}", "name": f"Product {i}", "qty": 1, "unit_cents": 2500}
             for i in range(60)]
    one = P.render_invoice_pdf(_summary(lines=lines[:3]))
    many = P.render_invoice_pdf(_summary(lines=lines))
    assert many.count(b"/Type /Page\n") > one.count(b"/Type /Page\n") >= 1


def test_the_date_is_the_invoice_date_not_today():
    assert P.invoice_date("2026-10-30T01:36:12+00:00") == "Oct 30, 2026"


def test_services_print_first():
    assert [l["slug"] for l in P.svc_first(_summary()["lines"])] == ["biofield", "aces-eye-drops"]


def test_the_totals_match_the_print_view():
    rows = P.totals_rows(_summary(discount_cents=500, adjustment_cents=-1381,
                                  payments=[{"kind": "payment", "method": "Zelle",
                                             "amount_cents": 1000}],
                                  balance_due_cents=47800))
    assert [r[0] for r in rows] == ["Subtotal", "Discount", "Adjustment", "Shipping", "Total",
                                    "Paid · Zelle", "Balance due", "You save"]
    assert rows[1][1] == "−$5.00" and rows[2][1] == "−$13.81"
    # $55 a bottle saved on 7, $200 on the service.
    assert rows[-1][1] == "$585.00"


def test_a_settled_invoice_shows_no_balance_due():
    rows = P.totals_rows(_summary(balance_due_cents=0, pay_status="paid"))
    assert "Balance due" not in [r[0] for r in rows]


def test_the_file_name_says_invoice_or_receipt():
    assert P.filename(_summary()) == "Remedy-Match-invoice-INH-ABC.pdf"
    assert P.filename(_summary(pay_status="paid")) == "Remedy-Match-receipt-INH-ABC.pdf"
    assert P.filename(_summary(external_ref="a/../b c")) == "Remedy-Match-invoice-abc.pdf"


def _send(monkeypatch, **order):
    O, cx = _orders_db()
    monkeypatch.setenv("INVOICE_PAYLINK_ENABLED", "1")
    cap = _capture_send_email(monkeypatch)
    oid = O.upsert_order(cx, source="in-house", external_ref="INH-PDF", status="proposed",
                         total_cents=5000, email="a@b.com", **order)
    O._send_invoice_exec({"order_id": oid}, {"cx": cx})
    return O, cx, oid, cap


def test_the_invoice_email_attaches_the_pdf(monkeypatch):
    seen = {}

    def _render(summary):
        seen["summary"] = summary
        return b"%PDF-fake"
    monkeypatch.setattr(P, "render_invoice_pdf", _render)
    _, _, _, cap = _send(monkeypatch)
    assert cap["attachments"] == [("Remedy-Match-invoice-INH-PDF.pdf", "application/pdf",
                                   b"%PDF-fake")]
    assert "A PDF copy is attached." in cap["plain"]
    assert "A PDF copy is attached." in cap["html"]
    # Drawn from the same summary the invoice page reads, ledger balance included.
    assert seen["summary"]["external_ref"] == "INH-PDF"
    assert "balance_due_cents" in seen["summary"]


def test_a_pdf_fault_still_sends_the_link(monkeypatch):
    def _boom(summary):
        raise RuntimeError("no font")
    monkeypatch.setattr(P, "render_invoice_pdf", _boom)
    O, cx, oid, cap = _send(monkeypatch)
    assert cap["attachments"] is None
    assert "attached" not in cap["plain"] and "attached" not in cap["html"]
    assert "/invoice/" in cap["plain"]
    assert O.get_order(cx, oid)["invoice_sent_at"]


def test_send_email_carries_the_attachment(monkeypatch):
    from dashboard import inbox
    captured = {}

    class _Send:
        def execute(self):
            return {"id": "m1", "threadId": "t1"}

    class _Msgs:
        def send(self, userId, body):
            captured["raw"] = body["raw"]
            return _Send()

    class _Users:
        def messages(self):
            return _Msgs()

    class _Svc:
        def users(self):
            return _Users()

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)   # the send guard
    monkeypatch.setattr(inbox, "_get_gmail_service", lambda: _Svc())
    monkeypatch.setattr(inbox, "_is_undeliverable", lambda to: False)
    monkeypatch.setattr(inbox, "_execute_send", lambda svc, body: svc.users().messages()
                        .send(userId="me", body=body).execute())
    inbox.send_email("a@b.com", "Hi", "plain", html="<p>hi</p>",
                     attachments=[("inv.pdf", "application/pdf", b"%PDF-1.4 x")])
    raw = base64.urlsafe_b64decode(captured["raw"]).decode()
    assert "multipart/mixed" in raw and "multipart/alternative" in raw
    assert "Content-Type: application/pdf" in raw
    assert 'filename="inv.pdf"' in raw
    assert base64.b64encode(b"%PDF-1.4 x").decode() in raw


def test_a_line_after_a_note_starts_at_the_left_margin(monkeypatch):
    from fpdf import FPDF
    starts = []
    real = FPDF.cell

    def spy(self, w=None, h=None, text="", *a, **k):
        if text in ("ACES Eye Drops",) or str(text).startswith("Product"):
            starts.append(round(self.get_x(), 1))
        return real(self, w, h, text, *a, **k)
    real_mc = FPDF.multi_cell

    def spy_mc(self, w, h=None, text="", *a, **k):
        if not k.get("dry_run") and (text == "ACES Eye Drops" or str(text).startswith("Product")):
            starts.append(round(self.get_x(), 1))
        return real_mc(self, w, h, text, *a, **k)
    monkeypatch.setattr(FPDF, "cell", spy)
    monkeypatch.setattr(FPDF, "multi_cell", spy_mc)
    o = _summary()
    o["lines"] = o["lines"] + [{"slug": "p1", "name": "Product 1", "qty": 1,
                                "unit_cents": 100, "note": "a note"},
                               {"slug": "p2", "name": "Product 2", "qty": 1, "unit_cents": 100}]
    P.render_invoice_pdf(o)
    assert starts and set(starts) == {15.0}


def test_a_long_name_wraps_whole():
    from fpdf import FPDF
    long = "Macular Wellness Astaxanthin Extra Strength Liposomal Formula 120 count"
    b = P.render_invoice_pdf(_summary(lines=[{"slug": "x", "name": long, "qty": 1,
                                              "unit_cents": 100}]))
    assert b[:5] == b"%PDF-"
    assert "…" not in open(P.__file__).read().split("def render_invoice_pdf")[1]


def test_a_missing_zelle_name_prints_no_none(monkeypatch):
    from fpdf import FPDF
    texts = []
    real_mc = FPDF.multi_cell

    def spy_mc(self, w, h=None, text="", *a, **k):
        texts.append(str(text))
        return real_mc(self, w, h, text, *a, **k)
    monkeypatch.setattr(FPDF, "multi_cell", spy_mc)
    P.render_invoice_pdf(_summary(zelle_pay={"to": "pay-to", "name": None, "memo": None}))
    assert not any("None" in t for t in texts)


def test_symbols_the_font_lacks_become_words_or_go():
    assert P.clean("Take 2 → daily 🌿 ✓") == "Take 2 to daily yes"
    assert P.clean("1 ≤ 3x daily") == "1 at most 3x daily"
    # What the font has stays, ʻokina and minus included.
    assert P.clean("Hawaiʻi − ‘ok’ · café") == "Hawaiʻi − ‘ok’ · café"
    assert P.clean("one\ntwo") == "one\ntwo"
