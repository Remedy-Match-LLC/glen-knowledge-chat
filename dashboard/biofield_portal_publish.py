"""Publish an authored Biofield Intake report to the illtowell.com client portal.

Pure / none-raising builder + an injectable prod POST. PHI stays local; only the
finished portal payload crosses to prod via the existing /admin/portal/upsert.
"""
import re
import secrets
import requests

from dashboard.practitioner_portal import name_to_slug
from dashboard import wholesale_pricing as _pricing
from dashboard.biofield_invoice import line_bottles
from dashboard.biofield_authoring import authored_report, remedy_dosing, merge_dosing
from dashboard.biofield_narrative import get_narrative

# Protocol wordings that differ from the catalog. Keyed by alphanumeric-only,
# lowercased remedy text so "Focus, Neuromagnesium" and "Focus Neuro-Magnesium"
# collapse to the same key.
ALIAS_SLUGS = {
    "focusneuromagnesium": "neuro-magnesium",
    "communityspiritformulainterrainrestore": "terrain-restore",
    # Cistus Syntropy Powder was replaced by Cistus Shield capsules (Glen, 2026-09-10).
    # `superseded_by` carries "Cistus" and "Cistus Synergy" forward on its own; these
    # three match nothing in the catalog, so only an alias reaches them. "cystusshield"
    # is Glen's own misspelling, used on 2026-09-10, so it will recur.
    "cistussyntropy": "cistus-shield",
    "cistussyntropypowder": "cistus-shield",
    "cystusshield": "cistus-shield",
}


def _norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_catalog():
    """The slug-keyed products map (data/products.json 'products')."""
    return _pricing._load_catalog()


def resolve_remedy_slug(name, catalog):
    """Resolve a protocol remedy name to a catalog slug: alias override first,
    then the in-repo fuzzy resolver. None when genuinely unresolvable.

    Every hit is routed through `superseded_slug`, because this result is STORED in
    `reorder_items` — persisted portal content, not a per-request lookup. The matchers
    below compare against `product["name"]` and never check `inactive`, so a retired
    record keeps matching its own name forever. Without the redirect, retiring a
    product silently starts writing dead slugs into every report published after it.
    Same rule, same reason as `test_superseded_write_boundary.py`."""
    if not (name or "").strip():
        return None
    return _live(_match(name, catalog), catalog)


def _live(slug, catalog):
    """A stored slug must name the survivor, never the record it replaced."""
    if not slug:
        return None
    from dashboard.products import superseded_slug
    return superseded_slug(slug, catalog or {})


def _match(name, catalog):
    alias = ALIAS_SLUGS.get(_norm_key(name))
    if alias:
        return alias
    wanted = (name or "").strip().lower()
    for slug, product in (catalog or {}).items():
        if (product.get("name") or "").strip().lower() == wanted:
            return slug
    return name_to_slug(name, catalog)


def _dosing(layer):
    parts = [(layer.get("dosage") or "").strip(),
             (layer.get("frequency") or "").strip(),
             (layer.get("timing") or "").strip()]
    return " ".join(p for p in parts if p)


def _cue_candidates(layer):
    """Ordered phrases to locate this layer in the narrative blob."""
    rem = (layer.get("remedy") or "").strip()
    out = []
    if rem:
        out.append(rem)
        first = rem.split(",")[0].strip()      # "Focus, Neuromagnesium" -> "Focus"
        if first and first != rem:
            out.append(first)
    head = (layer.get("head") or "").strip()
    if head:
        out.append(head)
    return out


# A layer's paragraph opens with its number: "1. ", "2) ", "**3.** ". Only a number at
# the start of a PARAGRAPH counts: a list inside a paragraph, or a closing "Next steps"
# list, is not a layer boundary (blind review, 2026-09-25).
_NUM_PREFIX = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?(\d{1,2})[.)](?:\*\*)?[ \t]+",
                         re.MULTILINE)
