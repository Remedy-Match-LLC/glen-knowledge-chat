"""The formal invoice as a PDF, attached to the invoice email.

Glen, 2026-11-04: the invoice email carried only a link, and the link opens the
pay-and-adjust page. The formal invoice appeared only behind "Save Invoice as PDF".
This draws the same document as buildInvoicePrintHtml() in static/invoice.html, from
the same customer-safe summary the page reads (app._invoice_summary_with_ledger).

Headings are title case, not the print view's capitals: Glen's no-caps rule.
Noto Serif (dashboard/fonts, SIL Open Font License) carries the ʻokina and the minus
sign, which the PDF core fonts cannot.
"""
from __future__ import annotations

import functools
import os
import re
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(_HERE, "fonts")
LOGO = os.path.join(os.path.dirname(_HERE), "static", "favicon.png")

GREEN = (0x3a, 0x5a, 0x40)
GOLD = (0xb8, 0x90, 0x2f)
INK = (0x1b, 0x1b, 0x1b)
GREY = (0x8a, 0x8a, 0x8a)
ANCHOR = (0x9a, 0x9a, 0x9a)
RULE = (0xec, 0xec, 0xec)
NOTE = (0x5b, 0x56, 0x70)
FOOT = (0x55, 0x55, 0x55)

# Item, Value, Regular, Price, Qty, Amount, in mm across a Letter page's 7.3in body.
_COLS = (78, 22, 22, 22, 13, 28)


# Symbols Noto Serif lacks would vanish from the PDF (review round 3), so they become
# words. Anything else outside the font, such as emoji, is dropped.
_SWAP = {"→": "to", "←": "from", "≤": "at most", "≥": "at least", "✓": "yes",
         "✔": "yes", "×": "x", "≈": "about"}


@functools.lru_cache(maxsize=1)
def _glyphs():
    from fontTools.ttLib import TTFont
    return frozenset(TTFont(os.path.join(FONT_DIR, "NotoSerif-Regular.ttf")).getBestCmap())


def clean(text) -> str:
    t = "".join(f" {_SWAP[ch]} " if ch in _SWAP else ch for ch in str(text or ""))
    have = _glyphs()
    t = "".join(ch for ch in t if ch in "\n\t" or ord(ch) in have)
    return re.sub(r"[ \t]{2,}", " ", t).strip(" ")


def money(cents) -> str:
    return f"${int(cents or 0) / 100:,.2f}"


def invoice_date(created_at) -> str:
    """The invoice's own date, never today's: same rule as the print view."""
    ymd = str(created_at or "")[:10]
    try:
        d = date.fromisoformat(ymd)
    except ValueError:
        d = date.today()
    return f"{d:%b} {d.day}, {d.year}"


def svc_first(lines):
    """Service-fee lines first; otherwise the original order."""
    return [l for l in lines if l.get("service")] + [l for l in lines if not l.get("service")]


def total_savings(lines) -> int:
    return sum((int(l["srp_cents"]) - int(l.get("unit_cents") or 0)) * int(l.get("qty") or 0)
               for l in lines
               if l.get("srp_cents") and int(l["srp_cents"]) > int(l.get("unit_cents") or 0))


def totals_rows(o):
    """(label, amount text, style) in the order the print view shows them."""
    rows = [("Subtotal", money(o.get("subtotal_cents")), "")]
    if o.get("discount_cents"):
        rows.append(("Discount", "−" + money(o["discount_cents"]), ""))
    adj = int(o.get("adjustment_cents") or 0)
    if adj:
        rows.append(("Adjustment", ("−" if adj < 0 else "+") + money(abs(adj)), ""))
    rows.append(("Shipping", money(o.get("shipping_cents")), ""))
    if o.get("get_cents"):
        rows.append(("Hawaiʻi GET (absorbed)", money(o["get_cents"]), ""))
    if o.get("points_redeemed_cents"):
        rows.append(("Points", "−" + money(o["points_redeemed_cents"]), ""))
    if o.get("ship_credit_applied_cents"):
        rows.append(("Shipping credit", "−" + money(o["ship_credit_applied_cents"]), ""))
    rows.append(("Total", money(o.get("total_cents")), "grand"))
    for p in o.get("payments") or []:
        refund = p.get("kind") == "refund"
        label = ("Refund" if refund else "Paid") + (f" · {p['method']}" if p.get("method") else "")
        rows.append((label, ("−" if refund else "") + money(p.get("amount_cents")), ""))
    if int(o.get("balance_due_cents") or 0) > 0:
        rows.append(("Balance due", money(o["balance_due_cents"]), "grand"))
    saved = total_savings(o.get("lines") or [])
    if saved > 0:
        rows.append(("You save", money(saved), "save"))
    return rows


