"""A purchase order as a PDF, for Glen to attach to a supplier email.

Glen, 2026-11-26: make a purchase order in the console, tap a button, get a PDF.
FileMaker used to do this; its last PDFs were titled "EXPENSE #: 1076". Nothing is
emailed from the app.

Same look and font handling as dashboard/invoice_pdf.py, and its clean() helper.
Headings are sentence case: Glen's no-caps rule. A blank cost prints blank: no
amount is ever invented.
"""
from __future__ import annotations

import os
import re
from datetime import date

from dashboard.invoice_pdf import FONT_DIR, FOOT, GOLD, GREEN, GREY, INK, LOGO, RULE, clean

# Header details: confirm with Glen. Our company block and the default "Ship to".
# Never put any other phone number here.
PO_COMPANY = {
    "name": "Remedy Match LLC",
    "address_lines": ["PO Box 126", "Hilo, HI 96721"],
    "phone": "(808) 217-9647",
    "email": "support@remedymatch.com",
}

# Qty, Description, Unit cost, Amount, in mm across a Letter page's body.
_COLS = (28, 98, 30, 29.9)


def _money(v) -> str:
    return f"${float(v):,.2f}"


def _unit_cost(v) -> str:
    """Two decimals, or up to four when a per-gram price needs them."""
    v = float(v)
    s = f"{v:,.4f}".rstrip("0")
    whole, _, frac = s.partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def _qty(q, unit) -> str:
    if q is None:
        return ""
    q = float(q)
    t = f"{q:,.3f}".rstrip("0").rstrip(".")
    return f"{t} {unit}".strip() if unit else t


def po_date_text(po_date) -> str:
    try:
        d = date.fromisoformat(str(po_date or "")[:10])
    except ValueError:
        d = date.today()
    return f"{d:%b} {d.day}, {d.year}"


def _supplier_name(po, supplier=None) -> str:
    return str((supplier or {}).get("company") or po.get("supplier_company")
               or po.get("supplier_name") or "").strip()


def filename(po, supplier=None) -> str:
    """'PO 1120 Lonza.pdf'."""
    sup = re.sub(r"[^A-Za-z0-9 &-]", "", _supplier_name(po, supplier))
    sup = re.sub(r"\s+", " ", sup).strip()[:40].strip()
    num = po.get("po_number") or po.get("id") or ""
    return " ".join(p for p in ("PO", str(num), sup) if p) + ".pdf"


def _amount(it):
    if it.get("cost") is None or it.get("qty") is None:
        return None
    return float(it["qty"]) * float(it["cost"])


def totals_rows(po, items):
    """(label, amount) rows, only for amounts that exist. Subtotal and Total need a
    cost on every line: a total over some lines would read as the whole order."""
    amounts = [_amount(it) for it in items]
    priced = bool(items) and all(a is not None for a in amounts)
    rows = []
    sub = sum(amounts) if priced else None
    if sub is not None:
        rows.append(("Subtotal", _money(sub)))
    ship = po.get("shipping_amount")
    if ship is not None:
        rows.append(("Shipping", _money(ship)))
    tax = po.get("tax")
    if tax is not None:
        rows.append(("Tax", _money(tax)))
    if sub is not None:
        rows.append(("Total", _money(sub + float(ship or 0) + float(tax or 0))))
    return rows


def _supplier_lines(supplier, po):
    s = supplier or {}
    out = [_supplier_name(po, s)]
    if s.get("address_street"):
        out.append(s["address_street"])
    city = ", ".join(x for x in (s.get("address_city"), s.get("address_province")) if x)
    city = " ".join(x for x in (city, s.get("address_postal_code")) if x)
    if city:
        out.append(city)
    if s.get("phone_business"):
        out.append(s["phone_business"])
    if s.get("email"):
        out.append(s["email"])
    return [clean(x) for x in out if x]


def _company_lines(company):
    return [company.get("name") or ""] + list(company.get("address_lines") or []) + \
        [x for x in (company.get("phone"), company.get("email")) if x]


