"""NeurOmega is WholOmega's old name (Glen, 2026-10-07). The store copy, atlas and
Clinical Theory catalogue use the new name. The alias stays on purpose: an alias on
`wholomega` would split the old name between the $70 and $190 sizes."""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    return json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))


@pytest.mark.parametrize("name", ["products.json", "atlas-concepts.json", "atlas-seed-input.json",
                                  "atlas-pending.json", "clinical_theory_catalog.json"])
def test_the_old_name_is_gone(name):
    assert "NeurOmega" not in json.dumps(_load(name), ensure_ascii=False)


@pytest.mark.parametrize("slug", ["wholomega", "wholomega-120-capsules"])
def test_the_description_opens_on_the_formula_not_a_name_run(slug):
    d = _load("products.json")["products"][slug]["description"]
    assert d.startswith("WholOmega delivers DHA"), d[:60]
    assert "120 capsules" not in d[:40]


def test_the_atlas_concept_keeps_its_id_and_product_link():
    c = next(c for c in _load("atlas-concepts.json")["concepts"] if c["id"] == "neuromega")
    assert c["label"] == "WholOmega"
    assert [l.get("title") for l in c["links"] if l.get("type") == "product"] == ["WholOmega"]


def test_the_catalogue_remedy_links_the_product_page():
    raw = json.dumps(_load("clinical_theory_catalog.json"))
    assert '"name": "WholOmega", "url": "https://myhealingoasis.com/begin/product/wholomega"' in raw


def test_the_old_name_alias_still_points_at_the_120_size():
    raw = json.dumps(_load("product-aliases.json"))
    assert '"NeurOmega"' in raw and "wholomega-120-gelcaps" in raw
