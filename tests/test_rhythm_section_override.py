""""Rhythm Section" is Heart Health's retired name, never Rhythm Restore.

2026-09-28: data/clinical_remedy_overrides.json sent the glossary name "Rhythm Section" to
rhythm-restore, a different product. Glen, 2026-09-27: "Rhythm section was an older name, no
longer used", and no alias is kept because Rhythm Restore exists. So the entry goes: the name
now shows as plain text rather than linking to the wrong product."""
import json
import os

from dashboard import clinical_glossary as cg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_rhythm_section_never_links_to_rhythm_restore():
    with open(os.path.join(ROOT, "data", "products.json")) as f:
        raw = json.load(f)
    products = raw if isinstance(raw, dict) else {p["slug"]: p for p in raw if isinstance(p, dict)}
    idx = cg.product_name_index(products)
    overrides = cg.load_overrides(os.path.join(ROOT, "data", "clinical_remedy_overrides.json"))
    assert "Rhythm Section" not in overrides
    assert cg.remedy_product_slug("Rhythm Section", idx, overrides) != "rhythm-restore"
