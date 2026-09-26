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
# "scanning" is not renamed: "during Bioenergetic Wellness Scan we saw" is not English.
# It is left as written and reported by scan_name_problems (review, 2026-09-26).
_SCAN_WORD = r"(?:scans?|analys[ie]s)"
# Most specific first, so "E4L Bioenergetic Voice Scan" is replaced whole and no word
# is doubled (blind review, 2026-09-25).
_SCAN_NAME_PATTERNS = [
    re.compile(r"\b(?:" + _E4L + r")?Bioenergetic[\s-]+Voice[\s-]+" + _SCAN_WORD + r"\b",
               re.IGNORECASE),
    re.compile(r"\b" + _E4L + r"voice[\s-]+" + _SCAN_WORD + r"\b", re.IGNORECASE),
    re.compile(r"\bvoice[\s-]+scans?\b", re.IGNORECASE),
]
# Glen's own instrument, however it is formatted: "**Five Element** Voice Scan",
# "Five Elements' Voice Scan", a line break between, or "voice scan (Five Element)".
# Glen, 2026-09-26: it is now the Five Element Voice ANALYSIS, so only Energy4Life's is
# ever a scan. Its old "Voice Scan" wording is renamed to that, never to E4L's name.
FIVE_ELEMENT_NAME = "Voice Analysis"
_FIVE_BEFORE = re.compile(r"(?:five|5)[\s-]*elements?['\u2019]?[\s*_'\u2019]*$", re.IGNORECASE)
_FIVE_AFTER = re.compile(r"^[\s*_]*\(?\s*(?:five|5)[\s-]*element", re.IGNORECASE)


MENTIONS_FIVE = _MENTIONS_FIVE = re.compile(r"(?:five|5)[\s-]*elements?", re.IGNORECASE)


def _is_five_element(m, text):
    return bool(_FIVE_BEFORE.search(text[max(0, m.start() - 30):m.start()])
                or _FIVE_AFTER.search(text[m.end():m.end() + 30]))


# "the E4L and Five Element voice scans" names BOTH instruments: renaming it as either
# is wrong, so it is left and reported (review, 2026-09-26).
_BOTH = re.compile(r"(?:E4L|Energy\s?4\s?Life|Bioenergetic)\W+(?:and|&|or)\W+(?:\*\*)?(?:five|5)"
                   r"|(?:five|5)[\s-]*elements?\W+(?:and|&|or)\W+(?:E4L|Energy\s?4\s?Life|Bioenergetic)",
                   re.IGNORECASE)


def _is_glens(m, text):
    """Glen's Five Element instrument alone, not a phrase naming both."""
    window = text[max(0, m.start() - 45):m.end() + 45]
    return _is_five_element(m, text) and not _BOTH.search(window)


def _sentence_around(m, text):
    start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, m.end()) for c in ".!?\n") if i != -1]
    return text[start:min(ends) if ends else len(text)]


def _rename(m, text, bare=False, five_context=False):
    if _is_five_element(m, text):
        # "Five Element Voice Scan" -> "Five Element Voice Analysis", plural kept. Only a
        # bare "voice scan" match reaches here; E4L-qualified forms never sit beside it.
        if not bare or not _is_glens(m, text):
            return m.group(0)
        return "Voice Analyses" if m.group(0).lower().endswith("scans") else FIVE_ELEMENT_NAME
    # "Your Bioenergetic Wellness Scan (E4L voice scan)" would read the name twice.
    # Left as written, and reported by scan_name_problems.
    if WELLNESS_SCAN.lower() in _sentence_around(m, text).lower():
        return m.group(0)
    # A bare "voice scan" in a letter that also names the Five Element scan could be
    # either instrument. Leave it, and scan_name_problems tells Glen.
    if bare and (five_context or _MENTIONS_FIVE.search(text)):
        return m.group(0)
    plural = m.group(0).lower().endswith(("scans", "analyses"))
    return WELLNESS_SCAN + ("s" if plural else "")


