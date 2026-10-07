"""Product names that mean more than one product, so no resolver may pick one.

A bare "Seacure" is the older name of both SeaAmino Powder (`seaaminos`) and SeaAmino
Syntropy capsules (`seaamino-syntropy`). Glen, 2026-09-30: "bare is older and can now
mean both the powder and the Syntropy capsules." Every consumer that turns a name into a
product checks this first, before any alias, exact, substring or fuzzy step, and leaves
the name for a person to choose. "Seacure Synergy" is not ambiguous: it is the capsules.
"""
import re

AMBIGUOUS_NAMES = frozenset({"seacure"})


def is_ambiguous_product_name(name):
    """True when `name`, ignoring case, spacing and punctuation, means two products."""
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip() in AMBIGUOUS_NAMES
