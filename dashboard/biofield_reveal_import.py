"""Import a client's E4L reveal (synthesized layers + remedies) into a local
Biofield Intake authoring test as needs-review causal-chain rows.

Runs the SAME synthesis pipeline as `02 Skills/e4l-reveal-push.py`, in-process on
Glen's Mac (PHI stays local). The vault pipeline is imported lazily through an
injectable `runner` so unit tests never touch the real e4l.db or the live matcher.
"""
import datetime
import os
import sqlite3

VAULT = os.path.expanduser("~/AI-Training")
SKILLS = os.path.join(VAULT, "02 Skills")
DEFAULT_E4L_DB = os.path.join(VAULT, "e4l.db")
DEFAULT_CATALOG = os.path.expanduser("~/deploy-chat/data/products.json")

# Glen, 2026-10-01: "For Rejuvenator layers, add the Harmony Laser as a 1st order remedy".
# ER2 (Large Intestine) and ER33 (Thyroid) alone get no laser; mixed with other ERs the laser
# carries his caution, approved 2026-10-01 ("perfect"). The store name, because the FileMaker
# name ("Harmony Soft Laser 172 Hz/5 Hz ...") does not resolve to a product on publish.
HARMONY_LASER = "Harmony Laser"
CAUTION_ERS = frozenset({"ER2", "ER33"})
HARMONY_CAUTION = ("Take care using infrared directly over the thyroid or the colon. "
                   "Cleansing reactions are more likely there, so start with the smallest dose "
                   "and increase only as you tolerate it.")


def harmony_laser_for(codes):
    """The Harmony Laser row for a layer's codes, or None. Humans and animals alike."""
    import re as _re
    ers = {c for c in (str(x).strip() for x in (codes or [])) if _re.fullmatch(r"ER\d+", c)}
    if not ers or not (ers - CAUTION_ERS):
        return None
    return {"name": HARMONY_LASER, "caution": HARMONY_CAUTION if ers & CAUTION_ERS else ""}


def _days_ago(scan_date, today):
    try:
        s = datetime.date.fromisoformat((scan_date or "").strip())
        t = datetime.date.fromisoformat((today or "").strip())
    except ValueError:
        return None
    return max(0, (t - s).days)


def _run_synthesis(email, scan_id, e4l_db, catalog, today):
    """Real pipeline: resolve the scan, synthesize, normalize to reveal layers.
    Returns ({scan_id, scan_date} | None, raw_layers). Mirrors e4l-reveal-push.py."""
    import sys
    if SKILLS not in sys.path:
        sys.path.insert(0, SKILLS)
    import e4l_synthesis as E  # noqa: E402
    from e4l_reveal_lib import build_payload  # noqa: E402
    cx = sqlite3.connect(e4l_db)
    try:
        if scan_id:
            row = cx.execute("SELECT scan_id, scan_date FROM e4l_scans WHERE scan_id=?",
                             (scan_id,)).fetchone()
            scan = {"scan_id": row[0], "scan_date": row[1]} if row else None
        else:
            scan = E.latest_scan(cx, email)
        if not scan:
            return None, []
        patterns = E.pull_patterns(cx, scan["scan_id"], limit=12)
        label_map = {p["item_code"]: (p.get("full_name") or p.get("name") or p["item_code"])
                     for p in patterns if p.get("item_code")}
        # FF-only enforcement (Glen 2026-07-14): restrict the pool to curated
        # Functional Formulations so the LLM pick, map fallback, and alternatives
        # can only resolve to an FF (mirrors 02 Skills/e4l-reveal-push.py).
        cat = E.ff_only_catalog(E.load_catalog(catalog))
        synth = E.synthesize(patterns, history="", rules=E.load_rules(),
                             ff_names=E.curated_ff_names(cat), layer_count=6)
        synth["layers"] = E.order_layers_by_pattern_count(synth.get("layers") or [])
        kw = dict(formulation_map=E.load_formulation_map(cx),
                  member_age=E.member_age_for_email(cx, email, today),
                  age_rules=E.load_age_rules(cx))
        # Glen chooses on the Intake page, so the import keeps second order remedies
        # (the layer candidates tag them). An older vault matcher has no tiers and no
        # apply_tiers argument; it already keeps everything.
        import inspect
        if "apply_tiers" in inspect.signature(E.to_portal_content).parameters:
            kw["apply_tiers"] = False
        content = E.to_portal_content(synth, cat, **kw)
        payload = build_payload(content, email, scan["scan_date"],
                                label_map=label_map, notify=False)
        return scan, ((payload or {}).get("layers") or [])
    finally:
        cx.close()


