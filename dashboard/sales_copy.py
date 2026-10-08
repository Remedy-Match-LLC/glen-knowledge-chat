NARRATIVE_SECTIONS = ("intro", "description", "research")

COMPLIANCE = (
    "Use structure/function language only (supports, promotes, helps maintain). "
    "Do NOT claim to diagnose, treat, cure, or prevent any disease. Make no medical "
    "claims and cite no invented studies. Give a mechanism, a design rationale, or a reason "
    "for a dose or caution only where the details given below state it; never invent one. "
    "This is educational and not a substitute for "
    "medical advice. Do not use em dashes; use commas."
)

SECTION_BRIEFS = {
    "intro": ("Write ONE warm, concrete paragraph (about 2-4 sentences): what this product "
              "supports for the person and why it matters, grounded in its listed ingredients "
              "and description as given below."),
    "description": ("Write a fuller plain-language overview in 2-3 short paragraphs: what the "
                    "product is, what it is built from, what it supports as the details below "
                    "state it, and who it is for."),
    "research": ("In lay language, 1-2 short paragraphs, describe what the product and its "
                 "listed ingredients support, as the description and ingredient details below "
                 "state it. Describe a mechanism only where those details state one; otherwise "
                 "stay with what it supports."),
}

def _ingredient_lines(product):
    out = []
    for ing in (product.get("ingredients") or []):
        if isinstance(ing, dict):
            out.append((f"- {ing.get('name','')} {ing.get('dose','')}").rstrip())
        elif isinstance(ing, str) and ing.strip():
            out.append(f"- {ing.strip()}")
    return "\n".join(out)

def _is_infoceutical(product):
    # Infoceuticals are the third product class (alongside nutritional formulas and
    # devices/books). Older GrooveKart records identify them in the URL; migrated
    # in-house records often have no URL, but retain the class in their product or
    # Pinecone title. Check all authoritative identity fields so their pages never
    # receive nutritional-supplement copy merely because the legacy URL is absent.
    identity = " ".join(str(product.get(k) or "")
                        for k in ("name", "pinecone_title", "url"))
    return "infoceutical" in identity.lower()

def build_section_prompt(section, product):
    # The name the reader sees on the page. `name` stays the invoice/QBO identity, and
    # display_name differs from it on purpose (Sulfur Syntropy, sold as Sulfur Synergy in
    # QBO until money renames the item). Copy must use the page's name.
    name = product.get("display_name") or product.get("name", "")
    ings = _ingredient_lines(product)
    desc = (product.get("description") or "").strip()
    brief = SECTION_BRIEFS[section]
    # Infoceuticals are NES-style energetic/bioinformational remedies: encoded field
    # information carried in a liquid that supports the body's own energy field and natural
    # self-regulation. They are NOT nutritional supplements and have no ingredient list, so
    # the copy must never claim "nutritional support" or name nutrients, and must never ask
    # for a missing ingredient stack (that produced nutritional-formula copy + an outright
    # LLM refusal on infoceutical pages like Rejuvenation).
    if _is_infoceutical(product):
        system = ("You are writing sales-page copy for Dr. Glen Swartwout's Infoceuticals: "
                  "energetic-frequency (bioinformational) remedies, encoded field information "
                  "carried in a liquid, that support the body's own energy field and its "
                  "natural self-regulation. Voice: warm, clinically grounded, and specific: no "
                  "fluff, no AI-pleasantry filler, no cliches. Write only about THIS "
                  "Infoceutical, using the details given below. An Infoceutical is NOT a "
                  "nutritional supplement: it has no ingredients and delivers no nutrients, so "
                  "NEVER describe it as providing 'nutritional support' or name any nutrient, "
                  "vitamin, mineral, or herb. Frame what it offers as energetic frequency "
                  "support for the body's field. Never ask for missing information, and never "
                  "invent an ingredient list, a formula, or nutrients it does not have. "
                  + COMPLIANCE)
        parts = [f"Infoceutical: {name}"]
        if desc:
            parts.append(f"Description:\n{desc}")
        parts.append("This is an energetic-frequency Infoceutical: it has no ingredient list "
                     "and delivers no nutrients. Ground the copy in what this Infoceutical is "
                     "and what it supports energetically, using its name and any description above.")
        parts.append(f"Task: {brief}\n\nReturn only the copy itself, with no headings, "
                     "labels, or preamble.")
        return system, "\n\n".join(parts)
    # Products are formulas AND devices/tools/books. Devices have no ingredient list, so the
    # prompt must ground them in the authored description and forbid inventing a formula or
    # asking for a "missing" ingredient stack (that produced hallucinated nutritional-formula
    # copy and literal LLM refusals on device pages).
    system = ("You are writing sales-page copy for Dr. Glen Swartwout's products, which include "
              "nutritional formulas as well as devices, tools, and books. Voice: warm, clinically "
              "grounded, and specific: no fluff, no AI-pleasantry filler, no cliches. Write only "
              "about THIS product, using the details given below. Some products (devices, tools, "
              "books) have no ingredient list: for those, ground the copy in the product "
              "description. Never ask for missing information, and never invent "
              "an ingredient list, a formula, or nutrients the product does not have. " + COMPLIANCE)
    parts = [f"Product: {name}"]
    if desc:
        parts.append(f"Product description:\n{desc}")
    parts.append("Ingredient stack:\n" + (ings or "(none: this product has no ingredient list)"))
    parts.append(f"Task: {brief}\n\nReturn only the copy itself, with no headings, labels, or preamble.")
    user = "\n\n".join(parts)
    return system, user
