"""Label-day catalog fixes, 2026-09-14, approved by Glen in the platform tab.

Vascular Integrity (formerly Vitamin P Polyphenols, FMP 374) and Nous Energy (FMP 335) were
made today, so their customer surfaces move to the approved labels. The Vitamin P entry had
been showing Vitamin C Syntropy's formula; Nous Energy showed its old formula and a customer
testimonial. The nine garlic products carry #1628's label names.

Every fix is also in data/products-manual-corrections.json, because a manual rerun of
scripts/apply_enrichment.py rewrites ingredients and descriptions from the clean file. The
rerun test below drives that script on copies of the real data, so deleting a correction
record turns it red.
"""
import json
import shutil

import pytest

import dashboard.product_content as pc

VI = "vitamin-p-polyphenols"
NE = "nous-energy"
VAGUS = "vagus-nerve-support"
VI_NAME = "Vascular Integrity: Vitamin P Plus TECA"
VI_INGREDIENTS = [
    "Bilberry 25% Anthocyanidins (Vaccinium myrtillus)",
    "Ginkgo 24/6 (Ginkgo biloba) (folium)",
    "Pine Bark OPCs 95% (Pinus pinaster) (cortex)",
    "Grape Seed OPCs 95% (Vitis vinifera) (semen)",
    "Hesperidin Methyl Chalcone (Citrus aurantium)",
    "Gotu Kola TECA 80% (Centella asiatica) (herba)",
]
NE_INGREDIENTS = [
    "AHCC 50% (Lentinula edodes) (mycelium)",
    "Graminex G63 Rye Pollen (Secale cereale)",
    "Noni 200:1 (Morinda citrifolia) (fructus)",
    "Green Tea EGCG 50% (Camellia sinensis) (folium)",
    "Black Goji Anthocyanins 25% (Lycium barbarum)",
    "Monatomic Rose Gold ORMUS",
]
GARLIC = ["sulfur-syntropy", "chelation", "ocuflow-daytime", "nerve-pulse", "vein-support",
          "shields-up", "virex", "blood-cleanse", "shields-up-cold-flu-formula"]


@pytest.fixture(scope="module")
def catalog():
    return json.load(open("data/products.json"))["products"]


def _names(product):
    return [i["name"] for i in product.get("ingredients", [])]


def test_vascular_integrity_shows_the_approved_label(catalog):
    p = catalog[VI]
    assert p["name"] == VI_NAME
    assert p["pinecone_title"] == VI_NAME
    assert _names(p) == VI_INGREDIENTS
    assert p["directions"] == "Take 1 to 2 capsules daily with food, or as guided."
    assert not any("ascorb" in n.lower() or "coq10" in n.lower() for n in _names(p))


def test_nous_energy_shows_the_approved_label(catalog):
    p = catalog[NE]
    assert _names(p) == NE_INGREDIENTS
    assert p["directions"] == "Take 1 capsule up to 3 times a day with food, or as guided."
    old = ("vancouver", "unnamed fmp ingredient", "bioavailability", "pacific ocean")
    assert not any(o in n.lower() for n in _names(p) for o in old)


ROBIN_QUOTE = (
    "“I’ve known Glen Swartwout since the year 2005. We were both involved with the original "
    "inventor of the Nouss-Ade product. Glen has found a way to duplicate many ingredients. I "
    "purchase Glen’s Nous Energy formula because of its extraordinary results. I take four capsules "
    "daily and with great consistency. It has provided me with better sleep, more energy. I have no "
    "muscle stiffness now. I have great range of motion. My skin has improved elasticity and my "
    "vision has improved. Thank you Dr. Glen.” - Robin Roemer Brown L.Ac. (received 9/10/24)"
)


def test_vascular_integrity_description_is_label_only(catalog):
    desc = catalog[VI]["description"]
    assert desc.startswith(VI_NAME)
    assert pc._deny_hits(desc) == []


