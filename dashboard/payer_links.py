"""One email to a household payer with every invoice link they may pay.

Glen, 2026-09-19: Steve Fox pays for himself and Michael Hill, and wants both payment links
in one email. The portal's caregiver notice links only to the sign-in page.

When the last unpaid invoice in a household group is sent, each payer who may pay for
someone in the group gets one email. It lists their own invoice, if they have one there,
and the invoice of every member they may pay for (household.can_pay: consent, or a pet or
child). A group is the order's combined shipment, else its household hold group, else the
order alone.

Flag-gated by PAYER_LINKS_EMAIL_ENABLED, off by default. Real customer email. Idempotent:
the same payer is never sent the same set of orders twice.
"""
import os
from datetime import datetime, timezone
from html import escape

_CLOSED = ("cancelled", "shipped", "delivered", "done")


def enabled():
    return os.environ.get("PAYER_LINKS_EMAIL_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS payer_link_emails (
            payer_email TEXT NOT NULL,
            order_ids   TEXT NOT NULL,
            sent_at     TEXT NOT NULL,
            PRIMARY KEY (payer_email, order_ids)
        )""")


def _norm(e):
    return (e or "").strip().lower()


def group_orders(cx, order):
    """Open, unpaid orders that ship or hold with this one, including itself."""
    for col in ("group_shipment_id", "hold_group_id"):
        gid = order.get(col)
        if gid is not None:
            rows = cx.execute(
                f"SELECT id, email, name, total_cents, invoice_token, invoice_sent_at, "
                f"status, pay_status FROM orders WHERE {col}=? ORDER BY id", (gid,)).fetchall()
            break
    else:
        rows = cx.execute(
            "SELECT id, email, name, total_cents, invoice_token, invoice_sent_at, "
            "status, pay_status FROM orders WHERE id=?", (order["id"],)).fetchall()
    out = []
    for r in rows:
        d = {"id": r[0], "email": _norm(r[1]), "name": r[2] or "", "total_cents": int(r[3] or 0),
             "invoice_token": r[4] or "", "invoice_sent_at": r[5] or "",
             "status": r[6] or "", "pay_status": r[7] or "unpaid"}
        if d["status"] in _CLOSED or d["pay_status"] == "paid":
            continue
        out.append(d)
    return out


def plan(cx, order):
    """[(payer_email, [orders])] still to send for this order's group. Empty until every
    open order in the group has been sent an invoice."""
    from dashboard import household as _hh
    orders = group_orders(cx, order)
    if not orders or any(not (o["invoice_sent_at"] and o["invoice_token"]) for o in orders):
        return []
    _hh.init_household_tables(cx)
    init_table(cx)
    payers = {}
    for o in orders:
        for c in _hh.caregivers_for(cx, o["email"]):
            p = _norm(c["primary_email"])
            if p and p != o["email"] and _hh.can_pay(cx, p, o["email"]):
                payers.setdefault(p, []).append(o)
    out = []
    for p, theirs in payers.items():
        own = [o for o in orders if o["email"] == p]
        listed = own + [o for o in theirs if o not in own]
        key = ",".join(str(o["id"]) for o in sorted(listed, key=lambda o: o["id"]))
        if cx.execute("SELECT 1 FROM payer_link_emails WHERE payer_email=? AND order_ids=?",
                      (p, key)).fetchone():
            continue
        out.append((p, listed, key))
    return out


def compose(payer_first_name, orders, base_url):
    """(subject, plain, html) listing one line per person with a pay link."""
    greet = f"Aloha {payer_first_name}," if payer_first_name else "Aloha,"
    lines_plain, lines_html = [], []
    for o in orders:
        url = f"{base_url}/invoice/{o['invoice_token']}"
        first = (o["name"].split() or ["this"])[0]
        amount = f"${o['total_cents'] / 100:,.2f}"
        lines_plain.append(f"{o['name']}, {amount}: {url}")
        lines_html.append(f"<p>{escape(o['name'])}, {amount}: "
                          f"<a href=\"{escape(url)}\">View and pay {escape(first)}'s invoice</a></p>")
    n = len(orders)
    lead = ("Here are the invoices for your household's order. You can pay each one from its link."
            if n > 1 else "Here is the invoice for your household's order. You can pay it from this link.")
    subject = "Your household invoices" if n > 1 else "Your household invoice"
    plain = "\n\n".join([greet, lead] + lines_plain + ["Mahalo,\nDr. Glen Swartwout"])
    html = ("<div style=\"font-family: 'arial black', sans-serif; font-size: large\">"
            f"<p>{escape(greet)}</p><p>{lead}</p>" + "".join(lines_html)
            + "<p>Mahalo,<br>Dr. Glen Swartwout</p></div>")
    return subject, plain, html


def send_for(cx, order, send_email, base_url):
    """Send what plan() says. Records each send so it never repeats. Returns the payers sent."""
    if not enabled():
        return []
    sent = []
    for payer, orders, key in plan(cx, order):
        try:
            row = cx.execute("SELECT first_name FROM people WHERE lower(email)=? LIMIT 1",
                             (payer,)).fetchone()
            first = (row[0] or "").strip() if row else ""
        except Exception:
            first = ""
        subject, plain, html = compose(first, orders, base_url)
        send_email(payer, subject, plain, from_name="Dr. Glen Swartwout", html=html)
        cx.execute("INSERT INTO payer_link_emails (payer_email, order_ids, sent_at) "
                   "VALUES (?,?,?)", (payer, key, datetime.now(timezone.utc).isoformat()))
        cx.commit()
        sent.append({"payer": payer, "order_ids": [o["id"] for o in orders]})
    return sent