def scan_name_problems(text):
    """Scan names a client must not read. Any wording fix_scan_names would change is
    reported, so a letter saved or edited by hand is caught on save and page load, not
    only at generation (clinical, 2026-09-25: 29 of 37 saved letters said "voice scan").
    A bare "voice scan" beside the Five Element Voice Analysis is left for Glen to name."""
    out = []
    text = text or ""
    fixed = fix_scan_names(text)
    if fixed != text:
        out.append(f"Uses an old scan name. E4L's is the {WELLNESS_SCAN}; Glen's own is "
                   f"the Five Element Voice Analysis.")
    # Whatever the rename leaves behind still needs a person: beside the Five Element
    # scan, a doubled name, or "voice scanning" (review, 2026-09-26).
    for m in _ANY_VOICE.finditer(fixed):
        glens_name = _is_glens(m, fixed) and m.group(0).lower().endswith(("analysis", "analyses"))
        if not glens_name:
            out.append(f"Still says '{m.group(0)}'. Name which scan it means.")
    return list(dict.fromkeys(out))


_ANY_VOICE = re.compile(r"\bvoice[\s-]+(?:scans?|scanning|analys[ie]s)\b", re.IGNORECASE)


def fix_scan_names(text, five_context=False):
    """Every name for E4L's scan becomes the Bioenergetic Wellness Scan. Glen's own
    instrument is renamed from "Five Element Voice Scan" to the Five Element Voice
    Analysis (Glen, 2026-09-26), never to E4L's name.
    five_context: the surrounding document names the Five Element scan somewhere else,
    so a bare "voice scan" here is left for Glen too (a report spans many fields)."""
    out = text or ""
    for i, pat in enumerate(_SCAN_NAME_PATTERNS):
        bare = i == len(_SCAN_NAME_PATTERNS) - 1
        out = pat.sub(lambda m: _rename(m, out, bare, five_context), out)
    # "Voice Scan analysis" renamed would read "Voice Analysis analysis".
    out = re.sub(r"\bVoice Analysis\s+analysis\b", "Voice Analysis", out)
    out = re.sub(r"\bVoice Analys[ie]s\s+analyses\b", "Voice Analyses", out)
    return re.sub(r"\b([Aa])n (" + WELLNESS_SCAN + r")", r"\1 \2", out)


# ── checks ──────────────────────────────────────────────────────────────────

# Words that head an ingredient name but say nothing a client would read as a claim.
_NOT_NUTRIENTS = {
    "water", "rice", "oil", "extract", "blend", "powder", "capsule", "vegicap",
    "cellulose", "gelatin", "silica", "organic", "base", "terrain restore", "enteric",
    "fiber", "enzymes", "probiotic", "flower", "essence", "brandy", "alcohol",
}
# A title is not a sentence end: "as Dr. Glen notes" (review round 3).
_SENTENCE = re.compile(r"(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bSt\.)(?<!\bMrs\.)(?<=[.!?])\s+")
# Letters in capitals only: "vitamin D3 and a small amount" must not read "a" as A.
_VITAMIN_LIST = re.compile(
    r"\b[Vv]itamins?\s+((?:[A-K]\d{0,2}\b(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)?)+)")


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


# A one-word product name is only worth flagging when it cannot be an ordinary word:
# an internal capital, a digit or a plus (AngiogenX, OcuHeal+, 5-MTHF).
_BRANDLIKE = re.compile(r"[a-z][A-Z]|\d|\+")
# A sentence that claims what a remedy holds. "EI8 supports the balance between the
# microbiome and the liver" claims nothing about its contents.
_CLAIM = re.compile(
    r"\b(?:provid\w*|suppl(?:y|ies|ied|ying)|contain\w*|deliver\w*|offer\w*|gives?|"
    r"bring\w*|includ\w*|carr(?:y|ies)|made (?:with|from)|"
    r"ingredients?|featur\w*|combin\w*|pack\w*|blend of|formula of|with [\w\s,]{0,30}like)\b",
    re.IGNORECASE)
_BARE_CODE = re.compile(r"(?<![\w-])(B\d{1,2}|D3|K2)(?![\w-])")
_PRONOUN_START = re.compile(
    r"^\W*(?:it|its|this|these|they|their|both|the (?:formula|remedy|blend)|"
    r"this (?:formula|remedy|blend)|each)\b", re.IGNORECASE)