def test_nous_energy_keeps_the_testimonial_unchanged(catalog):
    """Glen, 2026-09-14: "4 caps: keep as is". The quote stays word for word after the
    label intro; only the old formula text around it goes."""
    desc = catalog[NE]["description"]
    assert desc.startswith("Nous Energy, Spirit Mineral Source.")
    assert ROBIN_QUOTE in desc
    assert "our new nous" not in desc.lower()
    assert pc._deny_hits(desc) == []


class _Msg:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class _Messages:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return _Msg(self.seq.pop(0))


class _Client:
    def __init__(self, seq):
        self.messages = _Messages(seq)


EYE_DROPS = {"name": "Pterygium Eye Drops", "pinecone_title": "Pterygium Eye Drops"}


def _card(monkeypatch, product, replies):
    client = _Client(replies)
    monkeypatch.setattr(pc, "_clients", lambda: (None, client, None))
    return pc._generate_card(product, {"text": "page copy"}), client


def test_a_product_named_after_a_condition_keeps_its_card(monkeypatch):
    """A real product is called "Pterygium Eye Drops". Naming itself must not blank its page."""
    reply = json.dumps({"description": "Pterygium Eye Drops is a gentle drop for the eye surface.",
                        "ingredients": [], "benefits": ["Pterygium Eye Drops comfort the eye surface"]})
    card, client = _card(monkeypatch, EYE_DROPS, [reply])
    assert card["description"].startswith("Pterygium Eye Drops")
    assert card["benefits"]
    assert len(client.messages.calls) == 1


def test_the_claim_is_still_caught_on_that_product(monkeypatch):
    claim = json.dumps({"description": "Pterygium Eye Drops.", "ingredients": [],
                        "benefits": ["Support for pterygium-related concerns"]})
    card, client = _card(monkeypatch, EYE_DROPS, [claim, claim])
    assert card["description"] == "" and card["benefits"] == []
    assert len(client.messages.calls) == 2


def test_vagus_drops_show_no_per_bottle_amounts_as_doses(catalog):
    """Glen, 2026-09-14: this slug is Vagus Nerve Support Drops (FMP 460). Its T33 amounts
    are per-bottle batch figures, which the page would print as doses."""
    p = catalog[VAGUS]
    assert len(p["ingredients"]) == 24
    assert all(i["dose"] == "" for i in p["ingredients"])
    assert "price" not in p["description"].lower()
    assert p["bottle_type"] == "Dropper 50 mL"


def test_the_gate_catches_pterygium():
    assert "pterygium" in pc._deny_hits("Support for eye health, including pterygium-related concerns")


def test_a_rerun_of_apply_enrichment_keeps_every_fix(catalog, tmp_path, monkeypatch):
    from scripts import apply_enrichment as ae

    for name in ("products.json", "products-enrich-clean.json", "products-manual-corrections.json"):
        shutil.copy(f"data/{name}", tmp_path / name)
    monkeypatch.setattr(ae, "PRODUCTS", str(tmp_path / "products.json"))
    monkeypatch.setattr(ae, "CLEAN", str(tmp_path / "products-enrich-clean.json"))
    monkeypatch.setattr(ae, "CORRECTIONS", str(tmp_path / "products-manual-corrections.json"))

    ae.main()

    after = json.load(open(tmp_path / "products.json"))["products"]
    for slug in (VI, NE):
        assert _names(after[slug]) == _names(catalog[slug]), slug
        assert after[slug]["directions"] == catalog[slug]["directions"], slug
        assert after[slug]["description"] == catalog[slug]["description"], slug
    assert after[VI]["name"] == VI_NAME
    for slug in GARLIC:
        assert _names(after[slug]) == _names(catalog[slug]), slug
        assert bool(after[slug].get("gk_stale")) == bool(catalog[slug].get("gk_stale")), slug
    assert after[VAGUS]["ingredients"] == catalog[VAGUS]["ingredients"]
    assert after[VAGUS]["description"] == catalog[VAGUS]["description"]
