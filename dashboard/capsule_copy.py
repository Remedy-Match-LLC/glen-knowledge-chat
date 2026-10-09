"""The capsule paragraph on a product page.

Glen, 2026-10-03: the capsule shell is hidden from the ingredient list (see
dashboard/products.shown_ingredients) and gets its own paragraph instead. Both
texts are his, word for word: the DRcaps paragraph approved via marketing, the
pullulan paragraph from the product-label-studio skill's product-page copy.
Which capsule each product uses was confirmed by production from FileMaker and
the newest label. Spike Shield is pullulan on Glen's word; FileMaker still lists
the enteric shell for it, so it is set here by hand, not derived. Every other
capsule product defaults to pullulan (Glen, 2026-10-03). Three are in chlorophyll
vegicaps until their next production run (Glen, 2026-10-03), set by hand below.
"""

CAPSULE_COPY = {
    "drcaps": {
        "text": ("Each capsule is a phthalate-free DRcaps™ delayed-release vegicap, made of "
                 "hypromellose and gellan gum. It is designed to protect its contents from "
                 "stomach acid and delay their release. Most enteric capsules and coatings "
                 "rely on phthalates. This one does not, and it carries no added chemicals "
                 "or solvents."),
        "em": [],
    },
    "pullulan": {
        "text": ("A. pullulans is a beneficial endophyte inside many food plants. A. pullulans "
                 "is a naturally occurring mycorrhizal fungus that functions as an epiphyte in "
                 "the health-promoting microbiome of many food plants from grapes to green "
                 "beans, and also participates symbiotically as a beneficial endophyte inside "
                 "the plants. It is used agriculturally in biological control of plant "
                 "diseases."),
        "em": ["A. pullulans"],
    },
    # Glen approved this wording in production's tab, 2026-10-03 ("ship").
    "chlorophyll": {
        "text": ("Each capsule is a chlorophyll vegicap, made mainly from plant cellulose. It "
                 "contains no gelatin or other animal products."),
        "em": [],
    },
}

# Set by hand. Each confirmed by production from FileMaker and the newest label.
# Spike Shield is pullulan on Glen's word; FileMaker still lists the enteric shell.
CAPSULE_BY_SLUG = {
    "glutathione-syntropy": "drcaps",
    "lipid-zyme": "drcaps",
    "scar-silk": "drcaps",
    "alkalize-bicarbonate-blend": "drcaps",
    "microbiome": "drcaps",
    "spike-shield": "pullulan",
    "spleen-support": "pullulan",   # FileMaker line 5463; its old copy said enteric
    # Glen, production's tab, 2026-10-03: "Lens-Zyme is enteric, as is vitamin C. DHT is
    # pullulan". Enteric means DRcaps.
    "lens-zyme": "drcaps",
    "vitamin-c-syntropy": "drcaps",
    "dht-blocker": "pullulan",
    # Glen made batch CTW-20261008-10 in phthalate-free DRcaps (spec 2026-10-08-clear-the-way).
    "clear-the-way": "drcaps",
    # Glen, 2026-10-03: "Chlorophyll vegicaps until the next production run. Then they'll
    # be Pullulan." Nothing changes these automatically: move each to pullulan BY HAND
    # when its next run ships. Without these lines their 30 Caps bottles fall through to
    # pullulan, which is wrong for the jars on the shelf.
    "appestat": "chlorophyll",        # -> pullulan at its next production run, by hand
    "migrafree": "chlorophyll",       # -> pullulan at its next production run, by hand
    "iron-syntropy": "chlorophyll",   # -> pullulan at its next production run, by hand
}

# Capsule type not yet confirmed: no paragraph until Glen answers. Empty since 2026-10-03.
UNCONFIRMED = frozenset()

# Glen, 2026-10-03: the pullulan paragraph goes on every product in the pullulan
# capsule, and the label studio rule makes pullulan the standard capsule. So any
# capsule bottle not listed above is pullulan.
CAPSULE_BOTTLES = frozenset({"30 caps", "120 caps"})


def capsule_kind(slug, product=None):
    """"drcaps", "pullulan", "chlorophyll", or None (not a capsule, or not confirmed)."""
    slug = slug or ""
    if slug in CAPSULE_BY_SLUG:
        return CAPSULE_BY_SLUG[slug]
    if slug in UNCONFIRMED:
        return None
    bottle = str((product or {}).get("bottle_type") or "").strip().lower()
    return "pullulan" if bottle in CAPSULE_BOTTLES else None


def capsule_for(slug, product=None):
    """{text, em} for the product's capsule paragraph, or None."""
    kind = capsule_kind(slug, product)
    return dict(CAPSULE_COPY[kind]) if kind else None