def synthesize_reveal_layers(email, scan_id=None, *, e4l_db=DEFAULT_E4L_DB,
                             catalog=DEFAULT_CATALOG, today, runner=None,
                             is_animal=False, infoceutical_names=None):
    """`is_animal` swaps the imported REMEDY only, never the layering.

    Glen, 2026-09-18: for an animal, "Import Reveal -> Causal Chain should pull in the
    recommended Infoceuticals, rather than FF's", using "the names you are currently
    using: they name functions", and "you don't need to change the layering calculations".

    So the FF-only synthesis and the layer ordering run exactly as before. For an animal,
    each layer's remedy becomes the function it already names -- its primary pattern label,
    which is the E4L infoceutical -- and the FF alternatives are dropped, because an animal
    report recommends only infoceuticals (the same rule the publish gate enforces in
    dashboard/analysis_autoconfirm.animal_formulation_reasons). A human is unchanged.

    Glen, 2026-10-01: the remedy is the remedy-list entry FOR THE CODE, never E4L's own label.
    E4L's labels differ from the product names in ways no text match survives ("Love
    Hologram" is "MB8 Love Infoceutical", "Lymph Star" is "ES1 Immune Energetic Star
    Infoceutical", "Heart – Lung Integrator" has an en dash), and only the exact name fills
    the dosing. A code with no remedy-list infoceutical is never a remedy: that keeps out the
    Rejuvenators ("a setting on the miHealth which most clients do not have"), MR, BFA,
    Environmental and Nutrition codes. They stay in most_affected as information. A layer
    with no such code imports with a blank remedy (no_remedy), never an invented name; the
    route reports the count. most_affected is unchanged: it prints on the client's report.
    `infoceutical_names` is {code: product name}; it defaults to the live catalog.
    """
    runner = runner or _run_synthesis
    scan, raw = runner(email, scan_id, e4l_db, catalog, today)
    if not scan or not raw:
        return {"found": False, "scan_id": None, "scan_date": None,
                "days_ago": None, "fresh": False, "layers": []}
    days = _days_ago(scan["scan_date"], today)
    if is_animal and infoceutical_names is None:
        from dashboard.animal_infoceuticals import infoceutical_by_code
        from dashboard.biofield_portal_publish import load_catalog
        infoceutical_names = infoceutical_by_code(load_catalog())
    layers = []
    for L in raw:
        rem = L.get("remedy") or {}
        name = (rem.get("name") or "").strip() if isinstance(rem, dict) else ""
        labels = [x for x in (L.get("pattern_labels") or []) if (x or "").strip()]
        affected = ", ".join(labels)
        if is_animal:
            # The first of this layer's codes that the remedy list carries, by its exact name.
            codes = [str(c).strip() for c in (L.get("patterns") or []) if str(c).strip()]
            from dashboard.biofield_authoring import is_infoceutical_code
            remedy_name = next((infoceutical_names[c] for c in codes
                                if is_infoceutical_code(c) and c in infoceutical_names), "")
            alternatives = []                 # no FF alternatives for an animal
        else:
            remedy_name = name
            alternatives = L.get("alternatives") or []
        layers.append({"n": L.get("n"),
                       "title": (L.get("title") or "").strip(),
                       "summary": (L.get("summary") or "").strip(),
                       "most_affected": affected,
                       "remedy_name": remedy_name,
                       "codes": list(L.get("patterns") or []),
                       "laser": harmony_laser_for(L.get("patterns") or []),
                       # Reported to Glen by the import route; never in a printed field.
                       "no_remedy": bool(is_animal and not remedy_name),
                       "alternatives": alternatives})
    return {"found": True, "scan_id": scan["scan_id"], "scan_date": scan["scan_date"],
            "days_ago": days, "fresh": days is not None and days < 7, "layers": layers}


def build_coverage(layers):
    """Map each remedy (lowercased) to the set of scan stress codes it covers,
    derived from the synthesized layers. Empty-remedy layers are skipped."""
    cov = {}
    for L in layers or []:
        name = (L.get("remedy_name") or "").strip().lower()
        if not name:
            continue
        cov.setdefault(name, set()).update(L.get("codes") or [])
    return cov


def import_layers_to_test(cx, tid, layers, after_layer=0):
    """Create one needs-review (confirmed=0) chain row per reveal layer. Dosing is
    auto-filled from the product catalog when the remedy name resolves. Returns the
    number of rows created.

    `after_layer` is the intake's highest stored layer. Appending onto an intake that
    already has layers numbers the reveal's layers after it; numbering them from 1
    again would interleave them with the layers already there."""
    from dashboard.biofield_authoring import add_chain_row, remedy_dosing
    n = 0
    for L in layers or []:
        name = (L.get("remedy_name") or "").strip()
        d = remedy_dosing(cx, name) if name else {"dosage": "", "frequency": "", "timing": ""}
        _n = L.get("n")
        add_chain_row(cx, tid, (int(_n) + int(after_layer or 0)) if _n is not None else None,
                      L.get("title") or "",
                      L.get("most_affected") or "", name,
                      dosage=d.get("dosage", ""), frequency=d.get("frequency", ""),
                      timing=d.get("timing", ""), confirmed=0, origin="scan",
                      codes=L.get("codes") or [])
        n += 1
        laser = L.get("laser")
        if laser:
            # An extra first-order remedy in the same layer, never in place of its remedy.
            ld = remedy_dosing(cx, laser["name"])
            timing = ld.get("timing", "")
            if laser.get("caution"):
                timing = (timing + " " if timing else "") + laser["caution"]
            add_chain_row(cx, tid, (int(_n) + int(after_layer or 0)) if _n is not None else None,
                          L.get("title") or "", L.get("most_affected") or "", laser["name"],
                          dosage=ld.get("dosage", ""), frequency=ld.get("frequency", ""),
                          timing=timing, confirmed=0, origin="scan", codes=L.get("codes") or [])
            n += 1
    return n
