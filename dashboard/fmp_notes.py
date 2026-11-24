"""FileMaker client notes and Biofield test notes, for the console client page only.

Glen, 2026-11-23, in the platform tab: "console only". Only the owner role (Glen and Rae)
sees these notes. The client portal never sees them, and neither does a VA.

These notes live in their own table on purpose. `fmp_orders`' column lists remain the
privacy boundary for everything the portal can reach, and this module never widens
them. `notes_for_email` is the only reader, and only the console client-360 route calls it.

stdlib only, so it can be tested offline.
"""
import csv
import datetime

from dashboard.fmp_orders import _clients_where, _key

TABLE = "fmp_notes"
KINDS = ("client", "test")
MAX_NOTE = 200000   # longer is rejected and counted, never cut short (review round 2)


def ensure_table(cx):
    cx.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ("
               "kind TEXT NOT NULL, source_id TEXT NOT NULL, id_fk_client TEXT NOT NULL, "
               "note_date TEXT, notes TEXT NOT NULL, PRIMARY KEY (kind, source_id))")


def _rows(path):
    csv.field_size_limit(10 ** 9)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _date(v):
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def build_payload(clients_csv, tests_csv):
    """Only ids, a date and the note text leave the Mac. No other column is read."""
    notes = []
    for r in _rows(clients_csv):
        text = (r.get("notes") or "").strip()
        cid = _key(r.get("id_pk"))
        if text and cid:
            notes.append({"kind": "client", "source_id": cid, "id_fk_client": cid,
                          "note_date": "", "notes": text})
    for r in _rows(tests_csv):
        text = (r.get("notes") or "").strip()
        tid, cid = _key(r.get("id_pk")), _key(r.get("id_fk_client"))
        if text and tid and cid:
            notes.append({"kind": "test", "source_id": tid, "id_fk_client": cid,
                          "note_date": _date(r.get("date_test")), "notes": text})
    return {"notes": notes}


def _valid(n):
    return (isinstance(n, dict) and n.get("kind") in KINDS
            and all(isinstance(n.get(k), str) and n.get(k).strip()
                    for k in ("source_id", "id_fk_client", "notes"))
            and isinstance(n.get("note_date") or "", str)
            and len(n["notes"]) <= MAX_NOTE)


def ingest(cx, payload, *, commit=False, prune=False):
    """Merge notes by (kind, source_id). A dry run writes nothing and returns counts only.

    A note is changed when its text, its client or its date differs (review round 2).
    Duplicate keys in one payload collapse to the last one, and are counted. `absent`
    counts stored notes the payload does not carry, such as a note cleared in FileMaker.
    They are removed only with prune=True, which must be given a full export (round 1).

    The other counts say how many notes belong to a FileMaker client the app already
    holds, with and without an email on file. Only a client with an email can reach a
    console client page."""
    has_clients = _has_fmp_clients(cx)   # before ensure_table: a failed probe rolls back
    ensure_table(cx)
    raw = (payload or {}).get("notes") or []
    valid = [n for n in raw if _valid(n)]
    rejected = len(raw) - len(valid)
    incoming = {}
    for n in valid:
        incoming[(n["kind"], _key(n["source_id"]))] = {
            "kind": n["kind"], "source_id": _key(n["source_id"]),
            "id_fk_client": _key(n["id_fk_client"]), "note_date": n.get("note_date") or "",
            "notes": n["notes"].replace("\x00", "")}   # Postgres text rejects NUL (round 3)
    duplicates = len(valid) - len(incoming)
    have = {(k, s): (c, d or "", t) for k, s, c, d, t in cx.execute(
        f"SELECT kind, source_id, id_fk_client, note_date, notes FROM {TABLE}").fetchall()}
    emails = {_key(c): (e or "").strip() for c, e in cx.execute(
        "SELECT id_pk, email FROM fmp_clients").fetchall()} if has_clients else {}
    new, changed, same = [], [], 0
    for k, n in incoming.items():
        if k not in have:
            new.append(n)
        elif have[k] != (n["id_fk_client"], n["note_date"], n["notes"]):
            changed.append(n)
        else:
            same += 1
    absent = [k for k in have if k not in incoming]
    rows = list(incoming.values())
    known = [n for n in rows if n["id_fk_client"] in emails]
    result = {"dry_run": not commit, "received": len(valid), "rejected": rejected,
              "duplicates": duplicates,
              "by_kind": {kind: sum(1 for n in rows if n["kind"] == kind) for kind in KINDS},
              "new": len(new), "changed": len(changed), "unchanged": same,
              "absent": len(absent), "pruned": 0,
              "client_known": len(known),
              "client_has_email": sum(1 for n in known if emails[n["id_fk_client"]]),
              "clients_with_notes": len({n["id_fk_client"] for n in rows})}
    # Prune only from what is clearly a full export: nothing rejected, both kinds present,
    # and no more than a tenth of the stored notes missing (review round 3).
    refuse = ("rows were rejected" if rejected else
              "a kind of note is missing from the payload" if not all(result["by_kind"].values()) else
              "more than a tenth of stored notes are absent" if len(absent) > max(1, len(have) // 10)
              else "")
    if prune and refuse:
        result["prune_refused"] = refuse
    if commit:
        gone = list(absent) if prune and not refuse else []
        for kind, sid in [(n["kind"], n["source_id"]) for n in changed] + gone:
            cx.execute(f"DELETE FROM {TABLE} WHERE kind=? AND source_id=?", (kind, sid))
        if new or changed:
            cx.executemany(
                f"INSERT INTO {TABLE} (kind, source_id, id_fk_client, note_date, notes) "
                "VALUES (?,?,?,?,?)",
                [(n["kind"], n["source_id"], n["id_fk_client"], n["note_date"], n["notes"])
                 for n in new + changed])
        result["pruned"] = len(gone)
        cx.commit()
    return result


def _has_fmp_clients(cx):
    try:
        cx.execute("SELECT 1 FROM fmp_clients LIMIT 1").fetchall()
        return True
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return False


def _client_ids(cx, email):
    """FileMaker client ids on this email. A padded or mixed-case email still matches,
    and so does an email found later for a row that had none, the way order history
    finds it (dashboard/fmp_orders._clients_where, review round 1)."""
    ids = {_key(r[0]) for r in cx.execute(
        "SELECT id_pk FROM fmp_clients WHERE LOWER(TRIM(email)) = ?", (email,)).fetchall()}
    try:
        where, args = _clients_where(None, email, None, fills=True)
        ids |= {_key(r[0]) for r in cx.execute(
            f"SELECT id_pk FROM fmp_clients WHERE {where}", args).fetchall()}
    except Exception:
        try:
            cx.rollback()   # no client_email_fills table yet
        except Exception:
            pass
    return ids


def notes_for_email(cx, email):
    """Notes for every FileMaker client on this email, client notes first, then test
    notes newest first. Empty when the table or the email is absent."""
    email = (email or "").strip().lower()
    if not email or not _has_fmp_clients(cx):
        return []
    try:
        ids = sorted(_client_ids(cx, email))
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = cx.execute(
            f"SELECT kind, source_id, id_fk_client, note_date, notes FROM {TABLE} "
            f"WHERE id_fk_client IN ({ph})", ids).fetchall()
    except Exception:
        try:
            cx.rollback()
        except Exception:
            pass
        return []
    out = [{"kind": r[0], "id": r[1], "fmp_client": r[2], "date": r[3] or "", "text": r[4]}
           for r in rows]
    out.sort(key=lambda n: n["date"], reverse=True)
    out.sort(key=lambda n: n["kind"] != "client")   # stable: client notes first
    return out
