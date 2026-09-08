"""Prompts for a product's two sales-page images.

One botanical scene and one mechanism scene per product, both derived from that
product's own ingredients.

**Why this file changed on 2026-09-08.** It used to send the same two sentences for
every product, because an earlier pass concluded that "injecting names is what makes
Flux render text". That was measured and is wrong. Naming an ingredient renders the
ingredient. What made Flux render text was *asking for labels*, which the exclusion
below has forbidden since PR #174.

So the scene is now product-specific and the exclusion stays. Glen's rule, 2026-09-08:
the botanicals shown must be the ones in the formula, and the mechanism must be a real
scene rather than a glowing cell that would suit anything.
"""

IMAGE_KINDS = ("botanical", "mechanism")

# Appended to every prompt. This is the part that keeps text out of the image.
_NO_TEXT = ("No text, no words, no letters, no numbers, no labels, no captions, no logos, "
            "and no product packaging, bottles, jars, tubes, or containers anywhere in the image.")
NO_TEXT = _NO_TEXT   # public alias

# Used when a product has no usable ingredient list, and when the model is unavailable.
_BOTANICAL_BODY = ("Photo-quality botanical wellness lifestyle scene: an abundance of fresh herbs, "
                   "green leaves, flowers, roots, and colorful whole botanical ingredients arranged on a "
                   "natural wooden kitchen counter, an attractive mature woman gently preparing fresh "
                   "herbs, a lush green herb garden visible behind her.")
_MECHANISM_BODY = ("Photo-quality conceptual render: a single glowing living human cell surrounded by a "
                   "radiant protective energy field, luminous particles flowing inward toward it, "
                   "conveying cellular resilience, vitality, and protection.")
_BODY = {"botanical": _BOTANICAL_BODY, "mechanism": _MECHANISM_BODY}

_STYLES = {
    "botanical": ["warm natural daylight, eye-level composition",
                  "soft golden-hour light, slightly elevated three-quarter angle",
                  "bright airy morning light, overhead flat-lay composition",
                  "cozy warm interior light, close intimate framing"],
    "mechanism": ["clean studio render, deep teal background",
                  "luminous dark background with volumetric light, dramatic angle",
                  "iridescent blue-violet palette, centered symmetrical composition",
                  "warm amber glow on a black background, shallow depth of field"],
}

_client = None
_model = "claude-haiku-4-5-20251001"


def configure(client=None, model=None):
    """Injected at app startup. Without a client this module still works, generically."""
    global _client, _model
    if client is not None:
        _client = client
    if model:
        _model = model


_SCENE_BRIEF = """You write image-generator scene descriptions for a supplement's sales page.

The formula is "%(name)s" and contains:
%(ingredients)s

Return JSON only, exactly: {"botanical": "...", "mechanism": "..."}

botanical: a photo-quality scene on a natural wooden kitchen counter showing the visible
natural forms of THIS formula's own ingredients. Describe each by colour, shape and
texture so it is recognisable. Include an attractive mature woman arranging them, her
face visible in the frame and warmly lit, and a lush green herb garden behind her. Do not
crop her head out of the shot. Her hands are bare and natural, with short unpainted nails. If an ingredient is a vitamin, mineral or isolate with
no natural form, leave it out. If the formula's actives come from an animal, algal or
marine source, show that source. 35 to 55 words.

mechanism: one photo-quality conceptual scene of the specific part of the body this
formula supports, rendered as luminous art. Name the organ, tissue or structure. Show the
actives reaching it. Prefer a concrete subject such as an eye and its retina, a brain and
its neural pathways, a joint, a gut lining, or a cell membrane. Never a generic glowing
sphere. 30 to 50 words.

Describe only what is visible. Never ask for text, labels, packaging or bottles."""


def _ingredient_lines(product):
    """The formula's ingredients, or its own name when it has no list.

    Measured 2026-09-08: 12 of 30 sampled products carry no ingredient list. Some are
    single-ingredient products, where the product name IS the ingredient, so the name
    alone is enough to build a real scene. Devices and essences still fall back.
    """
    out = []
    for ing in (product or {}).get("ingredients") or []:
        name = ing.get("name") if isinstance(ing, dict) else ing
        name = (name or "").strip()
        if name:
            out.append("- " + name)
    if not out:
        own = ((product or {}).get("name") or "").strip()
        if own:
            out.append("- " + own)
    return out[:20]


def derive_scenes(product):
    """Two product-specific scene descriptions, or None to fall back to generic.

    Never raises. An image that is merely generic is a much smaller problem than a
    product page whose images stop generating.
    """
    if _client is None or not product:
        return None
    lines = _ingredient_lines(product)
    if not lines:
        return None
    try:
        import json
        brief = _SCENE_BRIEF % {"name": (product.get("name") or "this formula").strip(),
                                "ingredients": "\n".join(lines)}
        msg = _client.messages.create(
            model=_model, max_tokens=600,
            messages=[{"role": "user", "content": brief}],
        )
        raw = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(raw[start:end + 1])
        scenes = {}
        for kind in IMAGE_KINDS:
            text = (data.get(kind) or "").strip()
            if len(text) < 40:          # too short to be a scene
                return None
            scenes[kind] = text
        return scenes
    except Exception as e:
        print(f"[sales-img] scene derivation failed: {e!r}", flush=True)
        return None


def build_one_prompt(kind, variant_index, product=None):
    """A single prompt for `kind`, cycling the style at `variant_index`.

    Used by the image tournament, which renders one replacement challenger at a time.
    """
    styles = _STYLES[kind]
    style = styles[(int(variant_index) - 1) % len(styles)]
    body = _BODY[kind]
    if product:
        scenes = derive_scenes(product)
        if scenes:
            body = scenes[kind]
    return f"{body} {_NO_TEXT} {style}."


def build_image_prompts(product=None):
    """One prompt per kind, so one image per kind and two per product.

    It was two per kind while the page asked buyers to vote between them. Glen retired
    the vote on 2026-09-08, so the second of each pair was generated and never shown.
    """
    scenes = derive_scenes(product) or _BODY
    return {k: [f"{scenes[k]} {_NO_TEXT} {_STYLES[k][0]}."] for k in IMAGE_KINDS}
