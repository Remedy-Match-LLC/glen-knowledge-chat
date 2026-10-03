"""Pure powders: sold, never a Remedy Match, never promoted by chat.

Glen, 2026-10-03, relayed by production: the Pure Powder rule covers the single-ingredient
store powders and the food powders ("food powders too"). All of them stay sellable.

Matched by SLUG, or by the product's exact NAME. Never by a word inside a name, so a
formula that contains MSM or quercetin is unaffected. Pure logic, no I/O.
"""
import re

PURE_POWDER_SLUGS = frozenset({
    # Single-ingredient store powders (no FileMaker record)
    "nacetyl-cysteine", "tmg", "lcarnosine", "fulvic-acid", "humic-acid",
    "magnesium-taurate", "sumac-bran-501-pure-powder",
    # FileMaker-typed powders
    "bitter-melon-tea-powder-momordica-charantia", "bone-broth-powder", "chlorella-powder",
    "cilantro-juice-powder", "hydrolyzed-collagen",
    "hydrolyzed-collagen-powder-grass-fed-type-i-iii", "hydrolyzed-pea-protein-powder",
    "hydrolyzed-whey-protein-powder", "msm-powder", "pea-protein-aminos-pure-predigested",
    "quercetin-dihydrate", "seaaminos",
    # Inactive twins, kept so a manual pick cannot bypass the rule
    "hydrolyzed-collagen-powder", "quercetin-dihydrate-powder-60-grams", "seaamino-powder",
})

# The catalog names of the slugs above, lowercased, whitespace collapsed.
PURE_POWDER_NAMES = frozenset({
    "n-acetyl cysteine", "tmg", "l-carnosine", "fulvic acid", "humic acid",
    "magnesium taurate", "sumac bran 50:1 pure powder",
    "bitter melon tea powder (momordica charantia)", "bone broth powder",
    "chlorella powder", "cilantro juice powder", "hydrolyzed collagen powder",
    "hydrolyzed collagen powder - grass fed, type i & iii", "hydrolyzed pea protein powder",
    "hydrolyzed whey protein powder", "msm powder", "pea protein aminos - pure & predigested",
    "quercetin dihydrate", "seaamino powder", "quercetin dihydrate powder 60 grams",
})


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_pure_powder(slug=None, name=None):
    """True when the slug is listed, or the whole name is a listed product's name."""
    return _norm(slug) in PURE_POWDER_SLUGS or _norm(name) in PURE_POWDER_NAMES