_PARA_BREAK = re.compile(r"\n[ \t]*\n\s*")


def _paragraph_starts(text):
    return [0] + [m.end() for m in _PARA_BREAK.finditer(text)]


def _has_cue(segment, layer):
    low = segment.lower()
    return any(c.lower() in low for c in _cue_candidates(layer))


def _numbered_segments(text, layers):
    """One segment per numbered paragraph, the number stripped (the card shows its own).
    Trusted only when the paragraph numbers run exactly 1..n and paragraph k names a cue
    of layer k. A numbered terrain paragraph, extra numbers, or a stray list otherwise
    shifted every card onto the wrong layer. None when not trusted."""
    n = len(layers)
    numbered = []
    for start in _paragraph_starts(text):
        m = _NUM_PREFIX.match(text, start)
        if m:
            numbered.append((int(m.group(1)), start, m.end()))
    if [k for k, _, _ in numbered] != list(range(1, n + 1)):
        return None
    segs = []
    for i, (_, _, body) in enumerate(numbered):
        stop = numbered[i + 1][1] if i + 1 < n else len(text)
        segs.append(text[body:stop].strip())
    if not all(_has_cue(seg, layer) for seg, layer in zip(segs, layers)):
        return None
    return segs


def segment_narrative(narrative, layers):
    """Split the single narrative blob into one segment per layer.

    By the writer's numbered paragraphs first. Cutting at each remedy's name started
    every portal card mid-paragraph and ended it with the next layer's opening clause
    (clinical, 2026-09-25, Peach Goddard). The remedy cues remain the fallback for a
    narrative without clean numbering. Returns a list aligned to ``layers``; ``[]``
    when it cannot align."""
    # "\r\n" never matches the blank-line search below (blind review, 2026-09-25).
    text = (narrative or "").replace("\r\n", "\n")
    if not text or not layers:
        return []
    numbered = _numbered_segments(text, layers)
    if numbered:
        return numbered
    low = text.lower()
    positions = []
    cursor = 0
    for layer in layers:
        found = -1
        for cue in _cue_candidates(layer):
            idx = low.find(cue.lower(), cursor)
            if idx != -1:
                found = idx
                break
        if found == -1:
            return []                          # a layer has no cue -> fall back
        positions.append(found)
        cursor = found + 1
    # Narratives written before the paragraphs were numbered (a2 to a32 on 2026-09-25)
    # still come apart at blank lines. Move each cut back to the start of the paragraph
    # its cue sits in, so a card never opens mid-sentence, as long as that stays after
    # the previous cut.
    prev = -1
    for i, pos in enumerate(positions):
        breaks = [m.end() for m in _PARA_BREAK.finditer(text, prev + 1, pos)]
        if breaks:
            positions[i] = breaks[-1]
        prev = positions[i]
    # positions are strictly increasing by construction (each search starts past
    # the previous hit). Slice between consecutive cue starts.
    segs = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        segs.append(_NUM_PREFIX.sub("", text[start:end].strip(), count=1))
    return segs


