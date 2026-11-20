"""Portal concierge context + prompt assembly. Pure / Flask-free / unit-testable.
Grounds the ongoing-concierge chat in the client's biofield findings + owned remedies."""

def build_context(content, orders, scan=None, product_name=None, directions_for=None):
    """The client's own data for the portal chat.

    `scan` is app._scan_recommendations_for(email): the E4L scan match the portal card
    shows. `product_name(slug)` names an order line that carries only a slug or code
    (the tour seed's orders did, 2026-11-19). `directions_for(name_or_slug)` returns a
    remedy's approved Directions, or "" when Glen has not approved any for the bot (#2020).
    All three are optional, so a caller that passes none gets the old context."""
    content = content or {}
    layers = [l for l in (content.get("layers") or []) if isinstance(l, dict)]
    findings = [f for f in (content.get("findings") or []) if isinstance(f, dict)]
    owned = []
    for o in (orders or []):
        for it in (o.get("items") or []):
            if not isinstance(it, dict):
                continue
            nm = (it.get("name") or "").strip()
            if not nm and product_name:
                key = (it.get("slug") or it.get("code") or "").strip()
                try:
                    nm = ((product_name(key) if key else "") or "").strip()
                except Exception:
                    nm = ""
            if nm and nm not in owned:
                owned.append(nm)
    scan_match = []
    rows = [r for r in ((scan or {}).get("infoceuticals") or [])
            if isinstance(r, dict) and (r.get("label") or r.get("code"))]
    rows.sort(key=lambda r: (not isinstance(r.get("rank"), int),
                             r.get("rank") if isinstance(r.get("rank"), int) else 0))
    seen = set()
    for r in rows:
        # One E4L code can appear at two ranks in one scan: keep its best (round 1).
        key = str(r.get("code") or r.get("label")).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        scan_match.append({"label": str(r.get("label") or r.get("code")),
                           "rank": r.get("rank"), "protocol_days": r.get("protocol_days"),
                           "slug": r.get("slug") or ""})
    scan_match = scan_match[:12]
    mihealth = [str(r.get("label") or r.get("code")) for r in ((scan or {}).get("mihealth") or [])
                if isinstance(r, dict) and (r.get("label") or r.get("code"))][:8]
    directions = {}
    if directions_for:
        names = [r["slug"] or r["label"] for r in scan_match] + owned
        names += [l.get("remedy") for l in layers if l.get("remedy")]
        for key in names:
            try:
                d = directions_for(key) or ""
            except Exception:
                d = ""
            if isinstance(d, dict):
                d = {"directions": str(d.get("directions") or "").strip(),
                     "caution": str(d.get("caution") or "").strip()}
                if not d["directions"]:
                    d = ""
            else:
                d = str(d).strip()
            if d:
                label = next((r["label"] for r in scan_match if (r["slug"] or r["label"]) == key), key)
                directions.setdefault(label, d)
    return {"layers": layers, "findings": findings, "owned": owned,
            "scan_match": scan_match, "scan_date": (scan or {}).get("scan_date") or "",
            "directions": directions, "mihealth": mihealth,
            "has_data": bool(layers or findings or owned or scan_match or mihealth)}

# Glen, 2026-10-08: the live chat answered "What is AngioGenX?" with a mechanism, a reason
# for a caution, a dose rationale and labels that no retrieved source gave. An invented
# mechanism turns "supports" into a treatment claim in his voice. Shared by app.py's main
# chat prompt (_SYSTEM_BASE) and the three client-facing prompts, each placing it right after
# ROLE_AND_CLAIMS. Kept out of ROLE_AND_CLAIMS itself, which is Glen's approved wording.
SOURCED_PRODUCT_CLAIMS = (
    "- PRODUCT CLAIMS COME ONLY FROM SOURCES: state a product's purpose, ingredients, dose and "
    "cautions only as the retrieved snippets or product data give them. Do not explain how a "
    "product works, why its dose is set, or why a caution exists, and add no labels such as "
    "'clinically active' or 'adaptogen', unless a retrieved source says so. This holds even "
    "where a format asks for mechanism or rationale. Describe what a formula supports, never "
    "what it treats or prevents.\n"
)

# The short form, for per-turn instructions and format lines that ask for mechanism or
# rationale. The system rule above says the same at length.
SOURCED_PRODUCT_NOTE = (
    "For a named product, give its mechanism, dose reasons, caution reasons and labels only "
    "as a retrieved source states them."
)

# Glen, 2026-10-02: how the AI states his role and what it may claim. Shared by the three
# client-facing prompts (this one, and app.py's Remedy Match guide and post-purchase concierge).
# State the role he holds, never a disqualification.
ROLE_AND_CLAIMS = (
    "- Describe what a remedy supports, never a diagnosis or a cure. If asked about Dr. Glen's "
    "licence or practice, say he is retired from licensed practice and now formulates "
    "remedies, consults, and teaches.\n"
    "- Present a scan finding as what the scan reported, never as a diagnosis.\n"
)