# Nutrients and botanicals a letter may name that no catalog label happens to lead with.
_COMMON_NUTRIENTS = {
    "quercetin", "ashwagandha", "rhodiola", "glycine", "taurine", "selenium", "iodine",
    "chromium", "boron", "lysine", "arginine", "carnitine", "omega-3", "fish oil",
    "melatonin", "lutein", "zeaxanthin", "astaxanthin", "bilberry", "ginkgo", "magnesium",
    "zinc", "iron", "calcium", "potassium", "copper", "manganese", "collagen", "probiotics",
    "dha", "epa", "msm", "honokiol", "berberine", "amygdalin", "serrapeptase", "nattokinase",
    "lumbrokinase", "bromelain", "fulvic acid", "humic acid", "apigenin", "luteolin",
    "rutin", "taurine", "carnosine", "mistletoe", "saffron",
}
_NEGATION = re.compile(r"\b(?:no|not|without|free of|free from|none)\b[^.]{0,30}$", re.IGNORECASE)


def _vitamins_in(sentence):
    sentence = re.sub(r"\bB-(\d{1,2})\b", r"B\1", sentence)      # "B-12" is B12
    found = ["Vitamin " + c for c in _BARE_CODE.findall(sentence)]
    for m in _VITAMIN_LIST.finditer(sentence):
        for letter in re.findall(r"\b[A-K]\d{0,2}\b", m.group(1)):
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
    """Word-bounded, so "vitamin b1" is not found inside "vitamin b12" (review round 3)."""
    names = _names_for(nutrient)
    return any(re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", str(i).lower())
               for i in ingredients or [] for name in names)


def _squash(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower().replace("+", "plus"))


def _is_chain_product(name, chain):
    """A catalog name is the chain's remedy under another spelling when the chain's own,
    longer name contains it: 'Clear Lens Eyedrops' in 'Clear Lens Eye Drops ACES+CAT'.
    Never the other way: a chain 'OcuHeal' does not make 'OcuHeal+ Eye Drops', or 'ES1'
    'ES15 ...', the same product (blind review, 2026-09-25). Same-product spellings the
    other way round are passed in by the caller as extra chain names."""
    n = _squash(name)
    if any(n and c and n in c for c in (_squash(x) for x in chain)):
        return True
    # Infoceuticals go by their code: "ED11 Liver Driver" is the chain's "ED11 Liver
    # Energetic Driver Infoceutical" (blind review round 2).
    code = _INFO_CODE.match(name or "")
    return bool(code) and any(_INFO_CODE.match(x or "") and
                              _INFO_CODE.match(x).group(1) == code.group(1) for x in chain)


_INFO_CODE = re.compile(r"^\s*([A-Z]{1,3}\d{1,2})\b")


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


