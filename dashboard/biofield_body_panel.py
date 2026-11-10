"""The Body panel: the scan's recommended Infoceuticals, each with its Remedy Matches.

Glen, 2026-11-06 and 07, platform tab. The rows are the Infoceuticals the E4L report
recommends. Each row shows the Infoceutical and its top 3 Remedy Matches: the
Functional Formulations mapped to the Infoceutical's pattern in e4l.db
(pattern_glossary.pattern_remedies, the map the scan matcher reads). Glen picks one
per row; the Infoceutical is the default. For an animal the Infoceutical stays
chosen: an animal report recommends only infoceuticals (Glen, 2026-09-18).

Each match shows the stress patterns it shares with the Infoceutical: this scan's
codes the formulation is mapped to.

Read only. The rows the page shows are rebuilt when Glen clicks Add to plan, and
only a product they offer can be chosen.
"""
import sqlite3

INFOCEUTICAL = "infoceutical"
MATCHES = 3


def _ro(e4l_db):
    cx = sqlite3.connect(f"file:{e4l_db}?mode=ro", uri=True)  # raw-sqlite-ok: e4l.db, read only
    cx.row_factory = sqlite3.Row
    return cx


def panel_rows(e4l_db, scan_id, infoceutical_names, is_animal=False, order=None):
    """{"rows": [{code, label, infoceutical, matches: [{name, tier, shared:[code]}]}],
    "error": ""}. `infoceutical_names` is {code: remedy-list name}; `order` the
    recommended codes by the scan's rank (default: read from e4l.db)."""
    out = {"rows": [], "error": "", "animal": bool(is_animal)}
    if not e4l_db or not scan_id:
        return out
    if order is None:
        from dashboard.biofield_report_split import recommended_order
        order = recommended_order(e4l_db, scan_id)
    if not order:
        out["error"] = "The scan's recommended Infoceuticals are not stored."
        return out
    from dashboard.pattern_glossary import pattern_remedies
    key = int(scan_id) if str(scan_id).isdigit() else scan_id
    try:
        cx = _ro(e4l_db)
    except sqlite3.Error as exc:
        out["error"] = f"The scan database could not be read: {exc}"
        return out
    try:
        scan_codes = {r[0].upper() for r in cx.execute(
            "SELECT item_code FROM e4l_scan_results WHERE scan_id=? AND item_code IS NOT NULL",
            (key,))}
        names = {}
        try:
            names = {r[0].upper(): r[1] for r in cx.execute(
                "SELECT code,name FROM e4l_items WHERE code IN (%s)"
                % ",".join("?" * len(order)), list(order))}
        except sqlite3.Error:
            names = {}
        for code in list(dict.fromkeys(order)):
            matches = []
            # Shown to an animal too, greyed out: the Infoceutical stays chosen.
            for m in pattern_remedies(cx, code)[:MATCHES]:
                shared = [r[0].upper() for r in cx.execute(
                    "SELECT DISTINCT m.item_code FROM e4l_formulation_map m "
                    "JOIN formulations f ON f.id=m.formulation_id "
                    "WHERE lower(f.name)=lower(?) AND m.item_code IS NOT NULL",
                    (m["name"],)) if r[0] and r[0].upper() in scan_codes]
                shared = sorted(set(shared), key=lambda c: (c != code, c))
                matches.append({"name": m["name"], "tier": m.get("order_tier"),
                                "shared": shared})
            out["rows"].append({"code": code, "label": names.get(code) or code,
                                "infoceutical": (infoceutical_names or {}).get(code) or "",
                                "matches": matches})
    finally:
        cx.close()
    return out


def chosen_layers(panel, choices, is_animal=False):
    """The incoming layers for the products Glen chose, in row order, or raises
    ValueError for a choice the row does not offer. `choices` is {code: name}, where
    the name is "infoceutical" or one of that row's match names. A row with no
    choice takes its Infoceutical. An animal always takes the Infoceutical. A row
    whose chosen product has no name (an Infoceutical missing from the remedy list)
    is left out and counted in "unnamed"."""
    layers, unnamed = [], []
    for row in (panel or {}).get("rows") or []:
        pick = str((choices or {}).get(row["code"]) or INFOCEUTICAL).strip()
        if is_animal or pick == INFOCEUTICAL:
            name = row["infoceutical"]
        else:
            name = next((m["name"] for m in row["matches"] if m["name"] == pick), None)
            if name is None:
                raise ValueError(f"{pick} is not offered for {row['code']}. Reload the page.")
        if not name:
            unnamed.append(row["code"])
            continue
        layers.append({"title": row["label"], "most_affected": row["label"],
                       "remedy_name": name, "codes": [row["code"]], "laser": None,
                       "source": "body"})
    return layers, unnamed