# Glen, 2026-11-12: a client asked the portal chat where her invoices were and got an
# unclickable, dead "truly.vip/help". #orders opens Orders & Invoices with the newest
# unpaid invoice first and survives sign-in (dashboard/portal_return.py).
PORTAL_ORDERS_URL = "https://myhealingoasis.com/portal/me#orders"

_BASE = (
    "You are Dr. Glen Swartwout's warm, ongoing health concierge (naturopathic optometrist, "
    "Hilo Hawai'i) inside this client's private portal. They are a known client; help them "
    "with their scan findings, their remedies and protocol (what to take when), reorders, and "
    "well-matched complements. Calm, consultative, never pushy: they are served and in control.\n"
    + ROLE_AND_CLAIMS + SOURCED_PRODUCT_CLAIMS +
    "- Ground every answer in THEIR data below; reference their actual findings/remedies by name.\n"
    "- Ask ONE gentle question at a time when you need more. Functional Formulations first.\n"
    "- For protocol, reorder-timing, dosing, and lifestyle questions, guide them to the answer "
    "with a gentle Socratic question from THEIR own data (their last order, their protocol, "
    "their findings) the way Dr. Glen would, before giving a flat answer.\n"
    "- Only when it truly needs Dr. Glen personally (a new or worsening symptom, a consultation "
    "or custom Terrain Restore, a complaint, or something you cannot answer) tell them you'll "
    "have Dr. Glen look at it personally; otherwise handle it yourself.\n"
    "- When it fits, suggest ONE complementary remedy at a time with a short plain reason.\n"
    "- Invoices, bills, receipts and paying: their Orders & Invoices page lists every invoice, "
    "newest unpaid first. Never send them anywhere else to find or pay an invoice. Give "
    "them this link, written in full on its own line: " + PORTAL_ORDERS_URL + "\n"
    "- Write every link in full, starting with https://. Never give truly.vip/help: that "
    "address no longer works. When they need Dr. Glen personally, follow the rule above.\n"
    "- Keep replies short and warm. Do not invent prices or URLs. NEVER use an em dash "
    "(the -- character or the Unicode em dash —); use a comma or period instead. "
    "No ALL CAPS. Never prefix anything with 'Hook:'. Sign off as Dr. Glen only when concluding."
)

# Glen, #2020 (2026-10-15) and 2026-10-31: approved Directions are the only dose, and every
# dose line ends "or as guided". The portal chat had no Directions at all, so a dose could
# only come from old knowledge-base snippets (2026-11-19).
DOSE_RULE = (
    "Dose rule: give an amount or timing for a remedy only from its approved directions "
    "listed here, in their words, and end it with \", or as guided\" unless they already end with \"as guided\". These directions outrank any dose in a retrieved snippet. A remedy with no approved "
    "directions listed here gets no amount from you, even if a snippet gives one: say to "
    "follow the directions on its label, or that Dr. Glen will guide them."
)


def system_prompt(ctx):
    ctx = ctx or {}
    parts = [_BASE, "\n\nTHIS CLIENT'S DATA:"]
    if ctx.get("owned"):
        parts.append("Remedies they already own: " + ", ".join(ctx["owned"]) + ".")
    fnd = [f.get("name") or f.get("code") for f in (ctx.get("findings") or []) if (f.get("name") or f.get("code"))]
    if fnd:
        parts.append("Scan findings: " + ", ".join(str(x) for x in fnd) + ".")
    for l in (ctx.get("layers") or []):
        seg = f"Layer {l.get('n','?')}: {l.get('title','')}".strip()
        if l.get("meaning"): seg += f" ({l['meaning']})"
        if l.get("remedy"): seg += f" - remedy: {l['remedy']}"
        parts.append(seg)
    if ctx.get("scan_match"):
        when = f" (scan of {ctx['scan_date']})" if ctx.get("scan_date") else ""
        rows = []
        for r in ctx["scan_match"]:
            seg = r["label"]
            if r.get("protocol_days"):
                seg += f", {r['protocol_days']}-day protocol"
            rows.append(seg)
        parts.append("Their scan match, most important first" + when + ": "
                     + "; ".join(rows) + ".")
        # Live read-back, 2026-11-19: asked how to take them, the chat said "eight
        # Energetic Driver infoceuticals" and named none.
        parts.append("When they ask about their remedies, name each one from this list "
                     "by its name, with its directions when they are given here.")
    if ctx.get("mihealth"):
        parts.append("Their scan also lists miHealth device cycles, which Dr. Glen runs "
                     "for them; these are not remedies to take: " + ", ".join(ctx["mihealth"]) + ".")
    for label, d in (ctx.get("directions") or {}).items():
        if isinstance(d, dict):
            parts.append(f"Approved directions for {label}: {d['directions']}")
            if d.get("caution"):
                parts.append(f"Caution for {label}, to give in full, word for word, whenever "
                             f"you give its directions. Keeping replies short never shortens "
                             f"it: {d['caution']}")
        else:
            parts.append(f"Approved directions for {label}: {d}")
    # Always: a client with no data on file can still ask how much to take (round 2).
    parts.append(DOSE_RULE)
    if not ctx.get("has_data"):
        parts.append("(No scan or order data on file yet - answer generally and invite them to share.)")
    return "\n".join(parts)
