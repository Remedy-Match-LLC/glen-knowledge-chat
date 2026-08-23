"""Merge the authoritative Healing Oasis Invoices.fmp12 history into FMP orders.

Only commerce/identity fields cross the production boundary. Invoices Old.fmp12
is intentionally unsupported because its rows are included in Invoices.fmp12.
"""
import csv
import datetime


def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _id(value):
    value = (value or "").strip()
    return value[:-2] if value.endswith(".0") and value[:-2].isdigit() else value


def _date(value):
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def build_payload(invoice_summary_csv, invoice_items_csv, contacts_csv):
    contacts = {}
    for r in _rows(contacts_csv):
        cid = _id(r.get("Contact ID"))
        if cid:
            contacts[cid] = [cid, r.get("First") or "", r.get("Last") or "",
                             r.get("Company Name") or "", (r.get("email") or "").strip().lower(),
                             r.get("Phone1") or "", "", ""]
    invoices, valid_ids, skipped = [], set(), {"missing_client": 0, "invalid": 0}
    for r in _rows(invoice_summary_csv):
        iid, cid, date = _id(r.get("Invoice ID")), _id(r.get("Contact ID")), _date(r.get("Invoice Date"))
        if not iid or not cid or not date:
            skipped["invalid"] += 1
            continue
        if cid not in contacts:
            skipped["missing_client"] += 1
            continue
        invoices.append([iid, cid, date, "Historical", r.get("Sub Total") or "",
                         r.get("Grand Total") or "", r.get("Shipping Cost") or "", "0"])
        valid_ids.add(iid)
    items, ordinals = [], {}
    for r in _rows(invoice_items_csv):
        iid = _id(r.get("Invoice ID"))
        if iid not in valid_ids:
            continue
        ordinals[iid] = ordinals.get(iid, 0) + 1
        items.append([f"healing-oasis:{iid}:{ordinals[iid]}", iid, _id(r.get("Product ID")),
                      r.get("Description") or "", r.get("Quantity") or "",
                      r.get("Price") or "", r.get("Line Item Total") or ""])
    used_clients = {r[1] for r in invoices}
    return {"source": "healing_oasis_invoices_fmp12",
            "clients": [contacts[c] for c in sorted(used_clients)],
            "invoices": invoices, "items": items, "skipped": skipped}


def ingest_payload(cx, payload, *, commit=False):
    from dashboard import fmp_orders
    fmp_orders.ensure_tables(cx)
    specs = (("clients", "fmp_clients", fmp_orders._CLIENT_COLS),
             ("invoices", "fmp_invoices", fmp_orders._INV_COLS),
             ("items", "fmp_invoice_items", fmp_orders._ITEM_COLS))
    result = {"dry_run": not commit}
    for key, table, cols in specs:
        incoming = [tuple(r) for r in payload.get(key, []) if len(r) == len(cols)]
        existing = {r[0] for r in cx.execute(f"SELECT {cols[0]} FROM {table}").fetchall()}
        missing = [r for r in incoming if r[0] not in existing]
        result[key] = {"received": len(incoming), "existing": len(incoming) - len(missing),
                       "inserted": len(missing)}
        if commit and missing:
            ph = ",".join("?" * len(cols))
            cx.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", missing)
    if commit:
        cx.commit()
    return result
