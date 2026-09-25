"""OcuHeal and OcuHeal+ show the same panel except DMSO and the base.

Glen told a client "the one change from the OcuHeal you had in April is 10% DMSO", but the
pages disagreed on five other rows: OcuHeal's came from a stale cached AI card built on old
store copy. Spec: production/05 Formulations/ocuheal-plus/2026-09-25/panel-correction-spec.md,
Glen "approved" 2026-09-25. Carnosine is 1% in both (Glen, 2026-09-25). Order follows the
panel image, not the percentages. Checked through /begin/product-page-data, the endpoint
the page reads, by EXACT equality of (name, dose) pairs: a pairwise walk would stop at a
shorter list, and a percentage is never parsed out of a name.
"""
import importlib

import pytest

TAIL = [("Forskolin (Coleus forskohlii)", "0.1%"), ("Puerarin (Pueraria lobata)", "0.1%"),
        ("Vitamin A (Retinol)", "0.1%"), ("Vitamin B2 (Riboflavin 5-Phosphate)", "0.1%"),
        ("Vitamin B6 (Pyridoxal 5-Phosphate)", "0.1%"), ("Vitamin C & Zinc (Zinc Ascorbate)", "0.1%"),
        ("Vitamin E Complex", "0.01%"), ("Lanosterol", "0.01%"),
        ("Safranal 3% (Crocus sativus)", "0.1%"),
        ("Cineraria (Cineraria maritima) (succus)", "0.1%"), ("Silver (Nano)", "0.0001%"),
        ("Serrapeptase (Serratia marcescens)", "5C")]
EXPECTED = {
    "ocuheal-eye-drops": [("Quintessential Bioterrain Restore", "96%"),
                          ("MSM (Methylsulfonylmethane)", "1%"), ("N-Acetyl L-Carnosine", "1%"),
                          ("DMSO (Dimethylsulfoxide)", "0.5%")] + TAIL,
    "ocuheal-plus-eye-drops": [("Quintessential Bioterrain Restore", "86.5%"),
                               ("DMSO (Dimethylsulfoxide)", "10%"),
                               ("MSM (Methylsulfonylmethane)", "1%"),
                               ("N-Acetyl L-Carnosine", "1%")] + TAIL,
}
OCU_TEXT = ("OcuHeal Eye Drops carry botanical and nutritional ingredients for the whole eye, "
            "including the retina, lens, cornea, conjunctiva, lacrimal glands and tear film. The "
            "base is Quinton sea water (Quintessential Bioterrain Restore). Use 1 drop in each "
            "eye 2 times a day.")


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("SALES_PAGES_ENABLED", "true")
    mp.setenv("OPENAI_API_KEY", "sk-fake")
    mp.setenv("PINECONE_API_KEY", "pcsk_fake")
    import app as appmod
    importlib.reload(appmod)
    appmod.app.config["TESTING"] = True
    yield appmod.app.test_client()
    mp.undo()


def _section(data, sid):
    return next(s for s in data["sections"] if s["id"] == sid)


def _pairs(data):
    ings = _section(data, "ingredients")["body"]["ingredients"]
    return [(i["name"], i["dose"]) for i in ings]


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_the_page_serves_exactly_the_bottle_panel(client, slug):
    got = _pairs(client.get(f"/begin/product-page-data/{slug}").get_json())
    assert all(dose for _n, dose in got), "an empty dose is a failure"
    assert got == EXPECTED[slug]


def test_only_dmso_and_the_base_differ():
    a, b = EXPECTED["ocuheal-eye-drops"], EXPECTED["ocuheal-plus-eye-drops"]
    diff = {n for n, d in a if dict(b).get(n) != d}
    assert diff == {"Quintessential Bioterrain Restore", "DMSO (Dimethylsulfoxide)"}


def test_the_equality_check_catches_one_wrong_percentage():
    """Spec: prove the check by running it once with one percentage wrong."""
    wrong = [(n, "0.5%" if n == "N-Acetyl L-Carnosine" else d) for n, d in EXPECTED["ocuheal-eye-drops"]]
    assert wrong != EXPECTED["ocuheal-eye-drops"]
    assert wrong[:15] != EXPECTED["ocuheal-eye-drops"][:15]


def test_ocuheal_serves_its_approved_text_pinned(client):
    data = client.get("/begin/product-page-data/ocuheal-eye-drops").get_json()
    for sid in ("intro", "description"):
        sec = _section(data, sid)
        assert "ai" not in sec, f"{sid} must be pinned, not a cached draft"
        assert OCU_TEXT in str(sec["body"]), sid
    assert _section(data, "ingredients")["body"]["directions"] == "1 drop in each eye 2 times a day."
    text = str(data)
    for stale in ("levetates", "Quintessential Terrain Restore) 96%", "(10 ppm)", "mirifica"):
        assert stale not in text, stale


def test_ocuheal_plus_serves_its_approved_intro_pinned(client):
    from tests.test_ocuheal_plus_listing import APPROVED_INTRO
    data = client.get("/begin/product-page-data/ocuheal-plus-eye-drops").get_json()
    for sid in ("intro", "description"):
        sec = _section(data, sid)
        assert "ai" not in sec, sid
        assert APPROVED_INTRO in str(sec["body"]), sid
