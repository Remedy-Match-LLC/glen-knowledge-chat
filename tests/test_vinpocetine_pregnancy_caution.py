"""Every product whose formula carries vinpocetine gets a pregnancy line on its page and in chat.

The FDA advised on 3 June 2019 "pregnant women and women who could become pregnant not to
take vinpocetine". Glen said yes on 28 October to a line on every such page, approved the
wording on 30 October (formulation tab) and the write the same day (platform tab). Spec:
production/05 Formulations/_store-updates/2026-10-28-vinpocetine-pregnancy-caution.md.
"""
import json
import os
import shutil

import pytest

import app

ROOT = os.path.join(os.path.dirname(__file__), "..")
LINE = ("This formula contains vinpocetine, so do not use it if you are pregnant or could "
        "become pregnant.")
SOLO = "Do not use vinpocetine if you are pregnant or could become pregnant."
# Brain Boost left the list: its July 2026 label has no vinpocetine, and no older bottle is
# for sale (Glen, 30 October, "no"; spec 2026-10-30-brain-boost-one-record).
OWN_ROW = ["bioavailability-blend", "bioavailability-blend-powder",
           "heart-health", "iron-out", "iron-syntropy", "longevity", "melatonin-max",
           "migrafree", "muscle-mass", "one-step", "seaamino-syntropy", "stone-solvent",
           "sustain", "vagus-nerve-support"]
BLEND_ROW = ["acetaldehyde-detox", "apoptogenesis", "appestat", "blood-cleanse",
             "c15-syntropy", "c15-syntropy-pentadecanoic-acid", "curcu-guard", "dht-blocker",
             "lens-zyme", "magnesium-syntropy", "mitochondrial-biogenesis", "night-vision",
             "perfect-skin", "rescue", "rescue-phytochromotherapy", "rise--shine",
             "thermogenesis", "three-treasures", "tmg-syntropy-powder", "vein-support",
             "vital-energy-be", "vitamin-c-syntropy"]
DESCRIPTION_ONLY = ["nerve-repair", "nerve-pulse"]
# Their earlier warning stays first, then one space and the line.
APPENDED = {"iron-out": "Take up to 4 capsules a day.",
            "sleep-syntropy": None, "vitality": None}
# A later caution line follows the vinpocetine line (iodine, Glen "yes" 2026-10-31).
FOLLOWED = {"thermogenesis": (
    "Each capsule contains about 150 mcg of iodine, the adult daily requirement. If you have a "
    "thyroid condition or take other iodine products, consult your health professional before "
    "use.")}
# Its label's Bioavailability Factors list vinpocetine, while the app rows wait on the panel
# spec (formulation, 2026-10-31-macular-wellness-safranal-contents).
LABEL_ONLY = ["macular-wellness-safranal"]
ALL = (OWN_ROW + BLEND_ROW + ["vinpocetine"] + DESCRIPTION_ONLY + ["sleep-syntropy", "vitality"]
       + LABEL_ONLY)
# Blood Cleanse's store caution v2 opens with its own vinpocetine sentence and adds
# breastfeeding (marketing/04 Copy/blood-cleanse-niacin-2026-10-31/change-v2.md).
REPLACED = {"blood-cleanse": (
    "This formula contains vinpocetine. Do not use if you are pregnant, could become "
    "pregnant, or are breastfeeding. It is not for children. Consult your physician before "
    "use if you take any medicine, including aspirin or other pain relievers. This matters "
    "most with blood pressure, heart, blood thinning, blood sugar, cholesterol or "
    "immune-suppressing medicines. Also consult first if you have liver disease, gout, a "
    "stomach ulcer, low blood pressure, a bleeding disorder, an autoimmune condition or a "
    "seizure disorder, or are planning surgery. Niacin may cause a temporary flush. Stop use "
    "and consult your physician if you notice yellowing of the skin or eyes, or dark urine. "
    "This formula may change blood pressure and blood sugar, affect bleeding, and change how "
    "some medications are processed.")}
EVERYDAY = {"longevity": "Longevity", "heart-health": "Heart Health",
            "muscle-mass": "Muscle Mass", "sustain": "Sustain", "one-step": "One Step",
            "rescue": "Rescue", "night-vision": "Night Vision", "perfect-skin": "Perfect Skin",
            "vein-support": "Vein Support", "three-treasures": "Three Treasures",
            "thermogenesis": "Thermogenesis", "rise--shine": "Rise & Shine",
            "mitochondrial-biogenesis": "Mitochondrial Biogenesis",
            "vital-energy-be": "Vital Energy Be"}


def _data(name):
    return json.load(open(os.path.join(ROOT, "data", name)))


def _expected(slug, before=None):
    line = SOLO if slug == "vinpocetine" else LINE
    return f"{before} {line}" if before else line


def _products():
    return _data("products.json")["products"]


def test_the_list_is_the_spec_list():
    assert len(ALL) == len(set(ALL)) == 42