def check_narrative(text, *, chain, ingredients, catalog_names, allowed_text="", heads_text=""):
    """Problems a reader could be misled by, as plain sentences for Glen. Empty when clean.

    chain: remedy names on the client's chain. ingredients: {remedy: [ingredient line]}.
    catalog_names: the whole catalog, so a product from outside the chain is seen.
    allowed_text: names permitted beyond the chain (its Heads and Tails, the service).
    heads_text: the chain's Head and Tail text, whose words name body areas.

    Kept deliberately narrow. Blind review round 2 measured a check built on every
    catalog label word ("Kale", "Honey", "English") flagging 13 of 42 correct sentences,
    each flag costing a paid retry. Nutrients are now a curated list plus the chain's
    own label terms, and only a sentence that CLAIMS contents ("provides", "contains")
    is checked.
    """
    text = text or ""
    problems = []
    chain_low = {c.lower() for c in chain}
    # A name that is part of a chain remedy's own label ("5-MTHF" in B17 Syntropy's
    # "Vitamin B9 (5-MTHF)") names that ingredient, not another product.
    chain_labels = " ".join(str(i) for c in chain for i in (ingredients.get(c) or [])).lower()
    allowed_low = (allowed_text or "").lower()

    # Off-chain products: matched with case, because a product name is capitalised and
    # the same word in lower case is ordinary prose ("helps the body transform").
    for name in sorted({n for n in catalog_names if n and len(n) >= 4}, key=len, reverse=True):
        if " " not in name.strip() and not _BRANDLIKE.search(name):
            continue                  # "Energy", "Sleep", "Clarity": ordinary words too
        if (_is_chain_product(name, chain) or name.lower() in allowed_low
                or name.lower() in chain_labels):
            continue
        if _names_product(name, text):
            problems.append(f"Names {name}, which is not on this client's chain.")

    heads_low = (heads_text or "").lower()
    own = set()
    for c in chain:
        for ing in ingredients.get(c) or []:
            t = _term(ing)
            short_ok = len(t) == 3 and t.isalpha() and t.isupper()     # DHA, EPA, NAC
            if ((len(t) >= 4 or short_ok) and t.lower() not in _NOT_NUTRIENTS
                    and not t.lower().startswith("vitamin") and t.lower() not in chain_low):
                own.add(t)
    # Head and Tail words name body areas: "Silymarin Terrain" is not a nutrient claim.
    terms = {t for t in own | _SYNONYM_TERMS | _COMMON_NUTRIENTS if t.lower() not in heads_low}
    term_re = re.compile(r"(?<![\w+])(" + "|".join(
        re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")(?![\w+])",
        re.IGNORECASE) if terms else None
    by_len = sorted(chain, key=len, reverse=True)
    codes = {_INFO_CODE.match(c).group(1): c for c in chain if _INFO_CODE.match(c or "")}

    last_remedies = []                        # "This remedy..." may open the next paragraph
    for para in re.split(r"\n\s*\n", text):
        for sentence in _SENTENCE.split(para):
            # "X provides selenium, while Y provides copper" is two claims, not one
            # about both (blind review round 3).
            for clause in _CLAUSE.split(sentence):
                problems += _clause_problems(clause, by_len, codes, ingredients, chain_low,
                                             term_re, last_remedies)
                named = _named_in(clause, by_len, codes)[0]
                if named:
                    last_remedies[:] = named
    return list(dict.fromkeys(problems))


_CLAUSE = re.compile(r"\s*(?:;|,?\s+while\s+|,?\s+whereas\s+|,\s+but\s+)\s*", re.IGNORECASE)
_WHICH = re.compile(r",\s+(?:which|that)\b")


def _named_in(clause, by_len, codes):
    """Chain remedies named in a clause, with their positions, and the clause with those
    names blanked out. Case-sensitive: "healthy lymph flow" is not the remedy Lymph Flow."""
    rest, found = clause, []
    for r in by_len:                           # longest first: "B17 Syntropy" before "B17"
        m = re.search(r"(?<![\w+])" + re.escape(r) + r"(?![\w+])", rest)
        if m:
            found.append((m.start(), r))
            rest = rest[:m.start()] + " " * len(r) + rest[m.end():]
    # An infoceutical is often written by its code and a short name ("ED11 Liver
    # Driver" for "ED11 Liver Energetic Driver Infoceutical").
    for code, r in codes.items():
        m = re.search(r"(?<![\w+])" + code + r"(?:\s+[A-Z][\w/-]*)*", rest)
        if r not in [x for _, x in found] and m:
            found.append((m.start(), r))
            rest = rest[:m.start()] + " " * (m.end() - m.start()) + rest[m.end():]
    found.sort()
    return [r for _, r in found], found, rest


def _clause_problems(clause, by_len, codes, ingredients, chain_low, term_re, last_remedies):
    named, positions, rest = _named_in(clause, by_len, codes)
    # Only a pronoun ("It supplies...") carries the last remedy forward; "Leafy greens
    # rich in magnesium" after a remedy sentence is not about that remedy.
    remedies = named or (list(last_remedies) if _PRONOUN_START.match(clause) else [])
    claim = _CLAIM.search(rest)
    if not remedies or not claim:
        return []
    # "X pairs with Y, which provides magnesium": the claim belongs to Y.
    which = _WHICH.search(clause)
    if which and which.start() < claim.start() and positions:
        before = [r for pos, r in positions if pos < which.start()]
        if before:
            remedies = [before[-1]]
    nutrients = _vitamins_in(rest) + ([m.group(1) for m in term_re.finditer(rest)]
                                      if term_re else [])
    nutrients = list({n.lower(): n for n in reversed(nutrients)}.values())[::-1]
    # "the MSM on layer 1" is the chain's MSM Powder, shortened, not a claim.
    nutrients = [n for n in nutrients if not _negated(n, rest)
                 and not (re.search(r"\bthe\s+" + re.escape(n) + r"\b", rest, re.IGNORECASE)
                          and any(c.startswith(n.lower() + " ") for c in chain_low))]
    out = []
    for n in nutrients:
        for r in remedies:
            listed = ingredients.get(r) or []
            if not listed:
                out.append(f"Credits {r} with {n}, but no ingredient list is on file for it.")
            elif not _contains(listed, n):
                out.append(f"Says {r} provides {n}, which is not in its formula.")
    return out
