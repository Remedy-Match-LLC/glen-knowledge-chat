"""Assemble a client's hand-off invoice from the authored Biofield intake and raise it
on prod (Orders board). Pure line-assembly + injected prod calls; mirrors
biofield_fee.py. Prod is the pricing authority — this module sends [{slug,qty}] only.

"Hand-off" is the workflow step where Glen hands an authored analysis to Rae to
publish and invoice. It says nothing about delivery: the invoice carries the
Biofield Analysis fee (a service, no bottle, so it books no box) plus the physical
remedy lines, which prod quotes for shipping. Whether the client collects in person
is a separate, per-client choice made on the console — never assumed here.
"""
import json as _json
import os
import urllib.parse
import urllib.request

from dashboard.ambiguous_product_names import is_ambiguous_product_name

BIOFIELD_SLUG = "biofield-analysis"

DEFAULT_INVOICE_NOTE = "Biofield Analysis and remedies. Payable by Credit Card or Zelle."


def terrain_note(phase, location=""):
    """One-line terrain-phase note for the order (BSI 'phase P' + spoken location).
    '' when no phase was read, so an unread scan adds nothing. No em dashes (house style)."""
    from dashboard.terrain_phase import phase_display
    disp = phase_display(phase)
    if not disp:
        return ""
    loc = (location or "").strip()
    return f"Terrain Phase: {disp}." if not loc else f"Terrain Phase: {disp}. Location: {loc}."


def build_invoice_note(phase=None, location=""):
    """The order's invoice_note: the terrain-phase line (when a phase was read) followed
    by the standard payment text; just the standard text when no phase was read."""
    tn = terrain_note(phase, location)
    return f"{tn} {DEFAULT_INVOICE_NOTE}" if tn else DEFAULT_INVOICE_NOTE


# Retired product names still found on old records, mapped to the product's slug. The
# Intake app builds invoices from a catalog that carries only slug, name and price, so an
# alias in products.json never reaches it: the old name has to live here (2026-09-28:
# clinical.db holds 248 visit_remedy rows named "Synergy C").
_RETIRED_NAMES = {
    "synergy c": "vitamin-c-syntropy",
    "phytolacca oil": "phytolacca-americana-oil-roll-on",   # one listing, 2026-09-29
    # Renamed "Aller-Free HomeoEnergetic Drops" 2026-09-25. The FMP snapshot, and so the
    # remedy picker and authored reports, still carry the old spelling.
    "allerfree homeoenergetic drops": "allerfree-homeoenergetic-drops",
    # Renamed "ES1 Lymph and Immune Energetic Star Infoceutical" 2026-10-02. Reports
    # authored before then carry the July name.
    "es1 lymph energetic star infoceutical": "es1-lymph",
    # The powder became "Vitamin B12 Sublingual Drops" 2026-09-24 (same FileMaker 323).
    # Reports authored before then still name the powder.
    "vitamin b12 sublingual powder": "sublingual-b12",
    "sublingual b12 powder": "sublingual-b12",   # the FMP snapshot name the picker offers
    # Six Synergy names became Syntropy 2026-09-30. clinical.db holds 137 visit_remedy
    # rows under the first five. Molybdenum is off sale, so its old name bills nothing.
    "magnesium synergy": "magnesium-syntropy",
    "vitamin a synergy": "vitamin-a-syntropy",
    "zinc synergy": "zinc-syntropy",
    "vitamin d synergy": "vitamin-d-syntropy",
    "vitamin d synergy powder": "vitamin-d-syntropy",
    "seacure synergy": "seaamino-syntropy",
    "molybdenum synergy": "molybdenum-syntropy",
}


def resolve_line_slug(name, catalog):
    """A remedy NAME -> a sellable catalog slug by EXACT (case-insensitive) match.
    No fuzzy matching: on an invoice a near-name substitution could bill the wrong
    SKU (ES1 vs ES13, Vitamin A vs Vitamin D). A non-exact name returns None and the
    caller lists it as skipped for manual add against the real catalog."""
    name = (name or "").strip().lower()
    if not name or is_ambiguous_product_name(name):   # "Seacure" is two products
        return None
    norm = " ".join(name.split())
    for it in catalog or []:
        if (it.get("name") or "").strip().lower() == name:
            return it.get("slug") or None
    # A retired name, never for a withdrawn product (round 2). The Intake app's catalog
    # carries no aliases, which is why the name lives here (round 3).
    old = _RETIRED_NAMES.get(name)
    if old and any(it.get("slug") == old and not it.get("inactive") for it in catalog or []):
        return old
    # A product's own old names, exact only, for products that opt in (report_aliases).
    # The infoceutical merge (2026-10-03) renamed 80 survivors; reports authored before
    # carry "BFA Big Field Aligner", "PL Polarity", "ED1 Source Driver".
    slug = _old_names().get(norm)
    if slug and any(it.get("slug") == slug and not it.get("inactive") for it in catalog or []):
        return slug
    return terrain_restore_fallback(
        name, (it.get("slug") for it in catalog or [] if not it.get("inactive")),
        terrain_restore_sellable(catalog))