@pytest.mark.parametrize("slug", ALL)
def test_each_record_and_its_correction_carry_the_line(slug):
    p, c = _products()[slug], _data("products-manual-corrections.json")[slug]
    assert p["chat_caution"] is True and c["chat_caution"] is True
    assert c["warning"] == p["warning"]
    if slug in APPENDED:
        before = APPENDED[slug] or p["warning"][:-len(LINE) - 1]
        assert before and p["warning"] == _expected(slug, before)
    elif slug in FOLLOWED:
        assert p["warning"] == _expected(slug) + " " + FOLLOWED[slug]
    elif slug in REPLACED:
        assert p["warning"] == REPLACED[slug]
    else:
        assert p["warning"] == _expected(slug)


def test_a_new_correction_keeps_the_record_source():
    """apply_corrections() writes "manual" when an entry has no source, and that clears
    gk_stale: sustain would lose it (spec, Catalogue fields). Every other new entry is
    checked against main() in the full enrichment run below."""
    corr, products = _data("products-manual-corrections.json"), _products()
    assert corr["sustain"]["ingredients_source"] == products["sustain"]["ingredients_source"]
    assert products["sustain"]["gk_stale"] is True
    assert "ingredients_source" not in corr["vinpocetine"]


@pytest.mark.parametrize("slug", ["vitreous-vitality", "vitamin-a-syntropy"])
def test_the_two_that_already_cover_pregnancy_are_unchanged(slug):
    assert "vinpocetine" not in _products()[slug]["warning"]


def test_brain_program_no_longer_lists_brain_boost():
    # Glen, 30 October: no older Brain Boost bottle is for sale ("no").
    for d in (_products(), _data("products-manual-corrections.json")):
        assert d["brain-program"]["caution_components"] == ["brain-cleanse"]
        assert "warning" not in d["brain-boost"] and d["brain-boost"]["chat_caution"] is False


@pytest.mark.parametrize("slug", ["bioavailability-blend", "curcu-guard", "migrafree",
                                  "iron-out", "vinpocetine"])
def test_the_page_data_shows_the_line(slug):
    d = app.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["caution"]["body"] == _products()[slug]["warning"]


@pytest.mark.parametrize("slug,component", [("sleep-bundle", "sleep-syntropy")])
def test_a_bundle_shows_its_component_line(slug, component):
    d = app.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs[f"caution-{component}"]["body"] == _products()[component]["warning"]
    assert LINE in secs[f"caution-{component}"]["body"]


def _named(q):
    return set(app._named_product_spans(q, app._PRODUCTS["products"]))


@pytest.mark.parametrize("q,slug", [
    ("Is Curcu Guard safe in pregnancy?", "curcu-guard"),
    ("Can I take Bioavailability Blend while pregnant?", "bioavailability-blend"),
    ("Is vinpocetine safe when pregnant?", "vinpocetine"),
    ("Is Heart Health safe in pregnancy?", "heart-health"),
])
def test_a_pregnancy_question_links_the_line(q, slug):
    assert _named(q) == {slug}
    assert slug in app._caution_link_slugs(q)
    assert f"### {app._PRODUCTS['products'][slug]['name']}" in app.named_product_facts_block(q)


def test_iron_out_keeps_its_directions():
    b = app._named_product_facts("How do I take Iron Out?")
    assert "Directions: 1 to 4 capsules daily with food, or as guided." in b


@pytest.mark.parametrize("q", [
    "Longevity tips?", "Heart health tips?", "Muscle mass after 60?", "Sustain energy?",
    "Is Rescue Remedy safe for my dog?", "Rescue Remedy dose for anxiety?",
])
def test_an_everyday_phrase_brings_no_product(q):
    assert _named(q) == set()
    assert not app._caution_link_slugs(q)


@pytest.mark.parametrize("slug,name", sorted(EVERYDAY.items()))
def test_a_capitalised_sentence_start_needs_a_product_cue(slug, name):
    for q in (f"{name} tips?", f"{name} matters more after 60. What helps?",
              f"How do I care for my health? {name} is my goal."):
        assert slug not in _named(q), q
    for q in (f"Is {name} safe in pregnancy?", f"Can I take {name} while pregnant?",
              f"{name} dose?"):
        assert slug in _named(q), q
    assert slug in app._caution_link_slugs(f"Can I take {name} while pregnant?")


@pytest.mark.parametrize("q,slug", [
    ("can i take heart health while pregnant?", "heart-health"),
    ("is sustain safe in pregnancy?", "sustain"),
    ("is c15 syntropy safe while pregnant?", "c15-syntropy"),
    ("can i take iron out if i might become pregnant", "iron-out"),
    ("is vitality ok while breastfeeding?", "vitality"),
    ("longevity side effects when trying to conceive?", "longevity"),
    ("Heart Health while pregnant?", "heart-health"),
])
def test_a_pregnancy_or_safety_question_counts_in_any_case(q, slug):
    """Review rounds 1 and 2: lowercase pregnancy questions got no caution. Formulation,
    2026-10-30: any case counts beside a pregnancy or safety cue."""
    assert slug in _named(q), q
    assert slug in app._caution_link_slugs(q), q


