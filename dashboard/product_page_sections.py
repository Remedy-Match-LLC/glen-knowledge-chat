"""Which /begin/product/<slug> sections to show for a given product.

The ingredient list, the formula-vs-formula comparison table, and — rendered by
the frontend *inside* the comparison section — the Miron violet-glass rotator +
"Learn the science" story are all formulation-only. They are meaningless (and
misleading) on a device/tool/book SKU that has no ingredient list and does not
ship in Miron glass, so they are dropped for those products. The Miron
educational video ("How Miron violet glass is made") is likewise not appended for
them, so the Watch section is dropped too unless the product has a video of its own.
"""

# Sections that only make sense when the product has an ingredient list.
FORMULATION_ONLY = ("ingredients", "comparison")


# Sections a SERVICE never shows (EVOX Session, Biofield Analysis). "The research" is
# teased as "Studies behind the key ingredients", and a service has no ingredients, so
# it could only be empty or wrong. Found on the EVOX page by marketing, 2026-09-22.
SERVICE_NEVER = ("research",)


def filter_sections(sections, *, has_ingredients, has_own_video, is_service=False, in_miron=True):
    """Return `sections` minus the formulation-only ones when the product has no
    ingredient list. `has_own_video` keeps the Watch section for a device that
    carries its own product video (only the Miron educational clip is withheld).
    A service also loses SERVICE_NEVER. in_miron=False (a product resold in its maker's
    packaging, e.g. the MSM lotions in plastic squeeze bottles) drops the comparison,
    which claims Miron violet glass and no excipients."""
    drop = set()
    if not in_miron:
        drop.add("comparison")
        if not has_own_video:
            drop.add("video")      # its only video was the Miron clip, now withheld
    if not has_ingredients:
        drop |= set(FORMULATION_ONLY)
        if not has_own_video:
            drop.add("video")
    if is_service:
        drop |= set(SERVICE_NEVER)
    return [s for s in sections if s.get("id") not in drop]
