"""Code writes the whole reply for two questions, and the model is not called.

Live read-backs on 2026-11-26 after #2345 and #2346: 5 of 6 comparison answers invented
claims, and 4 of 8 AllerFree answers said "gentle, with no known side effects". Glen,
platform tab: "yes, code writes both answers".
"""
import pytest

import app

AF = "allerfree-homeoenergetic-drops"
Q_AF = "How do I take AllerFree?"
Q_CMP = "What is the difference between Nerve Pulse and Nerve Repair?"


def _p(slug):
    return app._PRODUCTS["products"][slug]


def _url(slug):
    return app._catalog_page_url(slug)


# ---- AllerFree -----------------------------------------------------------------------

def test_allerfree_reply_is_directions_page_and_preference():
    dirs = " ".join(_p(AF)["directions"].split())
    assert app._coded_directions_reply(Q_AF) == (
        f"The label directions for Aller-Free HomeoEnergetic Drops are: {dirs}\n\n"
        f"Its product page: {_url(AF)}\n\n"
        f"Immune Modulation is Dr. Glen's current preference: {_url('immune-modulation')}")


def test_allerfree_reply_says_nothing_else():
    r = app._coded_directions_reply(Q_AF).lower()
    for word in ("side effect", "gentle", "safe", "water", "dilut", "succuss", "allerg"):
        assert word not in r, word


@pytest.mark.parametrize("q", ["What is AllerFree?", "Does AllerFree work for hay fever?",
                               "How do I take AllerFree and Nerve Pulse?"])
def test_other_allerfree_questions_still_go_to_the_model(q):
    assert app._coded_directions_reply(q) == ""
    assert app._coded_reply(q) == ("", None)


def test_a_carried_follow_up_is_not_code_written():
    assert app._coded_directions_reply("How often?", ["Tell me about AllerFree"]) == ""


def test_no_preference_line_when_immune_modulation_is_inactive(monkeypatch):
    im = dict(_p("immune-modulation"), inactive=True)
    monkeypatch.setitem(app._PRODUCTS["products"], "immune-modulation", im)
    r = app._coded_directions_reply(Q_AF)
    assert r and "Immune Modulation" not in r


def test_allerfree_button_opens_its_page():
    _r, cta = app._coded_reply(Q_AF)
    assert cta == {"type": "page", "target": _url(AF),
                   "label": "See Aller-Free HomeoEnergetic Drops"}


# ---- two or more caution products ----------------------------------------------------

def test_compare_reply_has_each_product_in_order_with_its_caution():
    r = app._coded_compare_reply(Q_CMP)
    pulse, repair = r.index("**Nerve Pulse**"), r.index("**Nerve Repair**")
    assert pulse == 0 and pulse < repair
    for slug in ("nerve-pulse", "nerve-repair"):
        w = _p(slug)["warning"].strip()
        assert r.count(w) == 1
        assert f"Product page: {_url(slug)}" in r
    assert r.index(_p("nerve-pulse")["warning"].strip()) < repair


def test_approved_description_and_directions_only():
    r = app._coded_compare_reply(Q_CMP)
    # Nerve Pulse pins description and directions; Nerve Repair pins neither.
    assert "Directions: " + " ".join(_p("nerve-pulse")["directions"].split()) in r
    assert _p("nerve-pulse")["description"].split("\n")[0].strip() in r
    repair = r[r.index("**Nerve Repair**"):]
    assert "Directions:" not in repair
    assert _p("nerve-repair")["description"][:40] not in r


def test_unpinning_drops_the_text(monkeypatch):
    np = dict(_p("nerve-pulse"), copy_pinned=["ingredients"])
    monkeypatch.setitem(app._PRODUCTS["products"], "nerve-pulse", np)
    r = app._coded_compare_reply(Q_CMP)
    assert "Directions:" not in r
    assert _p("nerve-pulse")["description"][:40] not in r
    assert _p("nerve-pulse")["warning"].strip() in r


def test_a_caution_inside_the_description_is_given_once(monkeypatch):
    np = dict(_p("nerve-pulse"))
    np["description"] = "About it.\n\nCaution: " + np["warning"].strip()
    monkeypatch.setitem(app._PRODUCTS["products"], "nerve-pulse", np)
    r = app._coded_compare_reply(Q_CMP)
    assert r.count(np["warning"].strip()) == 1 and "About it." in r


def test_one_product_or_a_bundle_is_not_code_written():
    for q in ("What is Nerve Pulse?", "What is the Sleep Bundle?"):
        assert app._coded_compare_reply(q) == ""


def test_fails_open(monkeypatch):
    monkeypatch.setattr(app, "_catalog_page_url",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert app._coded_compare_reply(Q_CMP) == ""
    assert app._coded_directions_reply(Q_AF) == ""
    assert app._coded_reply(Q_AF) == ("", None)


def test_a_mismatched_caution_list_gives_none():
    c = app._compare_cautions(Q_CMP)
    assert app._coded_compare_reply(Q_CMP, cautions=c[::-1]) == ""
    assert app._coded_compare_reply(Q_CMP, cautions=c[:1]) == ""


def test_the_reply_has_no_model_text_rules_left_in_it():
    r = app._coded_compare_reply(Q_CMP)
    assert "—" not in r and "⟦CTA⟧" not in r