@pytest.mark.parametrize("q", [
    "what is longevity?", "how much muscle mass should I have?", "longevity tips?",
    "heart health tips for seniors", "how do i sustain energy all day?",
    "one step at a time", "rescue remedy safe in pregnancy?",
    "What Foods Support Heart Health?",
    "is a blood cleanse safe in pregnancy?", "is a heart health diet safe when pregnant?",
    "is a      heart health diet safe when pregnant?", "an\n\nheart health plan, safe?",
])
def test_a_lowercase_or_title_cased_everyday_phrase_brings_no_product(q):
    assert not (_named(q) & set(ALL)), q


def test_a_safety_phrase_with_an_everyday_name_is_recorded():
    """Accepted by formulation, 2026-10-30: the extra caution block is the cheaper error."""
    assert "muscle-mass" in _named("how much muscle mass is safe while pregnant?")


def test_a_long_near_miss_returns_quickly():
    """Review round 2: an overlapping whitespace pattern backtracked quadratically."""
    import time
    t = time.monotonic()
    app._named_product_spans("heart health breast" + " " * 60000 + "x",
                             app._PRODUCTS["products"])
    assert time.monotonic() - t < 1.5


def test_rescue_yields_to_the_flower_essence_by_its_full_name(monkeypatch):
    """Round 3: with _YIELDS_BEFORE in place this cannot tell which guard held, so it is
    switched off here."""
    monkeypatch.setattr(app, "_YIELDS_BEFORE", {})
    assert "rescue" not in _named("Is Rescue Remedy Flower Essence in Terrain Restore safe?")


@pytest.mark.parametrize("q", ["Can I take Brain Boost while pregnant?",
                               "is brain boost safe while pregnant?"])
def test_brain_boost_brings_its_facts_and_no_vinpocetine_line(q):
    # Glen, 30 October: no older bottle is for sale ("no"). The pinned label facts and the
    # no-caution text answer instead (spec 2026-10-30-brain-boost-one-record).
    assert _named(q) == {"brain-boost"}
    assert "brain-boost" not in app._caution_link_slugs(q)
    b = app.named_product_facts_block(q)
    assert "### Brain Boost" in b and "vinpocetine" not in b.lower()


def test_brain_boost_nootropic_is_brain_boost():
    # "Brain Boost Nootropic" is the retired name of the same product (Glen, 30 October).
    assert _named("What is Brain Boost Nootropic?") == {"brain-boost"}
    assert _named("What is Brain Boost?") == {"brain-boost"}


def test_a_later_standalone_rescue_still_counts():
    assert "rescue" in _named("Is Rescue Remedy like Rescue? Can I take Rescue while pregnant?")


def test_lowercase_vinpocetine_counts():
    assert _named("can i take vinpocetine when pregnant") == {"vinpocetine"}


def test_a_full_enrichment_run_twice_keeps_every_line(tmp_path, monkeypatch):
    """Spec read-back 5: main() twice on a copy, warning and chat_caution stripped first.
    ingredients_source and gk_stale match a control run without the new correction
    entries, so those entries change neither."""
    import scripts.apply_enrichment as ae
    names = ("products-enrich-clean.json", "products.json", "products-manual-corrections.json")

    def run(where, strip, drop_new=False):
        where.mkdir()
        for f in names:
            src = os.path.join(ROOT, "data", f)
            if os.path.exists(src):
                shutil.copy(src, where / f)
        monkeypatch.setattr(ae, "CLEAN", str(where / names[0]))
        monkeypatch.setattr(ae, "PRODUCTS", str(where / names[1]))
        monkeypatch.setattr(ae, "CORRECTIONS", str(where / names[2]))
        if drop_new:
            # The control: no new correction entry, so main() alone sets source and flags.
            corr = json.load(open(where / names[2]))
            for s in ALL:
                if set(corr.get(s, {})) <= {"ingredients_source", "warning", "chat_caution"}:
                    corr.pop(s, None)
            json.dump(corr, open(where / names[2], "w"))
        if strip:
            cat = json.load(open(where / names[1]))
            for s in ALL:
                cat["products"][s].pop("warning")
                cat["products"][s].pop("chat_caution")
            json.dump(cat, open(where / names[1], "w"))
        outs = []
        for _ in range(2):
            ae.main()
            outs.append(json.load(open(where / names[1]))["products"])
        return outs

    repo_bytes = open(os.path.join(ROOT, "data", "products.json"), "rb").read()
    control = run(tmp_path / "control", strip=False, drop_new=True)[-1]
    want = _products()
    for out in run(tmp_path / "stripped", strip=True):
        for s in ALL:
            assert out[s]["warning"] == want[s]["warning"], s
            assert out[s]["chat_caution"] is True, s
            if s != "vinpocetine":
                for k in ("ingredients_source", "gk_stale"):
                    assert out[s].get(k) == control[s].get(k), (s, k)
        assert out["vinpocetine"]["ingredients_source"] == "manual"
    assert open(os.path.join(ROOT, "data", "products.json"), "rb").read() == repo_bytes