def filename(o) -> str:
    ref = "".join(ch for ch in str(o.get("external_ref") or "invoice") if ch.isalnum() or ch in "-_")
    kind = "receipt" if o.get("pay_status") == "paid" else "invoice"
    return f"Remedy-Match-{kind}-{ref or 'invoice'}.pdf"


def render_invoice_pdf(o) -> bytes:
    """The formal invoice for one order summary, as PDF bytes."""
    from fpdf import FPDF

    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("Noto", "", os.path.join(FONT_DIR, "NotoSerif-Regular.ttf"))
    pdf.add_font("Noto", "B", os.path.join(FONT_DIR, "NotoSerif-Bold.ttf"))
    pdf.add_font("Noto", "I", os.path.join(FONT_DIR, "NotoSerif-Italic.ttf"))
    pdf.add_page()
    left, width = pdf.l_margin, pdf.epw

    # Header: logo, business, then "Invoice" and who it is for on the right.
    top = pdf.get_y()
    if os.path.exists(LOGO):
        pdf.image(LOGO, x=left, y=top, w=20, h=20)
    pdf.set_xy(left + 24, top + 2)
    pdf.set_font("Noto", "B", 14)
    pdf.set_text_color(*GREEN)
    pdf.cell(80, 7, "Remedy Match LLC")
    pdf.set_font("Noto", "", 8.5)
    pdf.set_text_color(0x44, 0x44, 0x44)
    pdf.set_xy(left + 24, top + 10)
    pdf.cell(80, 4.5, "351 Wailuku Drive, Hilo, Hawaiʻi 96720")
    pdf.set_xy(left + 24, top + 14.5)
    pdf.cell(80, 4.5, "(808) 217-9647 · illtowell.com")
    pdf.set_xy(left + 100, top + 2)
    pdf.set_font("Noto", "B", 9)
    pdf.set_text_color(*GOLD)
    pdf.cell(width - 100, 5, "Invoice", align="R")
    if o.get("name"):
        # Right of the address block, wrapping rather than running into it.
        pdf.set_xy(left + 100, top + 7.5)
        pdf.set_font("Noto", "B", 15 if len(str(o["name"])) <= 30 else 12)
        pdf.set_text_color(*INK)
        pdf.multi_cell(width - 100, 6.5, clean(o["name"]), align="R",
                       new_x="LMARGIN", new_y="NEXT")
    y = max(top + 23, pdf.get_y() + 1)
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.6)
    pdf.line(left, y, left + width, y)

    # Reference and date.
    pdf.set_xy(left, y + 4)
    pdf.set_font("Noto", "", 9.5)
    if o.get("external_ref"):
        pdf.set_text_color(*GREY)
        pdf.cell(pdf.get_string_width("Invoice ") + 0.5, 6, "Invoice ")
        pdf.set_text_color(*INK)
        pdf.cell(pdf.get_string_width(str(o["external_ref"])) + 8, 6, str(o["external_ref"]))
    pdf.set_text_color(*GREY)
    pdf.cell(pdf.get_string_width("Date ") + 0.5, 6, "Date ")
    pdf.set_text_color(*INK)
    pdf.cell(40, 6, invoice_date(o.get("created_at")))
    pdf.ln(10)

    def head_row():
        pdf.set_font("Noto", "B", 8.5)
        pdf.set_text_color(*GREEN)
        for i, h in enumerate(("Item", "Value", "Regular", "Price", "Qty", "Amount")):
            pdf.cell(_COLS[i], 6, h, align="L" if i == 0 else "R")
        pdf.ln(6)
        pdf.set_draw_color(*GREEN)
        pdf.set_line_width(0.4)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    def struck(w, text):
        """A right-aligned anchor price with a line through it."""
        x, yy = pdf.get_x(), pdf.get_y()
        pdf.cell(w, 7, text, align="R")
        if text:
            tw = pdf.get_string_width(text)
            pdf.set_draw_color(*ANCHOR)
            pdf.set_line_width(0.2)
            pdf.line(x + w - pdf.c_margin - tw, yy + 3.6, x + w - pdf.c_margin, yy + 3.6)

    head_row()
    lines = svc_first(o.get("lines") or [])
    if not lines:
        pdf.set_font("Noto", "", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(width, 7, "No items.")
        pdf.ln(7)
    for l in lines:
        note = clean(l.get("note")).strip()
        price, qty = int(l.get("unit_cents") or 0), int(l.get("qty") or 0)
        pdf.set_text_color(*INK)
        pdf.set_font("Noto", "B" if l.get("service") else "", 9.5)
        name = clean(l.get("name") or l.get("slug") or "")
        # A long name wraps, as on the page: two products differing only in a
        # suffix must not print alike (review round 2).
        wrapped = pdf.multi_cell(_COLS[0], 5, name, align="L", dry_run=True, output="LINES")
        h = max(7, 5 * len(wrapped) + 2)
        if pdf.will_page_break(h + (5 if note else 0)):
            pdf.add_page()
            head_row()
        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_xy(x0, y0 + 1)
        pdf.multi_cell(_COLS[0], 5, name, align="L", new_x="RIGHT", new_y="TOP")
        pdf.set_xy(x0 + _COLS[0], y0)
        pdf.set_font("Noto", "", 8.5)
        pdf.set_text_color(*ANCHOR)
        for i, key in ((1, "srp_cents"), (2, "regular_cents")):
            v = int(l.get(key) or 0)
            struck(_COLS[i], money(v) if v > price else "")
        pdf.set_font("Noto", "", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(_COLS[3], 7, money(price), align="R")
        pdf.cell(_COLS[4], 7, str(qty), align="R")
        pdf.cell(_COLS[5], 7, money(price * qty), align="R")
        pdf.set_xy(left, y0 + h)
        if note:
            pdf.set_font("Noto", "", 8)
            pdf.set_text_color(*NOTE)
            pdf.multi_cell(width, 4.5, note, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    # Totals, in the right half.
    pdf.ln(3)
    tx, tw = left + width * 0.52, width * 0.48
    for label, amount, style in totals_rows(o):
        if pdf.will_page_break(8):
            pdf.add_page()
        if style == "grand":
            pdf.set_draw_color(*GREEN)
            pdf.set_line_width(0.4)
            pdf.line(tx, pdf.get_y() + 1, tx + tw, pdf.get_y() + 1)
            pdf.ln(2)
            pdf.set_font("Noto", "B", 11)
            pdf.set_text_color(*INK)
        elif style == "save":
            pdf.set_font("Noto", "B", 9.5)
            pdf.set_text_color(*GREEN)
        else:
            pdf.set_font("Noto", "", 9.5)
            pdf.set_text_color(*INK)
        pdf.set_x(tx)
        pdf.cell(tw / 2, 6.5, label)
        pdf.cell(tw / 2, 6.5, amount, align="R")
        pdf.ln(6.5)

    pdf.set_font("Noto", "", 9)
    pdf.set_text_color(*INK)
    zp = o.get("zelle_pay") or {}
    if zp.get("to"):
        pdf.ln(3)
        pdf.multi_cell(width, 5, f"Pay by Zelle to {zp.get('name') or ''}, {zp.get('to')}, "
                                 f"memo: {zp.get('memo') or ''}", new_x="LMARGIN", new_y="NEXT")
    if o.get("pay_status") == "paid" and int(o.get("balance_due_cents") or 0) <= 0:
        pdf.ln(3)
        pdf.set_font("Noto", "B", 9.5)
        pdf.set_text_color(*GREEN)
        pdf.cell(40, 6, "Paid. Mahalo.")
        pdf.ln(6)
    if o.get("invoice_note"):
        pdf.ln(4)
        pdf.set_font("Noto", "", 7.5)
        pdf.set_text_color(*GREY)
        pdf.cell(width, 4.5, "Notes")
        pdf.ln(4.5)
        pdf.set_font("Noto", "", 9)
        pdf.set_text_color(*INK)
        pdf.multi_cell(width, 5, clean(o["invoice_note"]), align="L", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_draw_color(0xe2, 0xe2, 0xe2)
    pdf.set_line_width(0.2)
    pdf.line(left, pdf.get_y(), left + width, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Noto", "I", 9)
    pdf.set_text_color(*FOOT)
    pdf.cell(width, 6, "Thank you. Questions? (808) 217-9647 · Remedy Match LLC, Hilo, Hawaiʻi")
    return bytes(pdf.output())
