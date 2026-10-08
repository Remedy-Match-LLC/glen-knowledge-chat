"""Glen 2026-10-08: an enteric capsule that is not phthalate-free fails; gelatin fails.

Phthalate coatings are red. DRcaps (hypromellose, gellan gum), which Glen's own enteric products use,
stay green. Shellac is not covered by the ruling, so it is not red. "CAP" is not an alias, because
it would catch "veggie cap".
"""
import pytest

from dashboard import purity_avoidlist as pa
from dashboard import purity_screen as ps

AL = pa.load_avoidlist()


def _color(*other):
    return ps.screen_label([], list(other), AL)["color"]


@pytest.mark.parametrize("label", [
    "Hypromellose phthalate",
    "Hydroxypropyl methylcellulose phthalate (HPMCP)",
    "HPMCP",
    "Cellulose acetate phthalate",
    "Cellulose-Acetate-Phthalate enteric coating",
    "Polyvinyl acetate phthalate",
    "enteric coating (phthalate)",
])
def test_phthalate_coatings_fail(label):
    assert _color(label) == "red"


@pytest.mark.parametrize("label", [
    "DRcaps (hypromellose, gellan gum)",
    "Hypromellose",
    "Gellan gum",
    "Phthalate-free enteric capsule (hypromellose)",
    "veggie cap",
    "Shellac",
])
def test_these_do_not_fail_on_phthalate(label):
    assert _color(label) == "green"


def test_gelatin_still_fails():
    assert _color("Gelatin capsule") == "red"
    assert _color("Bovine gelatin") == "red"


def test_version_bumped():
    assert AL["version"] == "2026-10-08"


# Glen 2026-10-08 follow-up: an enteric capsule whose label does not say phthalate-free fails until the
# maker confirms. The exemption is read across the whole label, because the splitter cuts inside brackets.
from dashboard.purity_acquire import split_other_ingredients


def _label(line):
    return ps.screen_label([], split_other_ingredients(line), AL)


@pytest.mark.parametrize("line", [
    "Other ingredients: Enteric capsule (hypromellose), rice flour",
    "Delayed-release vegetable capsule",
    "Acid-resistant capsule, microcrystalline cellulose",
    "Enteric coating",
])
def test_enteric_without_phthalate_free_fails(line):
    r = _label(line)
    assert r["color"] == "red" and r["red_hits"]


@pytest.mark.parametrize("line", [
    "Other ingredients: Phthalate-Free Enteric Vegicaps, Healing Energy, Love and Prayer",
    "Phthalate-free enteric vegicaps",
    "DRcaps (hypromellose, gellan gum)",
    "Hypromellose capsule, rice bran",
])
def test_phthalate_free_or_drcaps_enteric_passes(line):
    assert _label(line)["color"] == "green"


def test_glens_drcaps_paragraph_screens_clean():
    para = ("Each capsule is a phthalate-free DRcaps delayed-release vegicap, made of hypromellose and "
            "gellan gum")
    assert _label(para)["color"] == "green"


def test_a_stated_phthalate_still_fails_even_on_an_enteric_label_that_says_drcaps():
    assert _label("DRcaps, hypromellose phthalate")["color"] == "red"


@pytest.mark.parametrize("line", [
    "Delayed-release capsule (hypromellose, gellan gum)",      # gellan alone proves nothing
    "Enteric capsule (hypromellose), contains no gellan gum",
    "Enteric capsule (not DRcaps)",
    "DRcaps outer capsule, enteric inner capsule (hypromellose)",  # exemption stays in its own group
])
def test_an_exemption_must_be_affirmed_and_belong_to_the_same_capsule(line):
    assert _label(line)["color"] == "red"
