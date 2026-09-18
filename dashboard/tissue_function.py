"""What each tissue DOES — the function axis for grouping causal-chain layers.

Glen, 2026-09-16: "ER is organ/tissue related, so that also relates to the functions
of that organ/tissue. Similar for ED, EI, ES: look at the related tissues and
functions." And: "detox cuts across multiple organs in different systems."

This is the axis that assembles a layer ACROSS body systems. Detoxification reaches
kidney (urinary), liver (digestive), lymph (immune) and skin (integumentary): four
systems, one function. Location alone can never group those.

Distinct from phase. Only the ET terrains are directly phase related (Glen,
2026-09-16), and a phase describes the CASE's terrain rather than any one finding.

A tissue may serve several functions, and should: the liver both digests and clears.

RULED by Glen on 2026-09-17: "map looks good".

This is the copy the Intake app reads. `02 Skills/tissue_function_map.py` in the vault
is the same content and was where it was drafted; keep them in step by hand, or delete
the vault copy once nothing there imports it. Derived by Claude, reviewed by him.
The field, emotional and cellular findings (MB, MR, BFA and the abstract ES stars)
carry no tissue and are deliberately left out; Glen: "ungrouped for now".
"""

TISSUE_FUNCTIONS = {
    # --- Detoxification and elimination -------------------------------------
    "kidney": ("detoxification", "fluid-balance"),
    "kidneys": ("detoxification", "fluid-balance"),
    "bladder": ("detoxification", "fluid-balance"),
    "lymphatics - bladder": ("detoxification", "drainage"),
    "liver": ("detoxification", "digestion"),
    "microbes - liver": ("detoxification", "immunity"),
    "blood field - gallbladder": ("detoxification", "digestion"),
    "gall bladder": ("detoxification", "digestion"),
    "digestive ducts": ("detoxification", "digestion"),
    "skin": ("detoxification", "barrier"),
    "large intestine": ("detoxification", "digestion"),
    "neurosensory - large intestine": ("detoxification", "digestion"),
    "rectum": ("detoxification", "digestion"),
    "heavy metals detox": ("detoxification",),
    # --- Drainage and immunity ----------------------------------------------
    "lymph": ("drainage", "immunity"),
    "groin lymph": ("drainage", "immunity"),
    "spleen": ("immunity", "drainage"),
    "shock - spleen": ("immunity",),
    "tonsils": ("immunity", "barrier"),
    "appendix": ("immunity", "digestion"),
    "bone marrow - stomach": ("immunity", "digestion"),
    "immunity": ("immunity",),
    "auto-immune": ("immunity",),
    # --- Digestion -----------------------------------------------------------
    "stomach": ("digestion",),
    "oesophagus": ("digestion",),
    "duodenum": ("digestion",),
    "jejunum": ("digestion",),
    "mucous membranes - small intestine": ("digestion", "barrier"),
    "pancreas": ("digestion", "metabolic-regulation"),
    "peritoneum": ("digestion", "barrier"),
    # --- Respiration and barrier ---------------------------------------------
    "lung": ("respiration",),
    "lungs": ("respiration",),
    "heart - lung": ("respiration", "circulation"),
    "bronchial": ("respiration",),
    "bronchial mucosa": ("respiration", "barrier"),
    "larynx": ("respiration", "expression"),
    "larynx mucosa": ("respiration", "barrier"),
    "pharynx": ("respiration", "barrier"),
    "paranasal sinuses": ("respiration", "drainage"),
    "diaphragm": ("respiration", "structure"),
    # --- Circulation ----------------------------------------------------------
    "heart": ("circulation",),
    "heart imprinter": ("circulation",),
    "myocardia": ("circulation",),
    "coronary vascular": ("circulation",),
    "circulation": ("circulation",),
    "circulation - heart protector": ("circulation",),
    "av nodes": ("circulation", "regulation"),
    "pericardium": ("circulation", "barrier"),
    "neurotransmitters - heart": ("circulation", "signalling"),
    # --- Endocrine and regulation ---------------------------------------------
    "adrenals": ("endocrine-regulation", "stress-response"),
    "thyroid": ("endocrine-regulation", "metabolic-regulation"),
    "thyroid - triple burner": ("endocrine-regulation", "metabolic-regulation"),
    "parathyroid": ("endocrine-regulation", "mineral-regulation"),
    "hypothalamus": ("endocrine-regulation", "regulation"),
    "pineal gland": ("endocrine-regulation", "regulation"),
    "cell metabolism": ("metabolic-regulation",),
    "cell": ("metabolic-regulation",),
    # --- Reproduction ----------------------------------------------------------
    "ovaries": ("reproduction", "endocrine-regulation"),
    "uterus": ("reproduction",),
    "breasts": ("reproduction",),
    "prostate": ("reproduction",),
    "testes": ("reproduction", "endocrine-regulation"),
    "female": ("reproduction",),
    # --- Nervous and sensory ----------------------------------------------------
    "brain": ("signalling",),
    "nerve": ("signalling",),
    "cranial nerves": ("signalling",),
    "sciatic nerve": ("signalling",),
    "trigeminal nerve": ("signalling",),
    "acoustic nerve": ("signalling", "sensing"),
    "midbrain": ("signalling", "regulation"),
    "sensory cortex": ("sensing", "signalling"),
    "eyes": ("sensing",),
    "retina": ("sensing",),
    "hearing & learning": ("sensing",),
    "vision & decisions": ("sensing",),
    "sensing & controlling": ("sensing", "regulation"),
    # --- Structure ---------------------------------------------------------------
    "bone": ("structure",),
    "muscle": ("structure", "movement"),
    "atlas": ("structure",), "skull": ("structure",), "scapula": ("structure",),
    "pelvis": ("structure",), "tailbone": ("structure",),
    "cervical spine": ("structure",), "thoracic spine": ("structure",),
    "lumbar spine": ("structure",),
    "ankle joints": ("structure", "movement"), "elbow joints": ("structure", "movement"),
    "knee joints": ("structure", "movement"), "hip joint": ("structure", "movement"),
    "shoulder joint": ("structure", "movement"), "shoulder & arm": ("structure", "movement"),
    "wrists": ("structure", "movement"), "fingers": ("structure", "movement"),
    "toes": ("structure", "movement"), "feet": ("structure", "movement"),
    "teeth": ("structure", "digestion"), "jaw/tmj": ("structure", "movement"),
}


def functions_for(name):
    """Every function a tissue serves, as a tuple. Empty when it is not a tissue."""
    n = str(name or "").strip().lower()
    if n in TISSUE_FUNCTIONS:
        return TISSUE_FUNCTIONS[n]
    for part in n.split(" - "):          # EI pairs: "Microbes - Liver"
        if part.strip() in TISSUE_FUNCTIONS:
            return TISSUE_FUNCTIONS[part.strip()]
    return ()
