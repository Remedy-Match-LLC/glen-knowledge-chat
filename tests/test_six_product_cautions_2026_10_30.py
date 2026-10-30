"""Six product cautions, word for word as Glen approved them in the communication tab.

He typed "approve" on 2026-10-30 to communication/drafts/2026-10-30-product-cautions/
cautions.md, after research and three blind reviews, and said yes to the write in the
platform tab the same day. AngiogenX changes two sentences only: "Stop a few days before
surgery. Resume once healing is complete." became the surgery lines the others carry.
"""
import io
import json
import os
import shutil
from contextlib import redirect_stdout

import pytest

import app

ROOT = os.path.join(os.path.dirname(__file__), "..")
APPROVED = {
    "macular-wellness-lycopene": (
        "Avoid use in pregnancy or while nursing. Stop 1 to 2 weeks before surgery or "
        "dental work. Resume only when your surgeon approves. If you take a blood "
        "thinner or daily aspirin, consult your physician before use. The danshen in "
        "this formula can strengthen the effect of warfarin. Ask your pharmacist if you "
        "take any prescription medicine. Avoid use if you have gallstones or a bile "
        "duct problem. If you receive eye injections, tell your eye doctor that you "
        "take this. Stop use and seek care if you notice dark urine or yellow skin."),
    "lipid-zyme": (
        "This formula contains enzymes that may increase the risk of bleeding. Do not "
        "use if you have a bleeding disorder or a history of bleeding. Avoid use with a "
        "fresh or bleeding wound. Also avoid use during menses or pregnancy, or while "
        "nursing. Stop 1 to 2 weeks before surgery or dental work. Resume only when "
        "your surgeon approves. Do not combine with a blood thinner or daily aspirin "
        "unless your physician approves. Do not take with Scar Silk unless your "
        "physician approves. Avoid use if you are allergic to pineapple. Ask your "
        "pharmacist if you take any prescription medicine. If you receive eye "
        "injections, tell your eye doctor that you take this. Stop use and seek care at "
        "once if you notice unusual bleeding or black stools."),
    "lipid-cleanse": (
        "Avoid use in pregnancy or while nursing. Avoid use if you have liver disease. "
        "Stop 1 to 2 weeks before surgery or dental work. Resume only when your surgeon "
        "approves. If you take a blood thinner or daily aspirin, consult your physician "
        "before use. This formula may lower blood sugar. Consult your physician if you "
        "take medicine for diabetes. It can also change how the body clears some "
        "prescription drugs. Ask your pharmacist if you take any prescription medicine. "
        "If you receive eye injections, tell your eye doctor that you take this. Stop "
        "use and seek care if you notice dark urine or yellow skin. It may cause "
        "stomach upset or trouble sleeping."),
    "scar-solve": (
        "Avoid use in pregnancy or while nursing. Stop 1 to 2 weeks before surgery or "
        "dental work. Resume only when your surgeon approves. If you take a blood "
        "thinner or daily aspirin, consult your physician before use. If you have an "
        "autoimmune condition, consult your physician before use. Also consult your "
        "physician if you take medicine that suppresses the immune system. Ask your "
        "physician first if you have or have had cancer. This formula contains zinc. If "
        "you take an eye vitamin with zinc, consult your physician before adding this. "
        "Ask your pharmacist if you take any prescription medicine. If you receive eye "
        "injections, tell your eye doctor that you take this. Stop use and seek care if "
        "you notice dark urine or yellow skin."),
    "scar-silk": (
        "This formula contains enzymes that may increase the risk of bleeding. Do not "
        "use if you have a bleeding disorder or a history of bleeding. Avoid use with a "
        "fresh or bleeding wound. Also avoid use during menses or pregnancy, or while "
        "nursing. Stop 1 to 2 weeks before surgery or dental work. Resume only when "
        "your surgeon approves. Do not combine with a blood thinner or daily aspirin "
        "unless your physician approves. Do not take with Lipid Zyme unless your "
        "physician approves. Avoid use if you are allergic to pineapple. Ask your "
        "pharmacist if you take any prescription medicine. If you receive eye "
        "injections, tell your eye doctor that you take this. Stop use and seek care at "
        "once if you notice unusual bleeding or black stools. Stop use and seek care if "
        "you notice dark urine or yellow skin."),
    "angiogenx": (
        "Avoid use while healing a wound or any other tissue injury, such as a cut, "
        "fracture, sprain or burn. Also avoid use during menses or in pregnancy. Stop 1 "
        "to 2 weeks before surgery or dental work. Resume only when your surgeon "
        "approves. If you take a blood thinner, consult your physician before use."),
}


def _data(name):
    return json.load(open(os.path.join(ROOT, "data", name)))


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_each_caution_is_the_approved_text_in_both_files(slug):
    assert _data("products.json")["products"][slug]["warning"] == APPROVED[slug]
    assert _data("products-manual-corrections.json")[slug]["warning"] == APPROVED[slug]


def test_angiogenx_keeps_the_rest_of_its_caution():
    w = APPROVED["angiogenx"]
    assert "Stop a few days" not in w and "Resume once healing" not in w
    assert w.startswith("Avoid use while healing a wound or any other tissue injury")
    assert w.endswith("If you take a blood thinner, consult your physician before use.")
    # Its pinned description ends with the same caution, so it changed too: the page must
    # not say "a few days" and "1 to 2 weeks" at once.
    for d in (_data("products.json")["products"], _data("products-manual-corrections.json")):
        assert d["angiogenx"]["description"].endswith("Caution: " + w)


@pytest.mark.parametrize("slug", sorted(APPROVED))
def test_the_page_shows_the_caution(slug):
    d = app.app.test_client().get(f"/begin/product-page-data/{slug}").get_json()
    secs = {s["id"]: s for s in d["sections"]}
    assert secs["caution"]["body"] == APPROVED[slug]


def test_a_rebuild_keeps_every_caution_and_every_source(tmp_path, monkeypatch):
    """main() twice on a copy with the warnings stripped. The new entries carry the source
    main() already gives, so a rebuild relabels nothing it would not have."""
    import scripts.apply_enrichment as ae
    names = ("products-enrich-clean.json", "products.json", "products-manual-corrections.json")

    def run(where, drop_new):
        where.mkdir()
        for f in names:
            shutil.copy(os.path.join(ROOT, "data", f), where / f)
        monkeypatch.setattr(ae, "CLEAN", str(where / names[0]))
        monkeypatch.setattr(ae, "PRODUCTS", str(where / names[1]))
        monkeypatch.setattr(ae, "CORRECTIONS", str(where / names[2]))
        if drop_new:
            corr = json.load(open(where / names[2]))
            for s in APPROVED:
                if set(corr[s]) <= {"ingredients_source", "warning"}:
                    corr.pop(s)
            json.dump(corr, open(where / names[2], "w"))
        else:
            cat = json.load(open(where / names[1]))
            for s in APPROVED:
                cat["products"][s].pop("warning")
            json.dump(cat, open(where / names[1], "w"))
        outs = []
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                ae.main()
            outs.append(json.load(open(where / names[1]))["products"])
        return outs

    control = run(tmp_path / "control", drop_new=True)[-1]
    for out in run(tmp_path / "stripped", drop_new=False):
        for s in APPROVED:
            assert out[s]["warning"] == APPROVED[s], s
            for k in ("ingredients_source", "gk_stale", "chat_caution"):
                assert out[s].get(k) == control[s].get(k), (s, k)