def render_po_pdf(po, items, supplier=None, company=None, ship_to=None) -> bytes:
    """One purchase order as PDF bytes. `ship_to` defaults to the company block."""
    from fpdf import FPDF

    company = company or PO_COMPANY
    ship_to = ship_to or company
    items = list(items or [])

    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_title(f"Purchase order {po.get('po_number') or ''}".strip())
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("Noto", "", os.path.join(FONT_DIR, "NotoSerif-Regular.ttf"))
    pdf.add_font("Noto", "B", os.path.join(FONT_DIR, "NotoSerif-Bold.ttf"))
    pdf.add_font("Noto", "I", os.path.join(FONT_DIR, "NotoSerif-Italic.ttf"))
    pdf.add_page()
    left, width = pdf.l_margin, pdf.epw

    # Header: logo and our company on the left, "Purchase order" and its number right.
    top = pdf.get_y()
    if os.path.exists(LOGO):
        pdf.image(LOGO, x=left, y=top, w=20, h=20)
    pdf.set_xy(left + 24, top + 2)
    pdf.set_font("Noto", "B", 14)
    pdf.set_text_color(*GREEN)
    pdf.cell(80, 7, clean(company.get("name")))
    pdf.set_font("Noto", "", 8.5)
    pdf.set_text_color(0x44, 0x44, 0x44)
    y = top + 10
    for line in _company_lines(company)[1:]:
        pdf.set_xy(left + 24, y)
        pdf.cell(80, 4.5, clean(line))
        y += 4.5
    pdf.set_xy(left + 100, top + 2)
    pdf.set_font("Noto", "B", 9)
    pdf.set_text_color(*GOLD)
    pdf.cell(width - 100, 5, "Purchase Order", align="R")
    if po.get("po_number"):
        pdf.set_xy(left + 100, top + 7.5)
        pdf.set_font("Noto", "B", 15)
        pdf.set_text_color(*INK)
        pdf.cell(width - 100, 6.5, f"PO {po['po_number']}", align="R")
    pdf.set_xy(left + 100, top + 15)
    pdf.set_font("Noto", "", 9.5)
    pdf.set_text_color(*GREY)
    pdf.cell(width - 100, 5, "Date " + po_date_text(po.get("po_date")), align="R")
    y = max(top + 23, y + 1)
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.6)
    pdf.line(left, y, left + width, y)

    # Supplier on the left, ship-to on the right.
    def block(x, y0, w, label, lines):
        pdf.set_xy(x, y0)
        pdf.set_font("Noto", "", 7.5)
        pdf.set_text_color(*GREY)
        pdf.cell(w, 4.5, label)
        yy = y0 + 5
        for i, line in enumerate(lines):
            pdf.set_xy(x, yy)
            pdf.set_font("Noto", "B" if i == 0 else "", 10 if i == 0 else 9)
            pdf.set_text_color(*INK)
            pdf.multi_cell(w, 5, line, align="L", new_x="LMARGIN", new_y="NEXT")
            yy = pdf.get_y()
        return yy

    y0 = y + 4
    half = width / 2
    y1 = block(left, y0, half - 4, "Supplier", _supplier_lines(supplier, po))
    y2 = block(left + half, y0, half, "Ship to",
               [clean(x) for x in _company_lines(ship_to) if x])
    pdf.set_xy(left, max(y1, y2) + 6)

    def head_row():
        pdf.set_font("Noto", "B", 8.5)
        pdf.set_text_color(*GREEN)
        for i, h in enumerate(("Qty", "Description", "Unit cost", "Amount")):
            pdf.cell(_COLS[i], 6, h, align="L" if i in (0, 1) else "R")
        pdf.ln(6)
        pdf.set_draw_color(*GREEN)
        pdf.set_line_width(0.4)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    head_row()
    if not items:
        pdf.set_font("Noto", "", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(width, 7, "No items.")
        pdf.ln(7)
    for it in items:
        desc = clean(it.get("item_label") or it.get("material_name")
                     or it.get("ingredient_canonical") or "")
        pdf.set_font("Noto", "", 9.5)
        wrapped = pdf.multi_cell(_COLS[1], 5, desc, align="L", dry_run=True, output="LINES")
        h = max(7, 5 * len(wrapped) + 2)
        if pdf.will_page_break(h):
            pdf.add_page()
            head_row()
        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_text_color(*INK)
        pdf.cell(_COLS[0], 7, clean(_qty(it.get("qty"), it.get("qty_unit"))))
        pdf.set_xy(x0 + _COLS[0], y0 + 1)
        pdf.multi_cell(_COLS[1], 5, desc, align="L", new_x="RIGHT", new_y="TOP")
        pdf.set_xy(x0 + _COLS[0] + _COLS[1], y0)
        cost = it.get("cost")
        amt = _amount(it)
        pdf.cell(_COLS[2], 7, _unit_cost(cost) if cost is not None else "", align="R")
        pdf.cell(_COLS[3], 7, _money(amt) if amt is not None else "", align="R")
        pdf.set_xy(left, y0 + h)
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        pdf.line(left, pdf.get_y(), left + width, pdf.get_y())

    rows = totals_rows(po, items)
    if rows:
        pdf.ln(3)
        tx, tw = left + width * 0.52, width * 0.48
        for label, amount in rows:
            if pdf.will_page_break(8):
                pdf.add_page()
            if label == "Total":
                pdf.set_draw_color(*GREEN)
                pdf.set_line_width(0.4)
                pdf.line(tx, pdf.get_y() + 1, tx + tw, pdf.get_y() + 1)
                pdf.ln(2)
                pdf.set_font("Noto", "B", 11)
            else:
                pdf.set_font("Noto", "", 9.5)
            pdf.set_text_color(*INK)
            pdf.set_x(tx)
            pdf.cell(tw / 2, 6.5, label)
            pdf.cell(tw / 2, 6.5, amount, align="R")
            pdf.ln(6.5)

    pdf.ln(8)
    pdf.set_draw_color(0xe2, 0xe2, 0xe2)
    pdf.set_line_width(0.2)
    pdf.line(left, pdf.get_y(), left + width, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Noto", "I", 9)
    pdf.set_text_color(*FOOT)
    contact = " · ".join(x for x in (company.get("phone"), company.get("email")) if x)
    pdf.cell(width, 6, clean(f"Questions? {contact}"))
    return bytes(pdf.output())

