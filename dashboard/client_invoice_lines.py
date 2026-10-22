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


class NotOrderable(ValueError):
    """A posted line names a product that cannot be ordered yet (waitlist_only). The
    message is the buyer-facing refusal; the whole edit is refused."""


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


def rebuild(posted, existing, *, known, refill_ok=None, refusal=None):
    """Server-trusted lines from `posted`, using `existing` stored lines.

    `existing` is the stored invoice's lines in order, or the older {slug: stored line}.
    With a list, a posted line names its stored line by `i`, its position in the stored
    lines, so two lines for one product (a caregiver's and a member's) each keep their
    own note, price and billed_for, even after the customer removes one (review
    rounds 1 to 4, 2026-10-22). Without a usable `i`, the one unused stored line with
    that slug is used. When several are left, the last one is used without its
    billed_for, which is how the slug-keyed version behaved. A line with no stored line
    left for its slug is new and copies nothing.

    `known(slug)` decides whether a slug is a real, sellable product.
    `refill_ok(slug)` decides whether refill packs apply (30-capsule products only,
    2026-09-25); without it every product may take a refill, as before.
    `refusal(slug)` returns a buyer-facing refusal for a product that cannot be ordered
    yet (waitlist_only, 2026-10-09), or None. A posted line for one, at any quantity
    above zero, raises NotOrderable: the edit is refused whole, never silently dropped.
    """
    pool = list(existing) if isinstance(existing, list) else None
    used = set()

    def _stored_slug(k):
        it = pool[k]
        return (it.get("slug") or "").strip() if isinstance(it, dict) else ""

    def _stored_for(line, slug):
        i = line.get("i")
        if (isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(pool)
                and i not in used and _stored_slug(i) == slug):
            used.add(i)
            return dict(pool[i])
        left = [k for k in range(len(pool)) if k not in used and _stored_slug(k) == slug]
        if not left:
            return {}
        used.add(left[-1])
        old = dict(pool[left[-1]])
        if len(left) > 1:
            old.pop("billed_for", None)
        return old

    out = []
    for line in (posted or []):
        if not isinstance(line, dict):
            continue
        slug = (line.get("slug") or "").strip()
        if slug and refusal is not None and _qty(line.get("qty")) > 0:
            msg = refusal(slug)
            if msg:
                raise NotOrderable(msg)
        if not slug or not known(slug):
            continue
        old = (existing.get(slug) or {}) if pool is None else _stored_for(line, slug)
        fmt = (line.get("format") or old.get("format") or "bottle").strip().lower()
        if fmt == "refill" and refill_ok is not None and not refill_ok(slug):
            fmt = "bottle"
        rec = {"slug": slug, "qty": _qty(line.get("qty")),
               "format": fmt if fmt in _FORMATS else "bottle"}
        # A price survives only as an owner override. Everything else re-prices, so
        # a line can never freeze at whatever it last displayed -- that is exactly
        # how invoice #165 went out at $809.13.
        if old.get("override"):
            rec["unit_cents"] = old.get("unit_cents")
        # billed_for keeps a household member's line marked as theirs, so billing that
        # member again replaces their lines instead of adding a second set.
        for key in ("note", "source", "billed_for"):
            if old.get(key):
                rec[key] = old[key]
        out.append(rec)
    return out
