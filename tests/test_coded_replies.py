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
    # Nerve Pulse pins description and directions. An unpinned product shows neither.
    assert "Directions: " + " ".join(_p("nerve-pulse")["directions"].split()) in r
    assert _p("nerve-pulse")["description"].split("\n")[0].strip() in r


def test_an_unpinned_product_shows_no_description_or_directions(monkeypatch):
    nr = {k: v for k, v in _p("nerve-repair").items() if k != "copy_pinned"}
    monkeypatch.setitem(app._PRODUCTS["products"], "nerve-repair", nr)
    r = app._coded_compare_reply(Q_CMP)
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


# ---- review rounds 1 to 3 ------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "I'm pregnant. How do I take AllerFree?",
    "I am breastfeeding. How many drops of AllerFree?",
    "How much AllerFree can I give my 4 year old?",
    "How much AllerFree should I give my dog?",
    "I took 30 drops of AllerFree at once, is that dangerous?",
    "My throat swelled. How much AllerFree should I take now?",
    "How do I take AllerFree with prednisone?",
    "How much does AllerFree cost?",
    "How can I buy AllerFree?",
    "How do I take Aller-Free Aid?",
    "How do I take AllerFree with Fungifuge?",
    "How many drops of AllerFree and Food Tolerance?",
])
def test_a_situation_or_other_question_goes_to_the_model(q):
    assert app._coded_reply(q) == ("", None), q


def test_a_situation_in_an_earlier_turn_counts():
    prior = ["I take warfarin every day."]
    assert app._coded_reply(Q_AF, prior) == ("", None)
    assert app._coded_reply(Q_CMP, ["I'm pregnant."]) == ("", None)


@pytest.mark.parametrize("q", [
    "Nerve Pulse with Nerve Repair and Vitamin C?",
    "I take antidepressants. Compare Nerve Pulse and Nerve Repair",
    "Is caffeine in Thermogenesis or Appestat?",
    "I'm pregnant. Can I take Thyroid Support and Iron Out?",
    "Can my 10 year old take Sleep Syntropy and Melatonin Max?",
])
def test_compare_questions_with_more_go_to_the_model(q):
    assert app._coded_compare_reply(q) == "", q


def test_the_full_reports_never_code_write_past_a_do_not_use_situation(monkeypatch):
    q = "I'm pregnant. Can I take Thyroid Support and Iron Out?"
    assert app._coded_reply(q) == ("", None)


def test_an_encoded_caution_inside_the_description_is_given_once(monkeypatch):
    np = dict(_p("nerve-pulse"))
    np["warning"] = np["warning"].replace("St. John's", "St. John&#39;s")
    np["description"] = "About it.\n\nCaution: " + _p("nerve-pulse")["warning"].strip()
    monkeypatch.setitem(app._PRODUCTS["products"], "nerve-pulse", np)
    r = app._coded_compare_reply(Q_CMP)
    assert r.count("Huperzine") <= 1
    assert r.count("interacts with many medicines") == 1


def test_a_button_failure_means_no_coded_reply(monkeypatch):
    monkeypatch.setattr(app, "_coded_name", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert app._coded_reply(Q_AF) == ("", None)


def test_no_follow_up_question_is_asked_of_the_model(monkeypatch, tmp_path):
    import importlib, json
    from types import SimpleNamespace
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(app)
    calls = []
    monkeypatch.setattr(app._cl.messages, "create", lambda **kw: calls.append(kw) or None)
    monkeypatch.setattr(app._cl.messages, "stream", lambda **kw: calls.append(kw) or None)
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    one = [SimpleNamespace(id="m0", score=0.8, metadata={"text": "x", "_source_ns": "x"})]
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: one)
    monkeypatch.setattr(app, "build_context", lambda m: ("snippet text", []))
    monkeypatch.setattr(app, "_is_gated_question", lambda *a, **k: False)
    monkeypatch.setattr(app, "is_member", lambda *a, **k: False)
    for q in (Q_AF, Q_CMP):
        r = app.app.test_client().post("/chat", json={"query": q, "level": "self-healing",
                                                      "mode": "brief"}, buffered=True)
        assert r.status_code == 200
    assert calls == []


def test_a_named_product_without_a_caution_means_no_coded_reply():
    q = "What is the difference between Nerve Pulse, Nerve Repair and Brain Boost?"
    assert "brain-boost" in app._named_product_spans(q, app._PRODUCTS["products"])
    assert app._coded_compare_reply(q) == ""


def test_a_do_not_use_reply_means_no_coded_reply(monkeypatch):
    monkeypatch.setattr(app, "_do_not_use_caution_reply", lambda *a, **k: "caution")
    assert app._coded_reply(Q_AF) == ("", None)
    assert app._coded_compare_reply(Q_CMP) == ""


# ---- Nerve Repair pinned, Glen 2026-11-26 platform tab ---------------------------------

def test_nerve_repair_half_carries_its_approved_text_once():
    r = app._coded_compare_reply(Q_CMP)
    repair = r[r.index("**Nerve Repair**"):]
    assert repair.count("1 capsule up to 5 times daily with food, or as guided.") == 1
    assert "Each capsule of Nerve Repair supplies ALCAR" in repair
    assert "Directions:" not in repair
    pulse = r[:r.index("**Nerve Repair**")]
    assert "Directions: " in pulse
