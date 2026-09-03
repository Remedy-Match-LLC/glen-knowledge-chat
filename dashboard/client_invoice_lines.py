"""Rebuild an invoice's lines from what a client posted from their own link.

The client controls quantity, packaging, which products are on the invoice, and
nothing else. Prices, notes and recommendation provenance come from the stored
invoice, so a posted price can never reach the pricer.

Zero is a real quantity here. Glen built zero-qty lines as a client-visible
reorder reference, and the console keeps them, but this path used to drop them:
a client zeroing a line destroyed the very reference the feature exists to keep.
Removing a line is therefore its own act -- omit it from the posted list.
"""

_FORMATS = ("bottle", "refill")


def _qty(value):
    """0..99. A MISSING qty means one; only an explicit 0 means zero.

    `int(x or 1)` is the trap: 0 is falsy, so a deliberate zero silently became a
    one. Unparseable input is treated as missing rather than as zero -- guessing
    "none of this" from a typo is the more destructive reading.
    """
    if value is None or value == "":
        return 1
    try:
        return max(0, min(int(value), 99))
    except (TypeError, ValueError):
        return 1


def rebuild(posted, existing, *, known):
    """Server-trusted lines from `posted`, using `existing` {slug: stored line}.

    `known(slug)` decides whether a slug is a real, sellable product.
    """
    out = []
    for line in (posted or []):
        if not isinstance(line, dict):
            continue
        slug = (line.get("slug") or "").strip()
        if not slug or not known(slug):
            continue
        old = existing.get(slug) or {}
        fmt = (line.get("format") or old.get("format") or "bottle").strip().lower()
        rec = {"slug": slug, "qty": _qty(line.get("qty")),
               "format": fmt if fmt in _FORMATS else "bottle"}
        # A price survives only as an owner override. Everything else re-prices, so
        # a line can never freeze at whatever it last displayed -- that is exactly
        # how invoice #165 went out at $809.13.
        if old.get("override"):
            rec["unit_cents"] = old.get("unit_cents")
        for key in ("note", "source"):
            if old.get(key):
                rec[key] = old[key]
        out.append(rec)
    return out
