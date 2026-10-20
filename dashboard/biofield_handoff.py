"""Hand off to Rae — build a portal-seed from a client's AUTHORED causal chain,
in the composer's flat layer format, so the prod portal is built from what Glen
actually authored (never a stale reveal, never a nested-remedy "[object Object]").
This is the server-side automation of the seed-build that was done by hand for
Bobbi. Pushed to prod as a portal ai_draft; Rae publishes it from the console.
"""


def build_portal_seed(cx, test_id, resolve_slug, name=None, terrain_restore_ok=None,
                      name_of=None, ready=None, primary_on=None):
    """Return a portal-seed content dict {greeting, layers, reorder_items} from the
    authored chain. Layers are FLAT ({title, meaning, remedy (name string), dosing});
    reorder_items resolve each remedy to a catalog slug via resolve_slug(name)->slug|None.
    Pure read of biofield_auth_chain; never raises on a missing slug (that remedy just
    doesn't get a reorder row).

    `primary_on` is the set of sections switched on in the Biofield Analysis. A layer
    remedy whose section is off is left out of the layers and the reorder list, as it
    is left off the invoice (Glen, 2026-10-19). A layer whose remedies are all off
    keeps its title with no remedy. None hands off every layer remedy, as before."""
    tnum = int(str(test_id).lstrip("a") or 0)
    # Terrain reading (BSI phase P + spoken location) carried onto the portal report.
    try:
        trow = cx.execute("SELECT phase, location FROM biofield_auth_tests WHERE id=?",
                          (tnum,)).fetchone()
    except Exception:
        trow = None
    phase = trow[0] if trow else None
    location = (trow[1] if trow and trow[1] else "") or ""
    rows = cx.execute(
        "SELECT layer, head, most_affected, remedy, dosage, frequency, timing "
        "FROM biofield_auth_chain WHERE test_id=? AND TRIM(COALESCE(remedy,''))<>'' "
        "ORDER BY layer, sort_seq", (tnum,)).fetchall()
    if primary_on is not None:
        from dashboard.biofield_report_split import primary_rows
        rows = primary_rows(
            rows, primary_on, remedy_of=lambda r: r[3],
            group_of=lambda r: (("head", (r[1] or "").strip().lower())
                                if (r[1] or "").strip() else ("row", id(r))),
            blank=lambda r: (r[0], r[1], r[2], "", "", "", ""))
    layers, reorder, seen = [], [], set()
    for lay, head, _tail, remedy, dose, freq, timing in rows:
        dosing = " ".join(x for x in (dose, freq, timing) if (x or "").strip()).strip()
        layers.append({"n": lay, "title": head or "", "meaning": "",
                       "remedy": remedy, "dosing": dosing})
        if not (remedy or "").strip():
            continue                 # a layer whose remedies are all switched off
        try:
            slug = resolve_slug(remedy)
        except Exception:
            slug = None
        from dashboard.biofield_invoice import RECOMMEND_ONLY_SLUGS
        if slug in RECOMMEND_ONLY_SLUGS:
            slug = None              # on the report, never carted or invoiced (Glen 2026-10-01)
        if slug:
            reorder.append({"slug": slug, "name": remedy})
    # All "in Terrain Restore" items are one bottle of Terrain Restore (Glen,
    # 2026-10-19); then each product once.
    from dashboard.biofield_invoice import (CUSTOM_TERRAIN_RESTORE_NAME,
                                            CUSTOM_TERRAIN_RESTORE_SLUG, fold_terrain_restore)
    # The caller that holds the catalog says whether plain Terrain Restore can be
    # sold; without it, it must at least resolve.
    base_ok = terrain_restore_ok
    if base_ok is None:
        try:
            base_ok = resolve_slug(CUSTOM_TERRAIN_RESTORE_NAME) == CUSTOM_TERRAIN_RESTORE_SLUG
        except Exception:
            base_ok = False
    folded = []
    for item in fold_terrain_restore(reorder, base_ok, name_of=name_of, ready=ready):
        if item["slug"] not in seen:
            seen.add(item["slug"])
            folded.append(item)
    reorder = folded
    first = ((name or "").split() or ["there"])[0]
    return {
        "greeting": ("Aloha " + first + " \U0001F33A\n\nHere is your Biofield Analysis — the "
                     "patterns your scan revealed, and the remedies matched to support each one. "
                     "Take your time exploring it; I am just a reply away."),
        "phase": phase,
        "location": location,
        "layers": layers,
        "reorder_items": reorder,
    }


def report_remedies_for_invoice(db_path, rep, bottles_needed=None):
    """[{name, qty}] for each authored remedy for the invoice. qty is the line's own
    Bottles field, default 1 (Glen 2026-09-25). `db_path` and `bottles_needed` are
    kept for existing callers and no longer read."""
    from dashboard.biofield_invoice import line_bottles
    out = []
    for L in (rep.get("layers") or []):
        nm = (L.get("remedy") or "").strip()
        if nm:
            out.append({"name": nm, "qty": line_bottles(L)})
    return out
