"""Keep the Biofield narrative to what the client's chain supplies.

Clinical, 2026-09-25 (Donna Banks a43, Peach Goddard a41) found three faults in confirmed
reports. Each had a cause in what the writer was given or allowed:

1. It named Liver Support, on no chain row. The scan findings it was handed end with
   "Consider: Liver Support, Free & Easy". clean_scan_description drops those lines.
2. It wrote "E4L voice scan", because our own prompt told it to. Glen, 2026-09-18: E4L's
   instrument is the Bioenergetic Wellness Scan, and a bare "voice scan" never reaches a
   client. fix_scan_names corrects the text after generation.
3. It credited B17 Max with B17 Syntropy's vitamins by naming both in one sentence.
   check_narrative finds that, and off-chain product names, so the caller can retry once
   and then show Glen what is still wrong.

Pure functions. The caller supplies the catalog and each remedy's ingredient list.
"""
import re

WELLNESS_SCAN = "Bioenergetic Wellness Scan"

# ── scan block ──────────────────────────────────────────────────────────────

_CONSIDER = re.compile(r"\s*\bconsider:.*$", re.IGNORECASE)
_SOURCED = re.compile(r"^\s*\[SOURCED:.*\]\s*$", re.IGNORECASE)


def clean_scan_description(desc):
    """A scan finding's description without its product suggestions or source tags.
    Both are notes for the practitioner. Handed to the writer, a suggestion became a
    recommendation the chain never made."""
    lines = []
    for line in str(desc or "").splitlines():
        if _SOURCED.match(line):
            continue
        line = _CONSIDER.sub("", line).rstrip()
        lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── scan names ──────────────────────────────────────────────────────────────

_E4L = r"(?:E4L|Energy4Life|Energy 4 Life)[\s-]+"
_SCAN_WORD = r"(?:scans?|scanning|analys[ie]s)"
# Most specific first, so "E4L Bioenergetic Voice Scan" is replaced whole and no word
# is doubled (blind review, 2026-09-25).
_SCAN_NAME_PATTERNS = [
    re.compile(r"\b(?:" + _E4L + r")?Bioenergetic[\s-]+Voice[\s-]+" + _SCAN_WORD + r"\b",
               re.IGNORECASE),
    re.compile(r"\b" + _E4L + r"voice[\s-]+" + _SCAN_WORD + r"\b", re.IGNORECASE),
    re.compile(r"(?<!element )(?<!element-)(?<!elements )(?<!elements-)\bvoice[\s-]+scans?\b",
               re.IGNORECASE),
]


def fix_scan_names(text):
    """Every name for E4L's scan becomes the Bioenergetic Wellness Scan. Glen's own
    Five Element Voice Scan is a different instrument and keeps its name."""
    out = text or ""
    for pat in _SCAN_NAME_PATTERNS:
        out = pat.sub(WELLNESS_SCAN, out)
    return re.sub(r"\b([Aa])n (" + WELLNESS_SCAN + r")", r"\1 \2", out)


# ── checks ──────────────────────────────────────────────────────────────────

# Words that head an ingredient name but say nothing a client would read as a claim.
_NOT_NUTRIENTS = {
    "water", "rice", "oil", "extract", "blend", "powder", "capsule", "vegicap",
    "cellulose", "gelatin", "silica", "organic", "base", "terrain restore", "enteric",
    "fiber", "enzymes", "probiotic", "flower", "essence", "brandy", "alcohol",
}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_VITAMIN_LIST = re.compile(
    r"\bvitamins?\s+((?:[A-K]\d{0,2}\b(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)?)+)",
    re.IGNORECASE)


def _term(ingredient):
    """The nutrient an ingredient line names: 'Curcumin 95% HPLC' -> 'Curcumin',
    'Magnesium (Magnesium Taurate 8%)' -> 'Magnesium'."""
    head = re.split(r"[(:,;\[]", str(ingredient or ""), maxsplit=1)[0]
    parts = head.split()
    # Drop strengths and ratios ("95%", "4:1") and trailing lab codes ("HPLC"). Keep a
    # name's own digits: "Coenzyme Q10", "Vitamin B12".
    words = [w for i, w in enumerate(parts)
             if not re.fullmatch(r"[\d.,:%/x-]+", w)
             and not (i > 0 and w.isalpha() and w.isupper() and len(w) > 1)]
    return " ".join(words).strip()