def build_portal_content(cx, test_id, *, special_price_cents, catalog=None,
                         audio_url=None, report_pdf_url=None, findings_provider=None):
    """Map an authored intake report to the portal content payload.

    Returns {email, name, scan_date, scan_id, content, unresolved}. Never raises
    on missing narrative (falls back to greeting=full narrative, blank meanings)."""
    cat = catalog if catalog is not None else load_catalog()
    rep = authored_report(cx, test_id)
    raw_layers = rep.get("layers") or []
    client = rep.get("client") or {}
    name = (client.get("name") or "").strip()
    first = name.split()[0] if name else ""

    # One portal layer per CAUSAL-CHAIN LAYER, not per remedy row. A layer can carry
    # several remedies (Glen, 2026-09-22: "one card per layer with all its remedies").
    # They share one card, with the remedies joined by " + ", the form the console
    # portal editor already reads (syncOrderFromLayers splits on "+"). Grouping is the
    # intake page's own group_layers, so the portal numbers layers as the page does.
    from dashboard.biofield_report_html import group_layers
    groups = group_layers(raw_layers)

    narrative = get_narrative(cx, test_id) or ""
    segs = segment_narrative(narrative, [g["rows"][0] for g in groups])
    if segs:
        greeting = f"Aloha {first}," if first else "Aloha,"
        meanings = segs
    else:
        greeting = narrative or (f"Aloha {first}," if first else "Aloha,")
        meanings = [""] * len(groups)

    layers, reorder, seen, unresolved = [], [], set(), []
    for i, g in enumerate(groups):
        names, dosings = [], []
        for L in g["rows"]:
            remedy = (L.get("remedy") or "").strip()
            if not remedy:
                continue
            # Standard-dosage fallback: fill any dose field the practitioner left blank
            # from the product catalog default (remedy_dosing -> fmp_snap_products), so
            # every unblurred recommendation carries its standard schedule. merge_dosing
            # fills per-field, so an authored value (a manual biofield test) always wins
            # over the standard.
            dose = merge_dosing(L.get("dosage"), L.get("frequency"), L.get("timing"),
                                remedy_dosing(cx, remedy))
            if remedy not in names:
                names.append(remedy)
                dosings.append((remedy, _dosing(dose)))
            slug = resolve_remedy_slug(remedy, cat)
            if slug is None:
                if remedy not in unresolved:
                    unresolved.append(remedy)
                continue
            if slug in seen:
                # One remedy on several layers is one product: keep the largest
                # count, as the invoice does (build_invoice_lines).
                for it in reorder:
                    if it["slug"] == slug:
                        it["qty"] = max(it["qty"], line_bottles(L))
                continue
            seen.add(slug)
            reorder.append({"slug": slug,
                            "qty": line_bottles(L),
                            "price_cents": int(special_price_cents)})
        if len(dosings) > 1:
            dosing = "; ".join(f"{n}: {d}" if d else n for n, d in dosings)
        else:
            dosing = dosings[0][1] if dosings else ""
        layers.append({
            "n": g["layer"],
            "title": (g.get("head") or "").strip(),
            "meaning": meanings[i] if i < len(meanings) else "",
            "remedy": " + ".join(names),
            "dosing": dosing,
        })

    # Bake the ASSIGNED stresses under each layer (from list_stresses' by_layer grouping)
    # so the portal can show, per layer, which stress patterns that layer addresses.
    # Each row carries its group's number AND its rid: without the rid, a stress placed
    # on a layer by hand (biofield_auth_layer_stress, keyed by row id) was never read.
    # Best-effort; a stress lookup failure must never break a publish.
    try:
        from dashboard import biofield_stress as _bstr
        _chain = [{"layer": g["layer"], "head": L.get("head"), "remedy": L.get("remedy"),
                   "rid": L.get("rid")}
                  for g in groups for L in g["rows"]]
        _sbl = {}
        for _grp in (_bstr.list_stresses(cx, test_id, _chain).get("by_layer") or []):
            _sbl[_grp.get("layer")] = [{"code": (s.get("code") or ""), "label": (s.get("label") or "")}
                                       for s in (_grp.get("stresses") or [])]
        for _cl in layers:
            _cl["stresses"] = _sbl.get(_cl["n"], [])
    except Exception:
        for _cl in layers:
            _cl.setdefault("stresses", [])

    # Bake the scan's findings (name + e4l_description) into the portal content so
    # the client-portal stress-pattern chips render. findings_for_scan_date reads the
    # local e4l.db and returns the findings for the EXACT scan_date being published
    # (scan_context would always return the latest scan). Injectable for tests; never
    # raises (portal must publish even when e4l.db is missing/unreadable). Trimmed to
    # the fields the portal uses. Empty when no scan matches that date.
    email = (client.get("email") or "").strip().lower()
    scan_date = rep.get("date") or ""
    findings = []
    if email and scan_date:
        try:
            _fp = findings_provider
            if _fp is None:
                from dashboard.biofield_e4l import findings_for_scan_date as _fp
            raw = _fp(email, scan_date) or []
            findings = [{"code": f.get("code", ""), "name": f.get("name", ""),
                         "description": f.get("description", ""), "rank": f.get("rank")}
                        for f in raw]
        except Exception:
            findings = []

    content = {
        "greeting": greeting,
        "video": {"url": "", "label": "Watch your message from Dr. Glen"},
        # Terrain reading from the scan's BSI (phase P + spoken location). Carried so
        # the portal report can show it at the top, mirroring the printed report.
        "phase": rep.get("phase"),
        "location": rep.get("location") or "",
        "layers": layers,
        "reorder_items": reorder,
        "pricing_note": "",
        "findings": findings,
        "biofield_status": "confirmed",
        # A comped intake has no payment by design, so the portal's paid gate needs
        # to be told, or it would blur exactly the reports Glen chose to give away.
        "comped_intake": comped_intake(cx, test_id),
        "client_id": str(client.get("client_id") or "").strip(),
        # Time-of-day remedy schedule (Breakfast/Lunch/Dinner/etc.), same source the
        # printed report uses (authored_report -> build_schedule). Forward-only:
        # existing portals must be re-published to gain it.
        "schedule": rep.get("schedule") or {},
    }
    if audio_url:
        content["audio"] = {"url": audio_url, "label": "Listen to your walkthrough"}
    if report_pdf_url:
        content["report_pdf"] = {"url": report_pdf_url}
    return {
        "email": email,
        "name": name,
        "scan_date": scan_date,
        "scan_id": "",
        "content": content,
        "unresolved": unresolved,
    }


