#!/usr/bin/env python3
"""Move every catalog price in data/products.json up to the next whole dollar.

Glen, 2026-10-01: prices round up to whole dollars, so $69.97 becomes $70 and $39.97
becomes $40. Plan: vault `00 System/primary/plans/2026-10-01-whole-dollar-prices-plan.md`.

Edits the file as text, number by number, so the file's own formatting is kept (a
json.dump round trip rewrites hundreds of unrelated lines). Then it re-reads the result
and checks it:
  - no price field ends in cents;
  - every bundle equals dashboard.bundle_pricing's rule on the new component prices;
  - every "Price: $X" typed into a description matches that product's price.

Idempotent: a second run changes nothing. Usage: python3 scripts/whole_dollar_catalog.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.bundle_pricing import compute_bundle_price_cents  # noqa: E402

PATH = ROOT / "data" / "products.json"
PRICE_KEYS = ("price_cents", "regular_cents", "default_price_cents", "wholesale_cents")
NUM_RE = re.compile(r'("(?:%s)": )(\d+)' % "|".join(PRICE_KEYS))
DESC_PRICE_RE = re.compile(r"Price: \$(\d[\d,]*(?:\.\d\d)?)\.")

# Prose that names a computed figure rather than the product's own price.
# Old text -> new text, each expected exactly once.
PROSE = {
    "Bought separately, the six cost $419.82. As a program, you pay $359.97 for the "
    "same six bottles.":
    "Bought separately, the six cost $420. As a program, you pay $360 for the "
    "same six bottles.",
    "Digital edition of book-dry-eye-relief (print $30, ships). Digital is $9.97":
    "Digital edition of book-dry-eye-relief (print $30, ships). Digital is $10",
    "Digital edition of book-macular-regeneration (print $30, ships). Digital is $9.97":
    "Digital edition of book-macular-regeneration (print $30, ships). Digital is $10",
}


def ceil_dollar(cents):
    return -(-int(cents) // 100) * 100


def dollars(cents):
    return "${:,}".format(cents // 100)


def round_numbers(text):
    return NUM_RE.sub(lambda m: m.group(1) + str(ceil_dollar(int(m.group(2)))), text)


def fix_bundles(text, products):
    for slug, p in products.items():
        if not p.get("bundle_component_slugs"):
            continue
        want = compute_bundle_price_cents(p, products)
        have = int(p["price_cents"])
        if want == have:
            continue
        # Replace this bundle's own price_cents: the first one after its key.
        start, end = _block(text, slug)
        i = text.index('"price_cents": %d' % have, start, end)
        text = text[:i] + '"price_cents": %d' % want + text[i + len('"price_cents": %d' % have):]
        p["price_cents"] = want
    return text


def drop_flat_regular(text, products):
    """Remove a struck-through regular_cents that rounding made equal to the price.
    Infoceuticals were $39.97 with a $40 regular; at $40 the anchor shows nothing, and a
    regular_cents at or below the price is incoherent data (tests/test_invoice_srp_anchor)."""
    for slug, p in products.items():
        reg = p.get("regular_cents")
        if not (isinstance(reg, int) and reg <= int(p.get("price_cents") or 0)):
            continue
        start, end = _block(text, slug)
        block = text[start:end]
        m = re.search(r'\n *"regular_cents": %d(,?)' % reg, block)
        if not m:
            raise SystemExit("regular_cents of %s not found" % slug)
        if m.group(1):            # not the last key: drop the line and its comma
            block = block[:m.start()] + block[m.end():]
        else:                     # the last key: drop it and the comma before it
            head = block[:m.start()]
            assert head.endswith(","), slug
            block = head[:-1] + block[m.end():]
        text = text[:start] + block + text[end:]
    return text


def fix_descriptions(text, products):
    for old, new in PROSE.items():
        n = text.count(old)
        if n == 0 and text.count(new) == 1:
            continue  # already applied
        if n != 1:
            raise SystemExit("expected one %r, found %d" % (old[:40], n))
        text = text.replace(old, new)
    for slug, p in products.items():
        desc = p.get("description") or ""
        m = DESC_PRICE_RE.search(desc)
        if not m:
            continue
        want = dollars(int(p["price_cents"]))
        if "$" + m.group(1) == want:
            continue
        # Replace inside this product's own block: from its key to the next product key.
        start, end = _block(text, slug)
        block = text[start:end]
        old_s = "Price: $%s." % m.group(1)
        if block.count('"description": ') != 1 or block.count(old_s) != 1:
            raise SystemExit("description price of %s not found exactly once" % slug)
        text = text[:start] + block.replace(old_s, "Price: %s." % want) + text[end:]
    return text


def _block(text, slug):
    """(start, end) of one product's entry in the text: its key up to the next key at
    the same indentation."""
    key = '\n  "%s": {' % slug
    start = text.index(key)
    nxt = re.compile(r'\n  "[^"]+": \{').search(text, start + len(key))
    return start, (nxt.start() if nxt else len(text))


def check(text):
    d = json.loads(text)
    products = d["products"]
    bad = []
    if int(d.get("default_price_cents") or 0) % 100:
        bad.append("default_price_cents")
    for slug, p in products.items():
        for k in PRICE_KEYS:
            v = p.get(k)
            if isinstance(v, int) and v % 100:
                bad.append("%s.%s=%d" % (slug, k, v))
        reg = p.get("regular_cents")
        if isinstance(reg, int) and reg <= int(p.get("price_cents") or 0):
            bad.append("%s regular_cents %d not above its price" % (slug, reg))
        if p.get("bundle_component_slugs"):
            if compute_bundle_price_cents(p, products) != p["price_cents"]:
                bad.append("%s bundle price off the rule" % slug)
        m = DESC_PRICE_RE.search(p.get("description") or "")
        if m and "$" + m.group(1) != dollars(int(p["price_cents"])):
            bad.append("%s description says $%s" % (slug, m.group(1)))
        for k in ("description", "_note"):
            if re.search(r"\$\d[\d,]*\.\d\d\b", p.get(k) or ""):
                bad.append("%s.%s still names a cents price" % (slug, k))
    return bad


def main():
    text = PATH.read_text()
    text = round_numbers(text)
    products = json.loads(text)["products"]
    text = fix_bundles(text, products)
    text = drop_flat_regular(text, json.loads(text)["products"])
    text = fix_descriptions(text, json.loads(text)["products"])
    bad = check(text)
    if bad:
        print("REFUSING, the result still has:\n  " + "\n  ".join(bad), file=sys.stderr)
        sys.exit(1)
    PATH.write_text(text)
    print("ok")


if __name__ == "__main__":
    main()