_BARE_CODE = re.compile(r"(?<![\w-])(B\d{1,2}|D3|K2)(?![\w-])")
_PRONOUN_START = re.compile(r"^\W*(?:it|its|this|these|they|their|both)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|without|free of|free from|none)\b[^.]{0,30}$", re.IGNORECASE)


def _vitamins_in(sentence):
    found = ["Vitamin " + c for c in _BARE_CODE.findall(sentence)]
    for m in _VITAMIN_LIST.finditer(sentence):
        for letter in re.findall(r"\b[A-K]\d{0,2}\b", m.group(1), re.IGNORECASE):
            v = "Vitamin " + letter.upper()
            if v not in found:
                found.append(v)
    return found


# One nutrient, several names. Found on real narratives: "B17 Syntropy provides Folate"
# is true, because its label says "Vitamin B9 (5-MTHF)".
_SAME_NUTRIENT = [
    {"vitamin a", "retinol", "retinyl"},
    {"vitamin b1", "thiamine", "thiamin", "benfotiamine"},
    {"vitamin b2", "riboflavin"},
    {"vitamin b3", "niacin", "niacinamide", "nicotinamide"},
    {"vitamin b5", "pantothenic", "pantethine"},
    {"vitamin b6", "pyridoxine", "pyridoxal", "p5p"},
    {"vitamin b7", "biotin"},
    {"vitamin b9", "folate", "folic acid", "methylfolate", "5-mthf"},
    {"vitamin b12", "cobalamin", "methylcobalamin", "adenosylcobalamin"},
    {"vitamin c", "ascorbic", "ascorbate", "ascorbyl"},
    {"vitamin d3", "vitamin d", "cholecalciferol"},
    {"vitamin e", "tocopherol", "tocotrienol"},
    {"vitamin k2", "vitamin k", "menaquinone"},
    {"coenzyme q10", "coq10", "ubiquinol", "ubiquinone"},
    {"silymarin", "milk thistle", "silybum"},
    {"curcumin", "turmeric", "curcuma"},
    {"egcg", "green tea"},
    {"berberine", "dihydroberberine"},
    {"nac", "n-acetyl cysteine", "n-acetylcysteine"},
    {"glutathione", "s-acetyl glutathione"},
    {"resveratrol", "trans-resveratrol"},
    {"vitamin b17", "amygdalin", "laetrile"},
    {"vitamin b15", "pangamic"},
]


# Synonyms are looked for in the text too, or "provides folate" would pass unseen
# whenever no catalog label happens to start with the word.
_SYNONYM_TERMS = {n for g in _SAME_NUTRIENT for n in g if not n.startswith("vitamin")}


def _names_for(nutrient):
    n = nutrient.lower()
    for group in _SAME_NUTRIENT:
        if n in group:
            return group
    return {n}


def _contains(ingredients, nutrient):
    names = _names_for(nutrient)
    return any(name in str(i).lower() for i in ingredients or [] for name in names)


def _squash(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower().replace("+", "plus"))


def _is_chain_product(name, chain):
    """A catalog name is the chain's remedy under another spelling when one squashed name
    contains the other: 'Clear Lens Eyedrops' and 'Clear Lens Eye Drops ACES+CAT'."""
    n = _squash(name)
    return any(n and c and (n in c or c in n) for c in (_squash(x) for x in chain))


def _names_product(name, text):
    """A capitalised product name in the text. A one-word name at a sentence start is
    ordinary prose: "Sleep is when repair happens" does not name the product Sleep.
    "&" and "and" are one spelling: "Free and Easy" names Free & Easy."""
    pat = r"\s*(?:&|and)\s*".join(re.escape(part.strip()) for part in name.split("&"))
    for m in re.finditer(r"(?<![\w+])" + pat + r"(?![\w+])", text):
        if " " in name.strip():
            return True
        before = text[:m.start()].rstrip()
        if before and not before.endswith((".", "!", "?", ":", "\n")) and not before[-1] in "\"'(":
            return True
    return False