def terrain_restore_fallback(name, slugs, custom_ok):
    """An "X in Terrain Restore" remedy with no product of its own is still made into
    the client's one bottle: Terrain Restore, which folds into the custom bottle and
    keeps its fee (Glen, 2026-10-19, Josie's "Digestzymes in Terrain Restore": "It is
    made into her Terrain Restore with the other ingredients, with no change in the
    fee."). None for any other name, when Terrain Restore is not in `slugs` (the
    live slugs), or when the custom bottle cannot be sold (`custom_ok` False): plain
    Terrain Restore would then bill the ingredients at its own price and drop their
    names, so they stay skipped for staff instead (review round 1, 2026-10-19)."""
    if not custom_ok or not terrain_restore_ingredient(name):
        return None
    return TERRAIN_RESTORE_SLUG if TERRAIN_RESTORE_SLUG in set(slugs) else None


def _old_names():
    """Old name -> slug for live products that opt in (`report_aliases: true`): their
    `aliases` and `pinecone_title`, exact and lowercased. A name two products answer to
    is dropped, never guessed. Infoceutical merge, 2026-10-03."""
    import json
    import re as _re
    try:
        from dashboard.biofield_authoring import _PRODUCTS_JSON
        with open(_PRODUCTS_JSON) as f:
            products = json.load(f).get("products") or {}
    except Exception:
        return {}
    out, seen = {}, set()
    for s, p in products.items():
        if not isinstance(p, dict) or p.get("inactive") or p.get("report_aliases") is not True:
            continue
        for v in list(p.get("aliases") or []) + [p.get("pinecone_title")]:
            k = " ".join(v.lower().split()) if isinstance(v, str) else ""
            # A bare code ("ES1", "MB 1", "BFA") never bills: it is one keystroke from
            # its neighbour (ES1 vs ES13), and a bare "BFA" stays for Rae.
            if not k or _re.fullmatch(r"(?:ed|ei|es|et|mb|mr|er|sk|pl|bfa) ?\d*", k):
                continue
            if k in out and out[k] != s:
                seen.add(k)
            out[k] = s
    return {k: v for k, v in out.items() if k not in seen}


def doses_per_day(freq_text):
    """A per-client frequency phrase -> doses/day, or None if unrecognized.
    Handles 'daily', 'a day', 'twice a day', 'two times a day', '3 times a day', '2x'."""
    import re
    t = (freq_text or "").strip().lower()
    if not t:
        return None
    m = re.search(r"(\d+)\s*(?:x|times?)\b", t)          # "3 times a day", "2x"
    if m:
        return int(m.group(1))
    words = {"once": 1, "one": 1, "twice": 2, "two": 2, "thrice": 3, "three": 3, "four": 4}
    for w, n in words.items():
        if re.search(rf"\b{w}\b", t) and re.search(r"\b(times?|x|a day|per day|daily|day)\b", t):
            return n
    if re.search(r"\b(daily|a day|per day|each day|every day)\b", t):
        return 1
    return None


def line_bottles(line):
    """Bottles to bill for one Biofield line: the line's own `bottles` field, else 1.

    Glen, 2026-09-25: "a single bottle gets entered on the invoice unless we specify
    otherwise, or change it on the invoice ... default would be 1 for most every
    product." It replaced a 30-day calculation (bottles_needed) that billed Peach
    Goddard 3 Candida Cleanse off a titrated six-a-day and Debra Herndon 3 IOP
    Syntropy. Anything blank, zero or unreadable is 1."""
    try:
        n = int(str((line or {}).get("bottles") or "").strip())
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


SUPPLY_MONTH_CHOICES = (1, 2, 3)


def _multiplies(slug, services):
    """True for a remedy line: never the Biofield, a service or a membership line."""
    slug = slug or ""
    return (slug != BIOFIELD_SLUG and slug not in services
            and not slug.startswith("membership:"))