def publish_to_portal(payload, *, base_url, console_key, send=False,
                      send_if_new=False, http_post=None):
    """POST the portal payload to the prod /admin/portal/upsert.

    send=True auto-emails the portal link on EVERY publish. This used to claim it
    "only emails when a NEW token is minted"; it never did -- the upsert resolves an
    existing client's stable link precisely so send=true can re-notify them when a
    new scan lands. Acting on that claim mails a client "your healing home is ready"
    every time the practitioner prints.

    send_if_new=True is the once-only form: the upsert emails only when it actually
    minted a token, so a longstanding client is never mailed as though they were new.

    Returns the parsed JSON (contains url/token). Raises RuntimeError on non-2xx."""
    post = http_post or requests.post
    url = f"{base_url.rstrip('/')}/admin/portal/upsert"
    body = {**payload, "send": bool(send)}
    if send_if_new:
        body["send_if_new"] = True
    r = post(url, json=body, headers={"X-Console-Key": console_key}, timeout=30)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"portal upsert failed {r.status_code}: {r.text[:300]}")
    return r.json()


def comped_intake(cx, test_id):
    """True when this test was run without charging for the analysis, so the portal
    gate can un-blur it: a comped intake has no payment by design."""
    from dashboard.biofield_authoring import get_no_charge
    try:
        return bool(get_no_charge(cx, test_id))
    except Exception:
        return False


def _asset_name(ext):
    """Return an opaque portal asset filename: biofield-<16 hex chars>.<ext>."""
    return f"biofield-{secrets.token_hex(8)}.{ext}"


def upload_asset(data_bytes, filename, *, base_url, console_key, http_put=None):
    """PUT raw bytes to the prod /portal-asset/upload; return the served url.
    Raises RuntimeError on non-2xx. http_put injectable (defaults requests.put)."""
    put = http_put or requests.put
    url = f"{base_url.rstrip('/')}/portal-asset/upload?filename={filename}"
    r = put(url, data=data_bytes, headers={"X-Console-Key": console_key}, timeout=60)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"asset upload failed {r.status_code}: {r.text[:300]}")
    return r.json()["url"]
