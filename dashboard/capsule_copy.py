"""The capsule paragraph on a product page.

Glen, 2026-10-03: the capsule shell is hidden from the ingredient list (see
dashboard/products.shown_ingredients) and gets its own paragraph instead. Both
texts are his, word for word: the DRcaps paragraph approved via marketing, the
pullulan paragraph from the product-label-studio skill's product-page copy.
Which capsule each product uses was confirmed by production from FileMaker and
the newest label. Spike Shield is pullulan on Glen's word; FileMaker still lists
the enteric shell for it, so it is set here by hand, not derived.
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
}

CAPSULE_BY_SLUG = {
    "glutathione-syntropy": "drcaps",
    "lipid-zyme": "drcaps",
    "scar-silk": "drcaps",
    "alkalize-bicarbonate-blend": "drcaps",
    "spike-shield": "pullulan",
}


def capsule_for(slug):
    """{text, em} for the product's capsule paragraph, or None."""
    kind = CAPSULE_BY_SLUG.get(slug or "")
    return dict(CAPSULE_COPY[kind]) if kind else None