def normalize_supply_months(value):
    """The 1 / 2 / 3 month supply choice (Glen, 2026-10-23). Anything else is 1."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return n if n in SUPPLY_MONTH_CHOICES else 1


def bottles_needed(freq_text, doses_per_bottle, program_days=30):
    """Bottles for the program = ceil(doses/day * days / doses_per_bottle), >= 1.
    Falls back to 1 when the frequency is unparseable OR doses_per_bottle is missing
    (e.g. infoceuticals carry no doses_per_bottle -> qty 1)."""
    import math
    try:
        dpb = int(doses_per_bottle)
    except (TypeError, ValueError):
        dpb = 0
    dpd = doses_per_day(freq_text)
    if not dpd or dpb <= 0:
        return 1
    return max(1, math.ceil(dpd * program_days / dpb))


# Recommended on a report, never billed or carted from it. Glen, 2026-10-01: the Harmony Laser
# that Import Reveal adds to Rejuvenator layers does not go on the invoice or the portal cart
# ("no"); the client buys it from its product page. It is a $997 device, and the portal applied
# the report's supplement special price to it (review round 1 found $50).
RECOMMEND_ONLY_SLUGS = frozenset({"harmony-laser"})


# Glen, 2026-10-19: "When more than one ingredient is listed "in Terrain Restore" in a
# layer or in different layers on the same Biofield, the invoice gets only one line
# with all those ingredients "in Terrain Restore"." One bottle at the normal Terrain
# Restore price ("yes"), named by a note under the line ("yes" to the wording).
TERRAIN_RESTORE_SLUG = "terrain-restore"
# Glen, 2026-10-19: a Terrain Restore with one or more added ingredients is its own
# product, "yes, always $70" (volume pricing off), that still counts toward the
# volume rate of the other formulations. He first said two or more, then: "a bottle
# of Terrain Restore with one or more added ingredient should be $70".
CUSTOM_TERRAIN_RESTORE_SLUG = "terrain-restore-custom"
CUSTOM_TERRAIN_RESTORE_NAME = "Custom Terrain Restore"
_IN_TR = " in terrain restore"


def terrain_restore_ingredient(name):
    """The ingredient of an "X in Terrain Restore" remedy, "" for plain Terrain
    Restore, None for anything else."""
    text = " ".join(str(name or "").split())
    low = text.lower()
    if low == "terrain restore":
        return ""
    if low.endswith(_IN_TR) and len(text) > len(_IN_TR):
        return text[:-len(_IN_TR)].strip()
    return None


def terrain_restore_note(ingredients):
    """"With A", "With A and B", "With A, B and C"."""
    names = [n for n in ingredients if n]
    if not names:
        return ""
    if len(names) == 1:
        return "With " + names[0]
    return "With " + ", ".join(names[:-1]) + " and " + names[-1]


def _qty(value):
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def fold_terrain_restore(items, base_ok, name_of=None, ready=None):
    """Every "in Terrain Restore" item (and plain Terrain Restore) as ONE item, at the
    place of the first, in the given (layer) order. Which item depends on how many
    different ingredients are added (Glen, 2026-10-19):

    - one or more: the custom bottle (CUSTOM_TERRAIN_RESTORE_SLUG), always its own
      catalog price ("a bottle of Terrain Restore with one or more added ingredient
      should be $70"), its ingredients named in "note" and listed in "held" as
      {name, slug, qty}: the authored name, so a count looked up by name is found,
      and the ingredient's own product, so the line can be unfolded again. When the
      custom product cannot be sold (`base_ok` False) nothing folds, so nothing is
      billed as a product that is not there;
    - none: plain Terrain Restore.

    `items` are dicts carrying the remedy "name" and a resolved "slug"; any "qty" is
    the largest among them. `name_of(slug)` gives the catalog name, so a remedy
    written without the suffix that still resolves to an "in Terrain Restore"
    product folds too. An item whose slug `ready(slug)` refuses stays its own item,
    so the caller's not-ready check still sees it.

    A price set on a line folded before ("_tr_price") or on the product the result
    is keeps that price. An ingredient's own override never prices a custom bottle."""
    rows = []
    for it in items or []:
        name = it.get("name") if isinstance(it, dict) else None
        if isinstance(it, dict) and terrain_restore_ingredient(name) is None and name_of \
                and it.get("slug"):
            name = name_of(it["slug"]) or name
        ingredient = terrain_restore_ingredient(name)
        foldable = (isinstance(it, dict) and ingredient is not None and bool(it.get("slug"))
                    and not (ready and not ready(it["slug"])))
        rows.append((it, name, ingredient, foldable))
    ingredients = []
    for _it, _nm, ing, ok in rows:
        if ok and ing and ing.lower() not in {i.lower() for i in ingredients}:
            ingredients.append(ing)
    custom = len(ingredients) >= 1
    if custom and not base_ok:
        rows = [(it, nm, ing, False) for it, nm, ing, _ok in rows]
    if custom:
        slug, label = CUSTOM_TERRAIN_RESTORE_SLUG, CUSTOM_TERRAIN_RESTORE_NAME
    else:
        slug, label = TERRAIN_RESTORE_SLUG, "Terrain Restore"
    out, folded, price, held = [], None, None, []
    for it, name, ingredient, ok in rows:
        if not ok:
            out.append({k: v for k, v in it.items() if k != "_tr_price"}
                       if isinstance(it, dict) else it)
            continue
        if folded is None:
            folded = {k: v for k, v in it.items() if k not in ("price_cents", "_tr_price", "note")}
            folded.update(slug=slug, name=label)
            out.append(folded)
        elif "qty" in it:
            folded["qty"] = max(_qty(folded.get("qty")), _qty(it.get("qty")))
        if price is None:
            if it.get("_tr_price") is not None:
                price = it["_tr_price"]
            elif it["slug"] == slug and it.get("price_cents") is not None:
                price = it["price_cents"]
        h = {"name": it.get("name") or name, "slug": it["slug"]}
        same = next((x for x in held if x["name"] == h["name"] and x["slug"] == h["slug"]), None)
        if same is None:
            if "qty" in it:
                h["qty"] = _qty(it.get("qty"))
            held.append(h)
        elif "qty" in it:
            same["qty"] = max(_qty(same.get("qty")), _qty(it.get("qty")))
    if folded is not None:
        if custom:
            folded["held"] = held
            folded["note"] = terrain_restore_note(ingredients)
        if price is not None:
            folded["price_cents"] = price
    return out


def _settle_bottle(items, bottles):
    """Put back the bottle count a folded line carried before, so a count Rae set
    survives a republish. An ingredient that joined since still raises it; the ones
    it already held do not. `bottles` is (that count, the (slug, name) it held)."""
    for it in items:
        if isinstance(it, dict) and it.get("slug") == CUSTOM_TERRAIN_RESTORE_SLUG and bottles:
            joined = [_qty(h.get("qty")) for h in it.get("held") or []
                      if isinstance(h, dict) and (h.get("slug"), h.get("name")) not in bottles[1]]
            it["qty"] = max([bottles[0]] + joined)
    return items


def held_names(item):
    """The remedy names a folded Terrain Restore line holds, or [its own name]."""
    held = [h.get("name") for h in (item.get("held") or []) if isinstance(h, dict)]
    return [n for n in held if n] or [item.get("name") or ""]


def held_slugs(item):
    """The ingredient products a folded Terrain Restore line holds."""
    return {(h.get("slug") or "").strip().lower() for h in (item.get("held") or [])
            if isinstance(h, dict) and h.get("slug")}


def fold_reorder_items(items, name_of, base_ok, ready=None):
    """A reorder list ({slug, qty, ...}) folded as the Biofield builders fold it, for
    a list that arrives already resolved: an editor's, a paste, or a sync from layers.

    A Terrain Restore line folded before is opened back into the ingredients it
    holds, each with its own product, then folded again. So a republish after
    Terrain Restore stops selling gives the old per-ingredient lines back, and an
    ingredient that stopped being ready comes out of the bottle. The result holds
    each slug once at its largest count."""
    cands, bottles = [], None
    for it in items or []:
        if not isinstance(it, dict) or not it.get("slug"):
            cands.append(it)
            continue
        base = {k: v for k, v in it.items() if k not in ("held", "names", "note", "name")}
        held = [h for h in (it.get("held") or [])
                if isinstance(h, dict) and h.get("slug") and h.get("name")]
        if not held and it["slug"] in FOLDED_SLUGS and it.get("note"):
            # A line folded by an older build: only its note says what it holds.
            held = [{"name": "%s in Terrain Restore" % n, "slug": TERRAIN_RESTORE_SLUG}
                    for n in note_ingredients(it.get("note"))]
        if held:
            price = base.pop("price_cents", None)
            if it["slug"] == TERRAIN_RESTORE_SLUG:
                # A plain Terrain Restore folded by an older build was priced under the
                # old rule (volume or courtesy), so the custom bottle it becomes starts
                # at $70 (review round 2, 2026-10-19). Rae can type a price again.
                price = None
            if bottles is None:
                bottles = (_qty(it.get("qty")), set())
            bottles = (max(bottles[0], _qty(it.get("qty"))),
                       bottles[1] | {(h["slug"], h["name"]) for h in held})
            for h in held:
                c = dict(base, slug=h["slug"], name=h["name"])
                if h.get("qty") is not None:
                    c["qty"] = _qty(h["qty"])     # its own count, never the bottle's
                if price is not None:
                    c["_tr_price"] = price
                cands.append(c)
        else:
            cands.append(dict(base, name=name_of(it["slug"]) or ""))
    out, by_slug = [], {}
    folded = fold_terrain_restore(cands, base_ok, name_of=name_of, ready=ready)
    for it in _settle_bottle(folded, bottles):
        if not isinstance(it, dict) or not it.get("slug"):
            out.append(it)
            continue
        it = {k: v for k, v in it.items() if k != "name"}
        if it["slug"] in by_slug:
            seen = by_slug[it["slug"]]
            seen["qty"] = max(_qty(seen.get("qty")), _qty(it.get("qty")))
            continue
        by_slug[it["slug"]] = it
        out.append(it)
    return out


# A folded line: the custom product, or plain Terrain Restore folded by #2074 before
# the custom product existed (its "held" list or note still says what it holds).
FOLDED_SLUGS = (CUSTOM_TERRAIN_RESTORE_SLUG, TERRAIN_RESTORE_SLUG)


def upgrade_old_folded(items):
    """Reorder items with a plain Terrain Restore line folded by an older build
    (it holds ingredients or names them in its note) read as the custom bottle,
    with no stored price: that price came from the old volume or courtesy rule.
    Reports published before the custom product existed then bill $70 without a
    republish (review round 1, 2026-10-19). Other items pass through unchanged."""
    out = []
    for it in items or []:
        if (isinstance(it, dict)
                and (it.get("slug") or "").strip().lower() == TERRAIN_RESTORE_SLUG
                and (it.get("held") or note_ingredients(it.get("note")))):
            it = {k: v for k, v in it.items() if k not in ("price_cents", "_tr_price")}
            it["slug"] = CUSTOM_TERRAIN_RESTORE_SLUG
        out.append(it)
    return out


def note_ingredients(note):
    """The ingredients a terrain_restore_note names, in order. Display text read
    back, so it is used only for a line that carries no "held" list."""
    text = str(note or "").strip()
    if not text.startswith("With "):
        return []
    text = text[5:]
    head, sep, last = text.rpartition(" and ")
    parts = (head.split(", ") + [last]) if sep else [text]
    return [p.strip() for p in parts if p.strip()]


def terrain_restore_sellable(catalog):
    """True when the catalog list holds the custom Terrain Restore, active and ready."""
    return any(it.get("slug") == CUSTOM_TERRAIN_RESTORE_SLUG and not it.get("inactive")
               for it in catalog or []) and not is_not_ready(CUSTOM_TERRAIN_RESTORE_SLUG, catalog)


def build_invoice_lines(client, remedies, catalog, include_fee=True, supply_months=1):
    """Biofield Analysis is lines[0] (unless include_fee is False — e.g. the client
    already PAID for the analysis, so we invoice remedies only); then one line per
    resolvable remedy (order preserved). A remedy is a name string (qty 1) or a
    {"name","qty"} dict (qty = bottles needed). Unresolvable names go to 'skipped',
    never mispriced.

    `supply_months` (1, 2 or 3; Glen 2026-10-23: "bottle count multiplies by the
    number of months") multiplies each remedy line's bottles. It is applied to the
    base count from the report, never to a count already on an order, so a repeated
    raise cannot compound it. The Biofield Analysis and any catalog product marked
    a service never multiply."""
    months = normalize_supply_months(supply_months)
    services = {c.get("slug") for c in catalog or []
                if isinstance(c, dict) and c.get("service")}
    lines = [{"slug": BIOFIELD_SLUG, "qty": 1}] if include_fee else []
    skipped, not_ready, resolved = [], [], []
    for r in remedies or []:
        if isinstance(r, dict):
            name, qty = (r.get("name") or "").strip(), r.get("qty")
        else:
            name, qty = (r or "").strip(), 1
        if not name:
            continue
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1
        slug = resolve_line_slug(name, catalog)
        if not slug:
            skipped.append(name)
            continue
        if slug in RECOMMEND_ONLY_SLUGS:
            continue
        # A not-ready product (waitlist_only, or a bundle holding one) stays on the
        # report as a name with its page link. It adds nothing to the invoice.
        if is_not_ready(slug, catalog):
            if name not in not_ready:
                not_ready.append(name)
            continue
        # One remedy can serve several layers — Steve Fox's 15 September report put
        # Neuroprotect on three. That is still one product taken once, so it gets one
        # line carrying the LARGEST bottle count, never one line per layer and never
        # the counts added together. Without this the raise duplicated the line, and
        # a second raise duplicated it again.
        resolved.append({"slug": slug, "qty": qty, "name": name})
    # All "in Terrain Restore" items are one bottle of Terrain Restore (Glen, 2026-10-19).
    names = {c.get("slug"): c.get("name") for c in catalog or [] if isinstance(c, dict)}
    for item in fold_terrain_restore(resolved, terrain_restore_sellable(catalog),
                                     name_of=names.get):
        slug, qty = item["slug"], item["qty"]
        seen = next((l for l in lines if l["slug"] == slug), None)
        if seen is not None:
            if _multiplies(slug, services):
                qty = qty * months
            seen["qty"] = max(seen["qty"], qty)
            continue
        if _multiplies(slug, services):
            qty = qty * months
        # These remedies came from the practitioner's authored Biofield
        # analysis, so preserve that provenance through order creation and
        # into Edit Invoice instead of falling back to source='self'.
        line = {"slug": slug, "qty": qty, "source": "biofield"}
        if item.get("note"):
            line["note"] = item["note"]
        lines.append(line)
    return {"lines": lines, "skipped": skipped, "not_ready": not_ready}


def is_not_ready(slug, catalog):
    """True when the console catalog marks this slug waitlist_only."""
    return any(it.get("slug") == slug and it.get("waitlist_only") for it in catalog or [])


def merge_manual_invoice_lines(new_lines, existing_items):
    """Preserve invoice-editor additions while refreshing Biofield schedule lines."""
    merged = [dict(line) for line in (new_lines or [])]
    present = {(line.get("slug") or "").strip() for line in merged}
    for item in existing_items or []:
        slug = (item.get("slug") or "").strip()
        if (not slug or slug == BIOFIELD_SLUG or
                (item.get("source") or "").strip().lower() == "biofield" or
                slug in present):
            continue
        line = {"slug": slug, "qty": max(1, int(item.get("qty") or 1))}
        for key in ("source", "note", "format"):
            if item.get(key):
                line[key] = item[key]
        if item.get("override") and item.get("unit_cents") is not None:
            line["unit_cents"] = item["unit_cents"]
        merged.append(line)
        present.add(slug)
    return merged


def _console():
    key = os.environ.get("CONSOLE_SECRET")
    if not key:
        return None, None
    base = os.environ.get("PUBLIC_BASE_URL", "https://illtowell.com").rstrip("/")
    return base, key


def default_fetch_catalog():
    base, key = _console()
    if not base:
        return []
    try:
        url = f"{base}/api/console/biofield-portal/catalog"
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp.get("products") or []
    except Exception:
        return []


def default_create_order(customer, lines, replace_open=False, invoice_note=None,
                         update_order_id=None, idempotency_key="", supply_months=None):
    """Create the hand-off invoice on prod. With replace_open=True (a re-hand-off),
    prod first cancels the client's prior OPEN hand-off drafts (proposed, unpaid, not
    yet published) so a repeated hand-off UPDATES rather than piling up duplicates;
    published/paid orders are never touched."""
    base, key = _console()
    if not base:
        return {"ok": False, "error": "The console connection is not configured."}
    try:
        # No `pickup` flag: a hand-off is not a pickup. Shipping is computed from the
        # physical remedy lines; the analysis fee is a service and contributes no
        # bottle, so an analysis-only invoice still ships $0. Mark the order pickup
        # from the console when the client actually collects in person.
        body = {"customer": {"name": customer.get("name") or "", "email": customer.get("email") or ""},
                "lines": lines, "replace_open": bool(replace_open),
                "invoice_note": invoice_note or DEFAULT_INVOICE_NOTE}
        if update_order_id:
            body["update_order_id"] = int(update_order_id)
        # The authoring page mints one token per LOAD. Prod derives the order's
        # external_ref from it, and orders is UNIQUE(source, external_ref), so a second
        # click carrying the same token gets the first order back instead of raising a
        # second. Absent or blank, prod keeps its old random reference per request.
        if str(idempotency_key or "").strip():
            body["idempotency_key"] = str(idempotency_key).strip()
        # Rule C: the months of remedies this invoice supplies, stored on the order
        # so the paid hook can size the membership. Absent = 1 on the server.
        if supply_months is not None:
            body["supply_months"] = normalize_supply_months(supply_months)
        url = f"{base}/api/orders/manual"
        req = urllib.request.Request(url, data=_json.dumps(body).encode(), method="POST",
                                     headers={"X-Console-Key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = _json.loads(r.read().decode() or "{}")
        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error") or "Order creation failed."}
        totals = resp.get("totals") or {}
        accepted = [ (l or {}).get("slug") for l in (resp.get("lines") or []) if (l or {}).get("slug") ]
        return {"ok": True, "order_id": resp.get("order_id"),
                "external_ref": resp.get("external_ref"),
                "total_cents": totals.get("total_cents"),
                "cancelled": resp.get("cancelled") or [],
                "accepted_slugs": accepted, "error": None}
    except Exception:
        return {"ok": False, "error": "Couldn't reach the console to create the order."}


def default_caregiver_billing(email, caregiver_email=None, on=None):
    """Bill with caregiver: who may be billed for this client, and who is remembered.
    With caregiver_email and on, sets the preference first. Returns {} on failure."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {}
    try:
        if caregiver_email is not None and on is not None:
            req = urllib.request.Request(
                f"{base}/api/console/caregiver-billing", method="POST",
                data=_json.dumps({"email": email, "caregiver_email": caregiver_email,
                                  "on": bool(on)}).encode(),
                headers={"X-Console-Key": key, "Content-Type": "application/json"})
        else:
            req = urllib.request.Request(
                f"{base}/api/console/caregiver-billing?email=" + urllib.parse.quote(email),
                headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read().decode() or "{}")
    except Exception:
        return {}


def default_invoice_link(order_id):
    base, key = _console()
    if not base or not order_id:
        return {"ok": False, "error": "link unavailable"}
    try:
        url = f"{base}/api/console/order/{int(order_id)}/invoice-link"
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = _json.loads(r.read().decode() or "{}")
        if resp.get("ok") and resp.get("link"):
            return {"ok": True, "print_url": resp["link"], "error": None}
        return {"ok": False, "error": "link unavailable"}
    except Exception:
        return {"ok": False, "error": "link unavailable"}


def default_orders_link(order_id):
    """The prod Orders-board deep link for an order (Edit action), key-carried the
    same way the local console tools bounce to prod. '' when unconfigured."""
    base, key = _console()
    if not base or not order_id:
        return ""
    return f"{base}/console/orders?order={int(order_id)}&key={urllib.parse.quote(key)}"


def default_edit_order_link(order_id):
    """Direct Edit Invoice URL for a known order."""
    base, key = _console()
    if not base or not order_id:
        return ""
    return (f"{base}/orders/new?edit_order={int(order_id)}&key="
            f"{urllib.parse.quote(key)}")


def default_client_orders(email, limit=300):
    """This client's orders from the console board, for the dispensed-before panel.

    Same read the invoice lookup already uses. Returns [] on any failure: the
    panel is a reference, and a reference that breaks the page it sits on is
    worse than one that is empty.
    """
    rows, err = client_orders_with_status(email, limit)
    # A list that also remembers WHY it is empty.
    #
    # client_orders is an injected seam on create_app, and the author route's tests
    # assert it is called. Returning a tuple, or reaching past the seam to the concrete
    # function, breaks both the injection and those tests. A list subclass keeps the
    # existing contract exactly -- every current caller treats it as a list and a test
    # may still inject a plain one -- while letting the panel ask `getattr(rows,
    # "error", None)` and tell a failed lookup from an empty history.
    out = _OrdersResult(rows)
    out.error = err
    return out


class _OrdersResult(list):
    """A list of orders carrying the reason it may be empty. See default_client_orders."""
    error = None


def client_orders_with_status(email, limit=300):
    """(orders, error). The error is what makes an empty panel honest.

    Glen, 2026-09-18: "I'm still not seeing how many bottles of each product have been
    previously purchased", and "yes, make it show history unavailable".

    The panel had been dead for at least a day and looked exactly like a client with no
    purchases. The local app reads CONSOLE_SECRET at startup, its process predated the key
    rotation, and every lookup came back 401. Returning [] on failure meant the page said
    "No order history for this client yet" about a client who had plenty.

    So failure and emptiness are now different answers. A caller that cannot use the error
    still gets the old list-only contract from default_client_orders.
    """
    base, key = _console()
    if not base:
        return [], "no console key configured on this machine"
    if not (email or "").strip():
        return [], None                       # genuinely nothing to ask about
    try:
        # Ask for THIS client's orders, the board's own client search. The newest N
        # orders shop-wide (the old read) drops an older client's store history once the
        # shop passes N.
        # The key goes in the header only: a key in the URL lands in server logs.
        url = (f"{base}/api/orders?limit={int(limit)}&q="
               + urllib.parse.quote(email.strip().lower()))
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read().decode() or "{}").get("data") or [], None
    except Exception as e:
        print(f"[dispensed] order lookup skipped: {e!r}", flush=True)
        code = getattr(e, "code", None)
        if code == 401:
            # Name the actual remedy. A stale key is the failure this panel has actually
            # had, and "unauthorized" alone sends the reader to the wrong place.
            why = ("the console key this app started with is no longer valid: "
                   "restart the local server to pick up the current one")
        elif code == 502:
            why = "the server was restarting (502); try again in a moment"
        elif isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            why = "the order lookup timed out"
        else:
            why = f"the order lookup failed ({type(e).__name__})"
        return [], why


def default_latest_invoice(email):
    """Latest Biofield-authored invoice for this client, including remedy-only
    invoices raised after the analysis fee was already paid."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"ok": False, "error": "invoice unavailable"}
    try:
        # This client's orders, newest first, key in the header only. See
        # client_orders_with_status.
        url = f"{base}/api/orders?limit=300&q=" + urllib.parse.quote(email.strip().lower())
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = (_json.loads(r.read().decode() or "{}").get("data") or [])
        target = email.strip().lower()
        for order in rows:
            if (order.get("email") or "").strip().lower() != target:
                continue
            items = order.get("items") or []
            if any((it.get("slug") == BIOFIELD_SLUG or it.get("source") == "biofield")
                   for it in items):
                return {"ok": True, "order_id": order.get("id"),
                        "items": items, "status": order.get("status"),
                        "pay_status": order.get("pay_status"),
                        "portal_published": bool(order.get("portal_published"))}
        return {"ok": False, "error": "No Biofield invoice found for this client."}
    except Exception:
        return {"ok": False, "error": "Couldn't reach the console to find the invoice."}


def default_publish_invoice(order_id):
    """POST the prod publish-to-portal endpoint for an order, so it shows as a pay
    card on the client's portal. Returns {ok, link} or {ok:False, error}."""
    base, key = _console()
    if not base or not order_id:
        return {"ok": False, "error": "publish unavailable (no console config)"}
    try:
        url = f"{base}/api/console/order/{int(order_id)}/publish-to-portal"
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"ok": False, "error": "bad response"}
    except Exception:
        return {"ok": False, "error": "publish failed"}


def default_biofield_paid(email):
    """Has this client already PAID for a Biofield Analysis? Asks prod for a paid,
    non-cancelled order carrying the biofield-analysis line. Returns {paid, order_id,
    paid_at} (paid False when none / unreachable) so the raise can drop the fee line."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"paid": False}
    try:
        url = (f"{base}/api/console/biofield-analysis-paid?email="
               + urllib.parse.quote(email) + "&key=" + urllib.parse.quote(key))
        req = urllib.request.Request(url, headers={"X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"paid": False}
    except Exception:
        return {"paid": False}


def default_handoff_push(email, name, content, scan_date=""):
    """Hand off to Rae: push a portal-seed `content` (built from the authored chain)
    to prod as a portal ai_draft (staged for Rae to review + publish from the console).
    Reuses /admin/portal/upsert. Returns {ok, ...} or {ok:False, error}."""
    base, key = _console()
    if not base or not (email or "").strip():
        return {"ok": False, "error": "handoff unavailable (no console config / email)"}
    payload = dict(content or {})
    payload["biofield_status"] = "ai_draft"
    # Stamp this hand-off's report as the client's CURRENT one so it wins over a stale
    # AI reveal (which owns its own per-scan report row) regardless of scan date.
    if (scan_date or "").strip():
        payload["current_scan_date"] = scan_date.strip()
    # A hand-off is Glen's handwritten Biofield, staged as ai_draft for Rae. Its status
    # says draft; report_kind says what it is, so the portal never calls it AI.
    body = _json.dumps({"email": email, "name": name or "", "content": payload,
                        "scan_date": scan_date or "", "scan_id": "",
                        "report_kind": "biofield"}).encode("utf-8")
    try:
        req = urllib.request.Request(f"{base}/admin/portal/upsert", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Console-Key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = _json.loads(r.read().decode() or "{}")
        return resp if isinstance(resp, dict) else {"ok": True}
    except Exception:
        return {"ok": False, "error": "handoff push failed"}
