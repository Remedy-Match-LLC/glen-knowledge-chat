"""History-to-finding links on the authoring page (spec 2026-10-01).

Reads the local e4l.db read-only and writes nothing. The vault's
02 Skills/history_links.py owns the tables. Any failure renders nothing, so the
authoring page never breaks on this panel. Glen's view only: no client sees it.
"""
import html
import os
import sqlite3

SOURCE_LABELS = {
    "people:conditions": "People record", "document": "uploaded report",
    "intake": "intake", "intake:narrative": "intake", "pb": "Practice Better",
    "scoreapp": "ScoreApp quiz", "ghl:terrain": "from GoHighLevel, origin unknown",
    "glen": "your tag", "ledger:intake": "intake tag", "chat": "from chat, unconfirmed"}


def _e(x):
    return html.escape(str(x or ""), quote=True)


def _group(cx, cid):
    """Same rule as e4l_synthesis.merge_group in the vault."""
    try:
        canon = {int(d): int(c) for d, c in cx.execute(
            "SELECT dup_client_id, canonical_client_id FROM e4l_identity_merges")}
    except sqlite3.Error:
        return {cid}
    c = canon.get(cid, cid)
    return {cid, c} | {d for d, can in canon.items() if can == c}


def render_panel(e4l_db_path, e4l_client_id, date_test):
    try:
        cid = int(str(e4l_client_id or "").strip())
    except ValueError:
        return ""
    if not os.path.exists(e4l_db_path):
        return ""
    try:
        with sqlite3.connect(f"file:{e4l_db_path}?mode=ro", uri=True) as cx:
            return _render(cx, cid, str(date_test or "").strip()[:10])
    except sqlite3.Error:
        return ""


def _people_on_address(cx, cid):
    """Distinct people (merge groups) on this client's address, as the vault counts
    them. 0 when the client has no e4l_clients row."""
    row = cx.execute("SELECT email FROM e4l_clients WHERE client_id=?", (cid,)).fetchone()
    if not row:
        return 0
    ids = [r[0] for r in cx.execute(
        "SELECT client_id FROM e4l_clients WHERE lower(trim(email))=lower(trim(?))",
        (row[0] or "",))]
    seen, n = set(), 0
    for i in ids:
        if i not in seen:
            seen |= _group(cx, i)
            n += 1
    return n


def _render(cx, cid, date_test):
    # One People record per address: with two people on it, history cannot be told apart.
    if _people_on_address(cx, cid) != 1:
        return ""
    group = sorted(_group(cx, cid))
    marks = ",".join("?" * len(group))
    q = (f"SELECT scan_id, scan_date FROM e4l_scans WHERE client_id IN ({marks})"
         + (" AND scan_date<=?" if date_test else "") + " ORDER BY scan_date DESC LIMIT 1")
    scan = cx.execute(q, group + ([date_test] if date_test else [])).fetchone()
    if not scan:
        return ""
    scan_id, scan_date = scan
    rows = cx.execute(
        "SELECT h.id, h.phrase, h.source, l.item_code, coalesce(i.full_name, i.name, l.item_code),"
        " f.reviewed, f.note, f.id FROM finding_history_links l"
        " JOIN client_history h ON h.id=l.history_id"
        " JOIN finding_conditions f ON f.id=l.finding_condition_id"
        " LEFT JOIN e4l_items i ON i.code=l.item_code"
        " WHERE l.scan_id=? AND f.removed=0 AND h.retired_at IS NULL"
        " ORDER BY h.phrase, l.item_code", (scan_id,)).fetchall()
    linked_ids = {r[0] for r in rows}
    active = cx.execute(
        f"SELECT id, phrase FROM client_history WHERE client_id IN ({marks})"
        " AND retired_at IS NULL AND kind='structured'", group).fetchall()
    unlinked = len({p for i, p in active if i not in linked_ids}
                   - {r[1] for r in rows})
    by_condition = {}
    for hid, phrase, source, code, name, reviewed, note, fid in rows:
        c = by_condition.setdefault(phrase, {"sources": set(), "findings": {}})
        c["sources"].add(SOURCE_LABELS.get(source, source))
        c["findings"].setdefault((code, fid), (name, reviewed, note))   # once per condition
    items = []
    for phrase, c in by_condition.items():
        finds = "".join(
            f"<li>{_e(name)}" + ("" if reviewed else " <span class=food>unreviewed</span>")
            + (f"<div class=food>{_e(note)}</div>" if note else "") + "</li>"
            for name, reviewed, note in c["findings"].values())
        items.append(f"<li><b>{_e(phrase)}</b> <span class=food>"
                     f"{_e(', '.join(sorted(c['sources'])))}</span><ul>{finds}</ul></li>")
    if not items and not unlinked:
        return ""
    noun = "condition" if unlinked == 1 else "conditions"
    return ("<section class=clinical-summary><div class=clinical-head>"
            f"<h3>History and findings, scan of {_e(scan_date)}</h3></div>"
            + (f"<ul>{''.join(items)}</ul>" if items else "<p class=food>No links on this scan.</p>")
            + f"<p class=food>{unlinked} reported {noun} no finding links to.</p></section>")
