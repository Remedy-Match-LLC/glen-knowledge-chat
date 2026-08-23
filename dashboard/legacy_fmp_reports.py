"""Privacy-bounded migration of finalized reports from legacy FMP exports.

The old Healing Oasis system split a report across Records.fmp12 and
Electronic.fmp12.  This module builds a small, JSON-safe projection locally;
the production endpoint never receives the 1,000+ raw clinical columns.
"""
import csv
import datetime
from collections import defaultdict
from pathlib import Path


def _date(value):
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_payload(records_csv, electronic_csv, contacts_csv):
    """Return only identity, date, provenance and report-display fields.

    Same-client/same-day Records rows are merged. Records without exactly one
    syntactically valid email or a valid date are counted but never exported.
    Electronic rows augment the matching dated report's provenance; they do not
    become a second report because the legacy app treated them as the same visit.
    """
    contacts = defaultdict(set)
    names = {}
    for row in _rows(contacts_csv):
        pid = (row.get("PtId#") or "").strip()
        email = (row.get("email") or "").strip().lower()
        if pid and email.count("@") == 1 and not any(c.isspace() for c in email):
            contacts[pid].add(email)
            names[email] = (row.get("Full Name") or
                            " ".join((row.get("First") or "", row.get("Last") or ""))).strip()

    grouped = {}
    skipped = defaultdict(int)
    for ordinal, row in enumerate(_rows(records_csv), 1):
        pid = (row.get("PtId#") or "").strip()
        emails = sorted(contacts.get(pid, ()))
        scan_date = _date(row.get("TodDat"))
        layers = []
        for i in range(1, 14):
            title = (row.get(f"head{i}") or "").strip()
            remedies = []
            for suffix in ("A", "B"):
                remedy = (row.get(f"remedy{i}{suffix}") or "").strip()
                if remedy and remedy.lower() not in {"remedy", f"remedy {i}"}:
                    remedies.append(remedy)
            if title or remedies:
                layers.append({"title": title, "remedy": ", ".join(dict.fromkeys(remedies))})
        if not layers:
            skipped["empty_report"] += 1
            continue
        if len(emails) != 1:
            skipped["unresolved_identity"] += 1
            continue
        if not scan_date:
            skipped["invalid_date"] += 1
            continue
        email = emails[0]
        key = (email, scan_date)
        item = grouped.setdefault(key, {"email": email, "name": names.get(email, ""),
                                        "scan_date": scan_date, "layers": [],
                                        "records_rows": [], "electronic_rows": 0})
        item["records_rows"].append(ordinal)
        item["layers"].extend(layers)

    electronic_by_key = defaultdict(int)
    for row in _rows(electronic_csv):
        pid = (row.get("PtId#") or "").strip()
        emails = sorted(contacts.get(pid, ()))
        scan_date = _date(row.get("Date"))
        if len(emails) == 1 and scan_date:
            electronic_by_key[(emails[0], scan_date)] += 1
    for key, count in electronic_by_key.items():
        if key in grouped:
            grouped[key]["electronic_rows"] = count

    reports = []
    for item in grouped.values():
        # Collapse exact duplicate layer/remedy pairs while preserving legacy order.
        seen, layers = set(), []
        for layer in item.pop("layers"):
            sig = (layer["title"].casefold(), layer["remedy"].casefold())
            if sig in seen:
                continue
            seen.add(sig)
            layers.append({"n": len(layers) + 1, "title": layer["title"], "meaning": "",
                           "remedy": layer["remedy"], "dosing": ""})
        item["content"] = {
            "greeting": "Your historical Healing Oasis biofield report.",
            "layers": layers,
            "video": {}, "reorder_items": [], "pricing_note": "",
            "legacy_source": {"databases": ["Records.fmp12", "Electronic.fmp12"],
                              "records_rows": item.pop("records_rows"),
                              "electronic_rows": item.pop("electronic_rows")},
        }
        reports.append(item)
    reports.sort(key=lambda x: (x["email"], x["scan_date"]))
    return {"reports": reports, "skipped": dict(skipped),
            "source": "healing_oasis_legacy_fmp"}


def ingest_payload(cx, payload, *, commit=False):
    """Insert missing historical reports and bare portals; never overwrite."""
    from dashboard import client_portal, portal_biofield_reports
    client_portal.init_client_portal_table(cx)
    portal_biofield_reports.init_table(cx)
    result = {"received": 0, "inserted": 0, "existing": 0, "portals_created": 0,
              "invalid": 0, "dry_run": not commit}
    for row in payload.get("reports") or []:
        result["received"] += 1
        email = (row.get("email") or "").strip().lower()
        scan_date = _date(row.get("scan_date"))
        content = row.get("content")
        if email.count("@") != 1 or not scan_date or not isinstance(content, dict):
            result["invalid"] += 1
            continue
        exists = cx.execute("SELECT 1 FROM portal_biofield_reports WHERE email=? AND scan_date=?",
                            (email, scan_date)).fetchone()
        if exists:
            result["existing"] += 1
            continue
        if commit:
            if not cx.execute("SELECT 1 FROM client_portals WHERE email=?", (email,)).fetchone():
                client_portal.upsert_portal(cx, email, row.get("name") or "", {})
                result["portals_created"] += 1
            portal_biofield_reports.upsert_report(
                cx, email, scan_date, f"legacy-fmp:{email}:{scan_date}", content, "confirmed")
        result["inserted"] += 1
    return result
