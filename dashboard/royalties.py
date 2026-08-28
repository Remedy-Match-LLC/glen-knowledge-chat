"""Amazon royalty ledger sourced from posted QuickBooks Online deposits.

QuickBooks' bank-feed ``For review`` queue is not part of the normal Accounting
API.  This module therefore imports posted Deposit entities and classifies only
strong Amazon/KDP/ACX matches.  Ambiguous Amazon rows are retained as
``amazon_review`` instead of being silently assigned to Kindle or Audible.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from dashboard import db
from dashboard import money


_AUDIBLE = re.compile(r"\b(?:AUDIBLE|ACX)\b", re.I)
_KINDLE = re.compile(r"\b(?:KDP|KINDLE|AMAZON\s+DIGITAL|AMZN\s+KDP)\b", re.I)
_AMAZON = re.compile(r"\b(?:AMAZON|AMZN)\b", re.I)


def init_table(cx):
    cx.execute("""
        CREATE TABLE IF NOT EXISTS royalty_payments (
          qbo_id TEXT PRIMARY KEY,
          txn_date TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          source TEXT NOT NULL,
          confidence TEXT NOT NULL,
          descriptor TEXT,
          bank_account_id TEXT,
          bank_account_name TEXT,
          qbo_updated_at TEXT,
          raw_json TEXT,
          first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _ref_text(value):
    if isinstance(value, dict):
        return " ".join(str(value.get(k) or "") for k in ("name", "value"))
    return str(value or "")


def descriptor(deposit):
    """Flatten the human-visible fields that can carry a bank-feed payor name."""
    parts = [deposit.get("PrivateNote"), deposit.get("DocNumber"),
             _ref_text(deposit.get("CurrencyRef"))]
    for line in deposit.get("Line") or []:
        parts.extend([line.get("Description"), _ref_text(line.get("LinkedTxn"))])
        detail = line.get("DepositLineDetail") or {}
        parts.extend([_ref_text(detail.get("Entity")),
                      _ref_text(detail.get("AccountRef")),
                      _ref_text(detail.get("PaymentMethodRef")),
                      detail.get("CheckNum")])
    return " | ".join(" ".join(str(p).split()) for p in parts if p).strip()


def classify(text):
    """Return (source, confidence), or (None, None) for a non-Amazon deposit."""
    text = text or ""
    if _AUDIBLE.search(text):
        return "audible_acx", "high"
    if _KINDLE.search(text):
        return "kindle_kdp", "high"
    if _AMAZON.search(text):
        return "amazon_review", "review"
    return None, None


def _amount_cents(deposit):
    try:
        return int(round(float(deposit.get("TotalAmt") or 0) * 100))
    except (TypeError, ValueError):
        return 0


def sync(db_path, *, days_back=120, fetch_fn=None):
    """Import matching posted QBO deposits; idempotent on the QBO Deposit Id."""
    since = (date.today() - timedelta(days=max(1, int(days_back)))).isoformat()
    if fetch_fn is None:
        token = money.qb_refresh()

        def fetch_fn(query):
            payload = money.qb_get(token, "/query", {"query": query})
            return payload.get("QueryResponse", {}).get("Deposit", [])

    query = f"SELECT * FROM Deposit WHERE TxnDate >= '{since}' MAXRESULTS 1000"
    deposits = fetch_fn(query) or []
    matched = upserted = 0
    with db.connect(db_path) as cx:
        init_table(cx)
        for dep in deposits:
            qbo_id = str(dep.get("Id") or "").strip()
            text = descriptor(dep)
            source, confidence = classify(text)
            if not qbo_id or not source:
                continue
            matched += 1
            bank = dep.get("DepositToAccountRef") or {}
            params = (
                qbo_id, str(dep.get("TxnDate") or ""), _amount_cents(dep),
                source, confidence, text[:2000], str(bank.get("value") or ""),
                str(bank.get("name") or ""),
                str((dep.get("MetaData") or {}).get("LastUpdatedTime") or ""),
                json.dumps(dep, separators=(",", ":"), default=str)[:50000],
            )
            cx.execute("""
                INSERT INTO royalty_payments
                  (qbo_id, txn_date, amount_cents, source, confidence, descriptor,
                   bank_account_id, bank_account_name, qbo_updated_at, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(qbo_id) DO UPDATE SET
                  txn_date=excluded.txn_date, amount_cents=excluded.amount_cents,
                  source=excluded.source, confidence=excluded.confidence,
                  descriptor=excluded.descriptor,
                  bank_account_id=excluded.bank_account_id,
                  bank_account_name=excluded.bank_account_name,
                  qbo_updated_at=excluded.qbo_updated_at, raw_json=excluded.raw_json,
                  updated_at=CURRENT_TIMESTAMP
            """, params)
            upserted += 1
        cx.commit()
    return {"scanned": len(deposits), "matched": matched, "upserted": upserted,
            "since": since}


def list_payments(db_path, *, limit=200):
    limit = max(1, min(int(limit), 1000))
    with db.connect(db_path) as cx:
        init_table(cx)
        rows = cx.execute(
            "SELECT qbo_id, txn_date, amount_cents, source, confidence, descriptor, "
            "bank_account_id, bank_account_name, qbo_updated_at, first_seen_at, updated_at "
            "FROM royalty_payments ORDER BY txn_date DESC, qbo_id DESC LIMIT ?", (limit,)
        ).fetchall()
    cols = ("qbo_id", "txn_date", "amount_cents", "source", "confidence",
            "descriptor", "bank_account_id", "bank_account_name", "qbo_updated_at",
            "first_seen_at", "updated_at")
    return [dict(zip(cols, tuple(row))) for row in rows]


def summary(db_path):
    payments = list_payments(db_path, limit=1000)
    totals = {}
    for row in payments:
        totals[row["source"]] = totals.get(row["source"], 0) + row["amount_cents"]
    return {"payments": payments, "totals_cents": totals,
            "review_count": sum(p["source"] == "amazon_review" for p in payments)}
