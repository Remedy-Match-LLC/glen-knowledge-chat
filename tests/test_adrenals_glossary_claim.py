"""The Adrenals glossary entry and the claim gate, 2026-09-15.

The public glossary page /learn/glossary/organs said an adrenal remedy reduced "the need for
prescription steroids" and eased "tapering off the Rx forms", and it named Adrenal Repair:
Sublingual 7-Keto, which is not in the catalog. Glen approved replacement text, and the
remedies are now Adrenal Syntropy and Endocrine Restore Powder.
"""
import json
import re

import dashboard.product_content as pc

CATALOG = "data/clinical_theory_catalog.json"
PRODUCTS = "data/products.json"


def _adrenal():
    d = json.load(open(CATALOG, encoding="utf-8"))
    entries = [e for dim in d["dimensions"] for e in dim["entries"] if e.get("slug") == "adrenal"]
    assert len(entries) == 1
    return entries[0]


def test_the_adrenals_entry_makes_no_medication_claim():
    text = _adrenal()["description"].lower()
    for phrase in ("prescription", "steroid", "tapering", "rx forms", "reducing the need", "7-keto"):
        assert phrase not in text, phrase
    assert "adrenal syntropy" in text and "endocrine restore" in text
    # Glen, 2026-09-19: the prose uses the short names; the links carry the full ones.
    assert "endocrine restore powder" not in text


def test_the_adrenals_remedies_are_live_catalog_products():
    cat = json.load(open(PRODUCTS))["products"]
    remedies = _adrenal()["remedies"]
    # Link labels follow the 2026-09-19 sublingual renames; the prose is #1708's question.
    assert [r["name"] for r in remedies] == ["Adrenal Syntropy Sublingual Powder",
                                             "Endocrine Restore Sublingual Powder"]
    for r in remedies:
        slug = r["url"].rsplit("/", 1)[-1]
        assert slug in cat and not cat[slug].get("inactive"), slug
        assert cat[slug]["name"] == r["name"]
        assert "remedymatch.com" not in r["url"]


APPROVED_ADRENAL = (
    "Supports the adrenals' natural anti-inflammatory function and our resilience under stress. "
    "Supportive remedies: Adrenal Syntropy or Endocrine Restore. Associated emotional conflict: "
    "Self-Worth. Supportive essences: Self-Esteem Flower Essence, Worthiness Flower Essence.")

# The public glossary JSON serves every string in an entry, snapshot fields included, and the
# catalog resync moves a changed description into its snapshot. So the whole file is checked.
WITHDRAWN = re.compile(r"prescription|Rx steroid|tapering|reducing the need|Adrenal Repair|Endocrine Repair",
                       re.IGNORECASE)


def _strings(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def test_the_adrenals_description_is_glens_approved_text():
    assert _adrenal()["description"] == APPROVED_ADRENAL


def test_no_string_anywhere_in_the_catalog_carries_withdrawn_wording():
    d = json.load(open(CATALOG, encoding="utf-8"))
    strings = list(_strings(d))
    assert len(strings) > 1000  # the walk reached the entries, not an empty shell
    hits = [(p, m.group(0)) for p, s in strings for m in WITHDRAWN.finditer(s)]
    assert hits == []


def test_no_public_glossary_dimension_serves_a_description_snapshot():
    from dashboard import clinical_glossary as cg
    cat = cg.load(CATALOG)
    raw = [e for d in cat["dimensions"] for e in d["entries"]]
    assert any(k.startswith("description_snapshot") for e in raw for k in e)  # history is still in the file
    served = [cg.get_dimension(d["key"], cat) for d in cat["dimensions"]]
    assert sum(len(d["entries"]) for d in served) == len(raw)
    for d in served:
        for e in d["entries"]:
            assert not [k for k in e if k.startswith("description_snapshot")], e.get("slug")
            assert e.get("description") is not None or "description" not in raw[0]
    # the served copy is not the cached catalog: a route that decorates entries cannot write back
    served[0]["entries"][0]["patterns"] = ["x"]
    assert "patterns" not in cat["dimensions"][0]["entries"][0]


def test_the_claim_gate_catches_the_withdrawn_phrases():
    for s in ("reducing the need for prescription steroids",
              "helps with tapering off medication",
              "a prescription steroid alternative"):
        assert pc._deny_hits(s), s
    assert pc._deny_hits("supports the adrenals' natural anti-inflammatory function") == []


def test_no_glossary_prose_names_endocrine_restore_powder():
    """Glen, 2026-09-19, option 2: short names in the prose. "Endocrine Restore Powder"
    matches no product once the catalog carries FileMaker's full name."""
    d = json.load(open(CATALOG, encoding="utf-8"))
    for dim in d["dimensions"]:
        for e in dim["entries"]:
            assert "Endocrine Restore Powder" not in (e.get("description") or ""), e["slug"]


def test_no_glossary_text_names_the_old_b12_product():
    """Glen, 2026-09-19: "Vitamin B12 Sublingual Powder is the new name - update
    elsewhere", and yes to all five places in the glossary and stressor map."""
    for path in ("data/clinical_theory_catalog.json", "data/e4l_stressor_map.json"):
        text = open(path, encoding="utf-8").read()
        assert "Sublingual B12" not in text, path
        assert "B12 Sublingual Powder" in text or path.endswith("stressor_map.json"), path


def test_no_glossary_link_points_at_the_old_store():
    """Glen, 2026-09-19: "look for any links still pointing to the old store". Every
    remedymatch.com product page now 404s, and a GroovePages builder link only opens in
    the editor, so either one is a dead link in approved customer text."""
    import json as _j
    cat = _j.loads(open("data/clinical_theory_catalog.json", encoding="utf-8").read())
    bad = [(e["slug"], r.get("name"), r.get("url"))
           for dim in cat["dimensions"] for e in dim["entries"]
           for r in (e.get("remedies") or [])
           if "remedymatch.com" in (r.get("url") or "") or "groove.cm" in (r.get("url") or "")]
    assert bad == [], bad


def test_every_glossary_product_link_is_a_real_page_or_the_shop():
    """A rewritten link must name a live product, or the browse page when the old
    product is retired or the link was a search."""
    import json as _j
    cat = _j.loads(open("data/clinical_theory_catalog.json", encoding="utf-8").read())
    products = _j.loads(open("data/products.json", encoding="utf-8").read())["products"]
    for dim in cat["dimensions"]:
        for e in dim["entries"]:
            for r in (e.get("remedies") or []):
                u = r.get("url") or ""
                if "/begin/product/" in u:
                    slug = u.rsplit("/", 1)[-1]
                    assert slug in products, (e["slug"], slug)
                    assert not products[slug].get("inactive"), (e["slug"], slug)
                elif u.startswith("http"):
                    assert u.endswith("/shop") or u.startswith("/"), (e["slug"], u)
