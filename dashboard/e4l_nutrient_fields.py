"""Which E4L Nutrition Fields bar a Clinical Summary item shows the colour of.

Section items name a product form ("Magnesium glycinate"); the scan names the
nutrient ("Magnesium"). Glen, 2026-10-18: "For a compound like Mg Taurate show
colors for both Mg and Tau." So an item gets every field one of its parts names,
in the order below. An item with no field (curcumin, probiotics) gets no icon.

Field names are spelled as the scan's Nutrition Fields pages print them.
"""
import re

_FIELDS = (
    (r"\bvitamin a\b|\bretinol", "Vitamin A"),
    (r"\bvitamin c\b|\bascorb", "Vitamin C"),
    (r"\bvitamin d\d?\b|\bcholecalciferol", "Vitamin D"),
    (r"\bvitamin e\b|\btocopher", "Vitamin E"),
    (r"\bvitamin k\d?\b|\bmenaquinone|\bphylloquinone", "Vitamin K"),
    (r"\bvitamin b complex\b|\bb[- ]complex\b", "Vitamin B Complex"),
    (r"\bvitamin b1\b|\bthiamin|\bbenfotiamine", "Vitamin B1"),
    (r"\bvitamin b2\b|\briboflavin", "Vitamin B2"),
    (r"\bvitamin b3\b|\bniacin|\bnicotinamide|\bnicotinic", "Vitamin B3"),
    (r"\bvitamin b5\b|\bpantothen", "Vitamin B5"),
    (r"\bvitamin b6\b|\bpyridox", "Vitamin B6"),
    (r"\bvitamin b7\b|\bbiotin", "Vitamin B7"),
    (r"\bvitamin b8\b|\binositol", "Vitamin B8"),
    (r"\bvitamin b9\b|\bfolate|\bfolic|\bmethyltetrahydrofolate", "Vitamin B9"),
    (r"\bvitamin b12\b|\bcobalamin", "Vitamin B12"),
    (r"\bbetaine\b|\btrimethylglycine\b|\btmg\b", "Betaine / TMG"),
    (r"\bcholine\b|\bphosphatidylcholine\b", "Choline"),
    (r"\bboron\b", "Boron"),
    (r"\bcalcium\b", "Calcium"),
    (r"\bchromium\b", "Chromium"),
    (r"\bcobalt\b", "Cobalt"),
    (r"\bcopper\b", "Copper"),
    (r"\biodine\b|\biodide\b", "Iodine"),
    (r"\biron\b", "Iron"),
    (r"\bmagnesium\b", "Magnesium"),
    (r"\bmanganese\b", "Manganese"),
    (r"\bmolybdenum\b", "Molybdenum"),
    (r"\bphosphorus\b", "Phosphorus"),
    (r"\bpotassium\b", "Potassium"),
    (r"\bselenium\b", "Selenium"),
    (r"\bsilica\b|\bsilicon\b", "Silica"),
    (r"\bsodium\b|\bsea salt\b", "Sodium"),
    (r"\bzinc\b", "Zinc"),
    (r"\bala\b|\balpha-linolenic", "Omega 3 - ALA"),
    (r"\bepa\b|\beicosapentaen", "Omega 3 - EPA"),
    (r"\bdha\b|\bdocosahexaen", "Omega 3 - DHA"),
    (r"\bgla\b|\bgamma-linolenic", "Omega 6 - GLA"),
    (r"\bconjugated linoleic\b|\bcla\b", "Conjugated Linoleic Acid (CLA)"),
    (r"\bhistidine\b", "Histidine"),
    (r"\bisoleucine\b", "Isoleucine"),
    (r"(?<!iso)\bleucine\b", "Leucine"),
    (r"\blysine\b", "Lysine"),
    (r"\bmethionine\b", "Methionine"),
    (r"\bphenylalanine\b", "Phenylalanine"),
    (r"\bthreonine\b", "Threonine"),
    (r"\btryptophan\b", "Tryptophan"),
    (r"\bvaline\b", "Valine"),
    (r"\barginine\b", "Arginine"),
    # NAC counts as cysteine (Glen approved the map, 2026-10-18).
    (r"\bcysteine\b|\bacetylcysteine\b|\bnac\b", "Cysteine"),
    (r"\bglutamine\b", "Glutamine"),
    (r"\bglycine\b|\bglycinate\b|\bbisglycinate\b", "Glycine"),
    (r"\bproline\b", "Proline"),
    (r"\bserine\b", "Serine"),
    (r"\btyrosine\b", "Tyrosine"),
    (r"\bcarnitine\b", "Carnitine"),
    (r"\bcarnosine\b", "Carnosine"),
    (r"\bcitrulline\b", "Citrulline"),
    (r"\bcreatine\b", "Creatine"),
    (r"\bglutamic acid\b|\bglutamate\b", "Glutamic Acid"),
    (r"\bornithine\b", "Ornithine"),
    (r"\btaurine\b|\btaurate\b", "Taurine"),
    (r"\bco-?enzyme q10\b|\bcoq10\b|\bubiquin", "Co-Enzyme Q10"),
    (r"(?<!-)\bglutathione\b(?! peroxidase)", "Glutathione"),
    (r"\bsuperoxide dismutase\b|\bsod\b", "Superoxide Dismutase (SOD))"),
)
_COMPILED = tuple((re.compile(p, re.I), field) for p, field in _FIELDS)


def fields_for(name):
    """The scan fields `name` shows colours for, in map order, without repeats."""
    text = str(name or "")
    return [field for pattern, field in _COMPILED if pattern.search(text)]

