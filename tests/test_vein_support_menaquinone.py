"""Vein Support names its vitamin K2 as Menaquinone-7, not Menadione.

Menadione is vitamin K3. Vein Support uses MK-7, a form of K2, and FileMaker raw 4264 says
"Menaquinone-7". The store text was scraped from a label that said "Menadione".
Glen approved the change on 2026-10-18 ("2. yes"). Production spec, sha256 prefix
e411cbbcf6d3 (b73e3a53694b before its Status line changed): production/05 Formulations/
_store-updates/2026-10-18-vein-support-menaquinone.md.

The corrections entry carries the description so a rerun of apply_enrichment, or a
regenerated enrich file, cannot bring the old word back.
"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SLUG = "vein-support"
OLD_PHRASE = "Vitamin K2 (MK-7) (Menadione) 40 mcg 33% (from 3.1 mg)"
NEW_PHRASE = "Vitamin K2 (MK-7) (Menaquinone-7) 40 mcg 33% (from 3.1 mg)"
FILES = ("products.json", "products-enrich-clean.json", "products-enrich-candidate.json",
         "products-manual-corrections.json")

# The whole strings as they stood on main (66e8dfb7) before the change.
OLD_PRODUCTS_DESC = (
    "Vein Support . Vein Support Vein Support Vein Support formulation notes: Minerals: Magne"
    "sium (from Magnesium Ascorbate) 2 mg 0.5% Bamboo Silica (Bambusa spp.) 1 mg Vitamins: Vi"
    "tamin C (Magnesium Ascorbate) 30 mg 33% (from 32 mg) Vitamin K2 (MK-7) (Menadione) 40 mc"
    "g 33% (from 3.1 mg) Botanicals: Cayenne ( Capsicum annuum ) 50 mg Cilantro juice powder "
    "( Coriandrum sativum ) 80 mg Ketones: Red Rasperry Ketones 98% ( Rubus idaeus ) 35 mg Or"
    "ganosulfur: Garlic Allicin 25% ( Allium sativa ) 5 mg Polyphenols: Bilberry Anthocyanidi"
    "ns 25% ( Vaccinium myrtillus ) 20 mg Turmeric Curcumin &..."
)

OLD_ENRICH_DESC = (
    "Vein Support (remedies > syntropy). Vein Support Vein Support Vein Support formulation n"
    "otes: Minerals: Magnesium (from Magnesium Ascorbate) 2 mg 0.5% Bamboo Silica (Bambusa sp"
    "p.) 1 mg Vitamins: Vitamin C (Magnesium Ascorbate) 30 mg 33% (from 32 mg) Vitamin K2 (MK"
    "-7) (Menadione) 40 mcg 33% (from 3.1 mg) Botanicals: Cayenne ( Capsicum annuum ) 50 mg C"
    "ilantro juice powder ( Coriandrum sativum ) 80 mg Ketones: Red Rasperry Ketones 98% ( Ru"
    "bus idaeus ) 35 mg Organosulfur: Garlic Allicin 25% ( Allium sativa ) 5 mg Polyphenols: "
    "Bilberry Anthocyanidins 25% ( Vaccinium myrtillus ) 20 mg Turmeric Curcumin & Tetrahydro"
    "curcumin ( Curcuma longa ) 20 mg Citrus Hesperiden Methyl Chalcone (HMC) ( Citrus aurant"
    "ium ) 20 mg Pine bark OPCs ( Pinus maritima ) 5 mg Grape seed OPCs ( Vitis vinifera ) 5 "
    "mg Quercetin Dihydrate ( Sophora japonica ) 50 mg Rutin (botanical source) 5 mg Grape Tr"
    "ans-Resveratrol ( Vitis vinifera ) 25 mg Hawthorn Vitexin 2% ( Crataegus oxyacantha ) 15"
    " mg Saponins: Horse chestnut Aes\nVein Support (remedies > syntropy). ) - Chrysanthemum ("
    "Cinchona officinalis) - Cinchona bark (Equisetum arvensis) - Horsetail (Krameria lappace"
    "a) - Rhatany root (Myrica cerifera) - Bayberry (Origanum…"
)

NEW_PRODUCTS_DESC = OLD_PRODUCTS_DESC.replace("(Menadione)", "(Menaquinone-7)")
NEW_ENRICH_DESC = OLD_ENRICH_DESC.replace("(Menadione)", "(Menaquinone-7)")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def _vein(name, data=None):
    data = _load(name) if data is None else data
    return data["products"][SLUG] if name == "products.json" else data[SLUG]


def test_the_old_strings_hold_the_word_exactly_once():
    for old in (OLD_PRODUCTS_DESC, OLD_ENRICH_DESC):
        assert old.count("Menadione") == 1
        assert old.count(OLD_PHRASE) == 1


def test_each_description_is_the_old_one_with_one_substitution():
    # Whole-string comparisons, so a slip such as 40 mcg becoming 400 mcg fails.
    assert NEW_PRODUCTS_DESC == OLD_PRODUCTS_DESC.replace(OLD_PHRASE, NEW_PHRASE, 1)
    assert _vein("products.json")["description"] == NEW_PRODUCTS_DESC
    assert _vein("products-enrich-clean.json")["description"] == NEW_ENRICH_DESC
    assert _vein("products-enrich-candidate.json")["description"] == NEW_ENRICH_DESC
    assert _vein("products-manual-corrections.json")["description"] == NEW_PRODUCTS_DESC


def test_no_file_says_menadione():
    for name in FILES:
        with open(os.path.join(DATA, name), encoding="utf-8") as fh:
            raw = fh.read()
        assert "menadione" not in raw.lower(), name


def _flatten(obj, prefix=()):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out.update(_flatten(v, prefix + (k,)))
        return out or {prefix: {}}
    return {prefix: obj}


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def _introducing_versions(name):
    """(before, after) of the commit that brought in the new phrase; the working tree if
    that commit is not made yet. Pinned to that commit, so later edits elsewhere pass."""
    path = "data/" + name
    log = _git("log", "--format=%H", "-S", NEW_PHRASE, "--", path)
    if log.returncode != 0:
        pytest.skip("no git history")
    shas = log.stdout.split()
    if shas:
        before, after = _git("show", shas[-1] + "^:" + path), _git("show", shas[-1] + ":" + path)
        if before.returncode or after.returncode:
            pytest.skip("history too shallow")
        return json.loads(before.stdout), json.loads(after.stdout)
    before = _git("show", "HEAD:" + path)
    if before.returncode:
        pytest.skip("history too shallow")
    return json.loads(before.stdout), _load(name)


@pytest.mark.parametrize("name", FILES)
def test_the_change_touched_only_the_vein_support_description(name):
    before, after = _introducing_versions(name)
    fb, fa = _flatten(before), _flatten(after)
    changed = {k for k in fb.keys() | fa.keys() if fb.get(k, "<absent>") != fa.get(k, "<absent>")}
    field = ("products", SLUG, "description") if name == "products.json" else (SLUG, "description")
    assert changed == {field}
    if name == "products-manual-corrections.json":
        assert field not in fb and fa[field] == NEW_PRODUCTS_DESC
    else:
        assert fb[field].replace(OLD_PHRASE, NEW_PHRASE, 1) == fa[field]


def _run_apply_enrichment_in_copy(tmp_path, mutate_enrich=False):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ("products.json", "products-enrich-clean.json", "products-manual-corrections.json"):
        shutil.copy(os.path.join(DATA, name), root / "data" / name)
    shutil.copy(os.path.join(ROOT, "scripts", "apply_enrichment.py"), root / "scripts")
    if mutate_enrich:   # a regenerated enrich file brings the old word back
        p = root / "data" / "products-enrich-clean.json"
        p.write_text(p.read_text(encoding="utf-8").replace(NEW_PHRASE, OLD_PHRASE), encoding="utf-8")
        assert "Menadione" in p.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("apply_enrichment_copy",
                                                  root / "scripts" / "apply_enrichment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PRODUCTS == str(root / "data" / "products.json")   # the copy, never the real file
    mod.main()
    return json.loads((root / "data" / "products.json").read_text(encoding="utf-8"))


def test_apply_enrichment_rerun_in_a_copy_keeps_the_correction(tmp_path):
    real_before = open(os.path.join(DATA, "products.json"), encoding="utf-8").read()
    out = _run_apply_enrichment_in_copy(tmp_path)
    assert out["products"][SLUG]["description"] == NEW_PRODUCTS_DESC
    assert open(os.path.join(DATA, "products.json"), encoding="utf-8").read() == real_before


def test_a_regenerated_enrich_file_cannot_bring_the_word_back(tmp_path):
    out = _run_apply_enrichment_in_copy(tmp_path, mutate_enrich=True)
    assert out["products"][SLUG]["description"] == NEW_PRODUCTS_DESC


def test_the_replay_restores_the_description():
    from scripts.apply_enrichment import apply_corrections
    products = _load("products.json")["products"]
    damaged = copy.deepcopy(products)
    damaged[SLUG]["description"] = OLD_PRODUCTS_DESC
    apply_corrections(damaged, _load("products-manual-corrections.json"))
    assert damaged[SLUG]["description"] == NEW_PRODUCTS_DESC