def _negated(nutrient, text):
    """'contains no curcumin', 'free of gluten': named, but not claimed."""
    m = re.search(r"(?<![\w+])" + re.escape(nutrient.replace("Vitamin ", "")) + r"(?![\w+])",
                  text, re.IGNORECASE)
    return bool(m) and bool(_NEGATION.search(text[:m.start()]))


def _word_in(term, text, flags=re.IGNORECASE):
    return re.search(r"(?<![\w+])" + re.escape(term) + r"(?![\w+])", text, flags) is not None


def check_narrative(text, *, chain, ingredients, catalog_names, catalog_ingredients,
                    allowed_text="", heads_text=""):
    """Problems a reader could be misled by, as plain sentences for Glen. Empty when clean.

    chain: remedy names on the client's chain. ingredients: {remedy: [ingredient line]}.
    catalog_names / catalog_ingredients: the whole catalog, so a product or nutrient from
    outside the chain is seen. allowed_text: everything the writer was given; a product
    named there is not off-chain invention. heads_text: the chain's Head and Tail text,
    whose words name body areas, not nutrients.
    """
    text = text or ""
    problems = []
    chain_low = {c.lower() for c in chain}
    # A name that is part of a chain remedy's own label ("5-MTHF" in B17 Syntropy's
    # "Vitamin B9 (5-MTHF)") names that ingredient, not another product.
    chain_labels = " ".join(str(i) for c in chain for i in (ingredients.get(c) or [])).lower()

    # Off-chain products: matched with case, because a product name is capitalised and
    # the same word in lower case is ordinary prose ("helps the body transform").
    for name in sorted({n for n in catalog_names if n and len(n) >= 4}, key=len, reverse=True):
        if (_is_chain_product(name, chain) or name.lower() in (allowed_text or "").lower()
                or name.lower() in chain_labels):
            continue
        if _names_product(name, text):
            problems.append(f"Names {name}, which is not on this client's chain.")

    heads_low = (heads_text or "").lower()
    vocab = set()
    for ing in catalog_ingredients or []:
        t = _term(ing)
        short_ok = len(t) == 3 and t.isalpha() and t.isupper()     # DHA, EPA, NAC
        if ((len(t) >= 4 or short_ok) and t.lower() not in _NOT_NUTRIENTS and not t.lower().startswith("vitamin")
                and t.lower() not in chain_low and t.lower() not in heads_low):
            vocab.add(t)
    # Head and Tail words name body areas: "Silymarin Terrain" is not a nutrient claim.
    terms = vocab | {t for t in _SYNONYM_TERMS if t not in heads_low}
    by_len = sorted(chain, key=len, reverse=True)

    for para in re.split(r"\n\s*\n", text):
        last_remedies = []
        for sentence in _SENTENCE.split(para):
            rest, named = sentence, []
            for r in by_len:                       # longest first: "B17 Syntropy" before "B17"
                if _word_in(r, rest):
                    named.append(r)
                    rest = re.sub(re.escape(r), " ", rest, flags=re.IGNORECASE)
            nutrients = _vitamins_in(rest) + sorted(t for t in terms if _word_in(t, rest))
            nutrients = list({n.lower(): n for n in reversed(nutrients)}.values())[::-1]
            # "the MSM on layer 1" is the chain's MSM Powder, shortened, not a claim.
            nutrients = [n for n in nutrients if not _negated(n, rest)
                         and not any(c.startswith(n.lower() + " ") for c in chain_low)]
            # Only a pronoun ("It supplies...") carries the last remedy forward; "Leafy
            # greens rich in magnesium" after a remedy sentence is not about that remedy.
            remedies = named or (last_remedies if _PRONOUN_START.match(sentence) else [])
            if named:
                last_remedies = named
            if not nutrients or not remedies:
                continue
            for n in nutrients:
                for r in remedies:
                    listed = ingredients.get(r) or []
                    if not listed:
                        problems.append(f"Credits {r} with {n}, but no ingredient list is "
                                        f"on file for it.")
                    elif not _contains(listed, n):
                        problems.append(f"Says {r} provides {n}, which is not in its formula.")
    return list(dict.fromkeys(problems))
