"""The bot copies a caution and adds nothing around it.

Live chat on 81acb916, 2026-10-21. Asked "Can I take Vitamin D Syntropy if I am on
warfarin?", the bot quoted only the first sentence of the caution and called it the
label's. It said some patients tolerate it with monitoring, said to adjust the warfarin
dose, and suggested a different vitamin D source. Vitamin D Syntropy was pinned but had no
chat_caution, so no copy rule reached the bot for it. The copy rules also never forbade
framing or advice around a caution.

Review round 1 (2026-10-21) scoped the rule to the caution's own subject, removed the
destination it named, and wrote it once per answer in the header.
"""
import copy
import json

import pytest

import app

SLUG = "vitamin-d-syntropy"
WARNING = ("If you take a blood thinner such as warfarin, consult your physician before "
           "use, because vitamin K affects it. Consult your physician before use if you "
           "are pregnant or have a thyroid condition.")
PRODUCTS = json.load(open("data/products.json"))["products"]
CORRECTIONS = json.load(open("data/products-manual-corrections.json"))
DESCRIPTION_LEAD = "Vitamin D is formed in the skin when sunlight transforms cholesterol"
MONITORING = ("Individuals who take more than 2,000 IU per day of vitamin D should have "
              "their vitamin D levels monitored.")
RULE = app._CAUTION_NO_ADDITIONS


def _opt_out(monkeypatch, *slugs):
    # Every pinned product with a caution opts in since 2026-10-21 (Glen's yes to all
    # seven), so the "never opted in" path is exercised by switching real ones off here.
    for slug in slugs:
        monkeypatch.setitem(app._PRODUCTS["products"][slug], "chat_caution", False)


def _header(b):
    return b[:b.index("\n### ")]


def test_vitamin_d_syntropy_opts_its_caution_into_the_chat_in_both_files():
    for record in (PRODUCTS[SLUG], CORRECTIONS[SLUG]):
        assert record["chat_caution"] is True
        assert record["warning"] == WARNING


def test_a_rebuild_keeps_the_opt_in():
    from scripts.apply_enrichment import apply_corrections
    products = copy.deepcopy(PRODUCTS)
    products[SLUG].pop("chat_caution")
    apply_corrections(products, copy.deepcopy(CORRECTIONS))
    assert products[SLUG]["chat_caution"] is True


@pytest.mark.parametrize("q", ["Can I take Vitamin D Syntropy if I am on warfarin?",
                               "Is Vitamin D Syntropy safe with a blood thinner?"])
def test_a_warfarin_question_gets_the_full_caution_and_the_rules_once(q):
    b = app.named_product_facts_block(q)
    assert "### Vitamin D Syntropy" in b
    # Both sentences, once: the second one was dropped live. The caution is held inside
    # the pinned description, so that paragraph must be there to carry it.
    assert b.count(WARNING) == 1 and DESCRIPTION_LEAD in b
    assert app._ONE_CAUTION_RULE in _header(b) and app._SEVERAL_CAUTIONS_RULE not in b
    assert b.count(RULE) == 1 and RULE in _header(b)
    assert "the caution on the product page" in RULE and "never the label caution" in RULE
    for added in ("tolerate the product", "monitoring or changing any medicine or dose",
                  "no one else to ask"):
        assert added in RULE
    # No destination of the bot's own: the caution's own "consult your physician" stands.
    assert "Dr. Glen" not in b
    assert "patient" not in b.lower() and "\u2014" not in RULE


def test_a_dose_question_keeps_the_approved_monitoring_line():
    # Review round 1, F1: the description's own monitoring line is approved text, so the
    # rule must carve it out rather than ban monitoring everywhere.
    b = app.named_product_facts_block("How much Vitamin D Syntropy should I take?")
    assert MONITORING in b and b.count(RULE) == 1
    assert "Repeat the approved description and directions below as written" in RULE
    assert "When the user asks about a medicine, pregnancy, surgery or a condition that a caution names" in RULE


def test_the_brain_program_note_and_the_rule_agree():
    # Review round 1, F2: the caution-only note says to answer from the snippets. The rule
    # limits only the caution's subject, so it leaves other questions to that note.
    b = app.named_product_facts_block(
        "What is the Brain Program and is it safe with my antidepressant?")
    assert app._CAUTION_ONLY_NOTE in b and b.count(RULE) == 1
    assert "answer other questions as before" in RULE
    assert "This limits only what you add yourself." in RULE


def test_two_opted_in_cautions_still_carry_the_rule_once():
    # Review round 1, F5: each pinned block keeps its own reminder, the rule is not repeated.
    b = app.named_product_facts_block("Is Brain Cleanse safe with Sleep Syntropy?")
    assert app._SEVERAL_CAUTIONS_RULE in b and b.count(RULE) == 1
    assert b.count(app._CAUTION_COPY_REMINDER) == 2
    assert RULE not in app._CAUTION_COPY_REMINDER


def test_a_product_without_the_opt_in_keeps_its_header(monkeypatch):
    # Estro-Clear switched off: pinned with a warning, no copy rule, no new rule.
    _opt_out(monkeypatch, "estro-clear")
    b = app.named_product_facts_block("Is Estro-Clear safe?")
    assert "### Estro-Clear" in b and RULE not in b


def test_the_gated_rewording_still_finds_its_target():
    # The gated answer swaps the paragraph start; the added sentences must not break it.
    b = app.named_product_facts_block("Can I take Vitamin D Syntropy if I am on warfarin?",
                                      gated=True)
    assert '"The caution on the product page for <product name> is:"' in b
    assert b.count(WARNING) == 1 and b.count(RULE) == 1


STAY = app._CAUTION_STAY_LINE
CLOSING = app._CAUTION_CLOSING_REMINDER


def test_the_warfarin_block_has_the_stay_line_after_the_caution_and_closes_on_the_reminder():
    # Review round 2, variants a to c: the header rule alone let warfarin snippets through.
    b = app.named_product_facts_block("Can I take Vitamin D Syntropy if I am on warfarin?")
    assert b.count(STAY) == 1 and b.index(STAY) > b.index(WARNING)
    # "Give only the caution's words" made answers quote one sentence (variant d).
    assert STAY.startswith("Copy every sentence of the caution above.")
    assert "give only" not in STAY.lower()
    assert b.count(CLOSING) == 1 and b.endswith("\n\n" + CLOSING)
    assert "skip the drug interaction text in the snippets" in CLOSING
    assert "never the label" in CLOSING and "Dr. Glen" not in b


def test_each_opted_in_caution_gets_one_stay_line_and_the_answer_one_reminder():
    b = app.named_product_facts_block("Is Brain Cleanse safe with Sleep Syntropy?")
    assert b.count(STAY) == 2 and b.count(CLOSING) == 1


def test_a_caution_without_the_opt_in_gets_neither(monkeypatch):
    _opt_out(monkeypatch, "estro-clear")
    b = app.named_product_facts_block("Is Estro-Clear safe?")
    assert "### Estro-Clear" in b and STAY not in b and CLOSING not in b


# The snippet filter (variant d, 2026-10-21). Metadata copied from live retrieval for the
# warfarin question: Stargrove in its three stored forms, and two snippets that stay.
from types import SimpleNamespace as _M

_STARGROVE_BOOK = {"author": "Mitchell Bebel Stargrove + Lori Beth Stargrove",
                   "book": "Herb, Nutrient and Drug Interactions: Clinical Implications and "
                           "Therapeutic Strategies", "_source_ns": "clinical-references"}
_STARGROVE_PDF = {"source": "pdf-book", "title": "Herb Nutrient and Drug Interactions",
                  "_source_ns": "ingredients"}
_IBIS_INTE = {"source": "ibis", "title": "Integrative — materia-medica — rx — interactions — "
              "Warfarin (Coumadin) — Integra", "_source_ns": "ingredients"}
_IBIS_RX = {"ibis_modality": "INTE", "ibis_topic": "WARFARIN",
            "_source_ns": "clinical-references"}
_STUDY = {"chunk_type": "study", "ingredient": "Vitamin K1 (Phylloquinone)",
          "_source_ns": "ingredients"}
_IBIS_VITAMIN = {"source": "ibis", "title": "Nutrition — materia-medica — rx — vitamin — "
                 "Vitamin K — Nutrition", "_source_ns": "ingredients"}
# A Stargrove record known only by its author, so the author check stands on its own.
_STARGROVE_AUTHOR = {"author": "Mitchell Bebel Stargrove + Health Resources Unlimited Inc.",
                     "_source_ns": "clinical-references"}
_DROPPED = [_STARGROVE_BOOK, _STARGROVE_PDF, _IBIS_INTE, _IBIS_RX, _STARGROVE_AUTHOR]
_KEPT = [_STUDY, _IBIS_VITAMIN]


def _matches():
    return [_M(id=f"m{i}", score=0.8, metadata=dict(meta, text="warfarin text"))
            for i, meta in enumerate(_DROPPED + _KEPT)]


@pytest.mark.parametrize("q", ["Can I take Vitamin D Syntropy if I am on warfarin?",
                               "My INR is stable on Coumadin, can I add Vitamin D Syntropy?",
                               "Is Vitamin D Syntropy safe on a blood thinner?",
                               "Is Vitamin D Syntropy ok if I am pregnant?"])
def test_a_caution_subject_question_drops_only_the_drug_interaction_snippets(q):
    kept = app._drop_caution_subject_snippets(_matches(), q)
    assert [m.metadata["_source_ns"] for m in kept] == ["ingredients", "ingredients"]
    assert [m.id for m in kept] == ["m5", "m6"]


@pytest.mark.parametrize("q", [
    "How much Vitamin D Syntropy should I take?",   # no caution subject named
    "Can I take warfarin with vitamin K?",          # control: no product named
    "Is Estro-Clear safe with warfarin?",           # pinned caution, switched off here
    "Is Clear the Way safe with my blood thinner?", # names the subject, switched off here
    "Can I take Scar Silk while pregnant?",         # opted in, but its caution has no pregnancy
])
def test_every_other_question_keeps_its_snippets(q, monkeypatch):
    _opt_out(monkeypatch, "estro-clear", "clear-the-way")
    ms = _matches()
    assert app._drop_caution_subject_snippets(ms, q) is ms


def test_the_subject_words_come_from_the_caution():
    assert app._caution_subjects_asked(
        "Can I take Vitamin D Syntropy if I am on warfarin?") == ["warfarin", "blood thinner"]
    assert app._caution_subjects_asked(
        "My INR is stable on Coumadin, can I add Vitamin D Syntropy?") == [
        "warfarin", "blood thinner"]
    assert app._caution_subjects_asked(
        "Is the Sleep Bundle ok if I am pregnant?") == ["pregnan"]
    assert app._caution_subjects_asked("How much Vitamin D Syntropy should I take?") == []


class _Stop(Exception):
    pass


@pytest.fixture
def spy(monkeypatch):
    """Stubs retrieval and stops at build_context, recording what reached it. Nothing
    after it runs, so no model call, log row or mail."""
    seen = []
    monkeypatch.setattr(app, "embed", lambda text: [0.0] * 1536)
    monkeypatch.setattr(app, "query_all_namespaces", lambda vec: _matches())

    def _record(matches):
        seen.append([m.id for m in matches])
        raise _Stop()
    monkeypatch.setattr(app, "build_context", _record)
    return seen


def test_chat_drops_them_before_the_snippets_are_built(spy):
    try:
        app.app.test_client().post(
            "/chat", json={"query": "Can I take Vitamin D Syntropy if I am on warfarin?",
                           "level": "executive"}, buffered=True)
    except _Stop:
        pass
    assert spy == [["m5", "m6"]]


def test_the_full_answer_drops_them_too(spy):
    with pytest.raises(_Stop):
        app._generate_full_answer("Can I take Vitamin D Syntropy if I am on warfarin?",
                                  "executive")
    with pytest.raises(_Stop):
        app._generate_full_answer("Can I take warfarin with vitamin K?", "executive")
    assert spy == [["m5", "m6"], [f"m{i}" for i in range(7)]]


def test_the_full_report_stream_drops_them_too(spy):
    with app.app.test_request_context():
        resp = app._full_report_stream(
            1, "Can I take Vitamin D Syntropy if I am on warfarin?", "executive", "s1")
        with pytest.raises(_Stop):
            for _ in resp.response:
                pass
    assert spy == [["m5", "m6"]]


# Review round 2b: follow-ups, other spellings of a subject, and the product cap.
PRIOR = ["Tell me about Vitamin D Syntropy"]


def test_a_follow_up_filters_from_the_product_named_before():
    q = "Can I take it with warfarin?"
    assert app._caution_subjects_asked(q, PRIOR) == ["warfarin", "blood thinner"]
    assert [m.id for m in app._drop_caution_subject_snippets(_matches(), q, PRIOR)] == [
        "m5", "m6"]
    # With no earlier turn there is no product and so no caution: snippets stay.
    ms = _matches()
    assert app._drop_caution_subject_snippets(ms, q) is ms


def test_chat_filters_a_follow_up_from_its_history(spy):
    try:
        app.app.test_client().post(
            "/chat", json={"query": "Can I take it with warfarin?", "level": "executive",
                           "history": [{"role": "user", "content": PRIOR[0]},
                                       {"role": "assistant", "content": "It is a D3 formula."}]},
            buffered=True)
    except _Stop:
        pass
    assert spy == [["m5", "m6"]]


@pytest.mark.parametrize("q, subject", [
    ("Is Vitamin D Syntropy safe with hypothyroidism?", "thyroid"),
    ("Is Vitamin D Syntropy safe with hyperthyroidism?", "thyroid"),
    ("Can I take Vitamin D Syntropy on Pradaxa?", "blood thinner"),
    ("Can I take Vitamin D Syntropy with dabigatran?", "blood thinner"),
    ("Can I take Vitamin D Syntropy on Savaysa?", "blood thinner"),
    ("Can I take Vitamin D Syntropy with edoxaban?", "blood thinner"),
])
def test_other_names_for_a_subject_match(q, subject):
    assert subject in app._caution_subjects_asked(q)


def test_a_product_cut_by_the_cap_brings_no_caution(monkeypatch):
    # Three earlier blocks fill the cap, so Vitamin D Syntropy's caution never reaches
    # the answer and its warfarin subject must not remove the interaction snippets.
    # Estro-Clear and Angiostasis are switched off, so only the cut product names it.
    _opt_out(monkeypatch, "estro-clear", "angiostasis")
    q = "Can I take Iron Out, Estro-Clear, Angiostasis and Vitamin D Syntropy with warfarin?"
    assert "### Vitamin D Syntropy" not in app.named_product_facts_block(q)
    assert app._caution_subjects_asked(q) == []
    ms = _matches()
    assert app._drop_caution_subject_snippets(ms, q) is ms
    # Named first, it is inside the cap and filters.
    q2 = "Can I take Vitamin D Syntropy, Iron Out, Estro-Clear and Angiostasis with warfarin?"
    assert app._caution_subjects_asked(q2) == ["warfarin", "blood thinner"]


def test_chat_prior_turns_are_the_user_turns_of_its_history():
    history = [{"role": "user", "content": PRIOR[0]},
               {"role": "assistant", "content": "It is a D3 formula."}]
    assert app._chat_prior_user_turns(history, "s1") == PRIOR
    assert app._chat_prior_user_turns([], None) == []


# Review round 3: one list of earlier turns, the query_log fallback, failing open,
# punctuation and more spellings.
import sqlite3 as _sqlite3

ALL = [f"m{i}" for i in range(7)]


@pytest.fixture
def logged(monkeypatch, tmp_path):
    """A query_log holding one earlier question for session s-fb."""
    path = tmp_path / "chat_log.db"
    cx = _sqlite3.connect(path)
    cx.execute("CREATE TABLE query_log (id INTEGER PRIMARY KEY, session_id TEXT, "
               "query TEXT, answer TEXT)")
    cx.execute("INSERT INTO query_log (session_id, query, answer) VALUES (?, ?, ?)",
               ("s-fb", PRIOR[0], "It is a D3 formula."))
    cx.commit()
    cx.close()
    monkeypatch.setattr(app, "LOG_DB", path)
    return path


def _post(history, session_id="s-fb"):
    try:
        app.app.test_client().post(
            "/chat", json={"query": "Can I take it with warfarin?", "level": "executive",
                           "history": history, "session_id": session_id}, buffered=True)
    except _Stop:
        pass


def test_with_no_history_the_logged_question_carries_the_product(logged, spy):
    assert app._chat_prior_user_turns([], "s-fb") == PRIOR
    _post([])
    assert spy == [["m5", "m6"]]


def test_an_assistant_only_history_reads_no_log_like_chat(logged, spy):
    # chat() reads query_log only when history is empty, so the filter must not either:
    # the facts block would carry no caution while the snippets were gone.
    history = [{"role": "assistant", "content": "Aloha, how can I help?"}]
    assert app._chat_prior_user_turns(history, "s-fb") == []
    _post(history)
    assert spy == [ALL]


def test_a_failed_log_read_gives_no_turns(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app.db, "connect", _boom)
    assert app._chat_prior_user_turns([], "s-fb") == []


def test_the_filter_fails_open(monkeypatch, spy):
    def _boom(*a, **k):
        raise RuntimeError("facts builder broke")
    monkeypatch.setattr(app, "_caution_subjects_asked", _boom)
    ms = _matches()
    assert app._drop_caution_subject_snippets(ms, "Can I take Vitamin D Syntropy on warfarin?") is ms
    try:
        app.app.test_client().post(
            "/chat", json={"query": "Can I take Vitamin D Syntropy if I am on warfarin?",
                           "level": "executive"}, buffered=True)
    except _Stop:
        pass
    assert spy == [ALL]


@pytest.mark.parametrize("q, subject", [
    ("Is Vitamin D Syntropy safe during pregnancy?", "pregnan"),
    ("Can I take Vitamin D Syntropy with levothyroxine?", "thyroid"),
    ("Can I take Vitamin D Syntropy with Synthroid?", "thyroid"),
    ("Is Vitamin D Syntropy ok with Hashimoto's?", "thyroid"),
    ("Is Brain Cleanse ok while nursing?", "breastfeeding"),
    ("Can I take Brain Cleanse with St. John's Wort?", "st john"),
    ("Can I take Brain Cleanse with St John's wort?", "st john"),
])
def test_more_spellings_match(q, subject):
    assert subject in app._caution_subjects_asked(q)


def test_subject_words_come_from_this_message_only():
    # The earlier turn named warfarin; this one asks only about the dose.
    prior = ["Is Vitamin D Syntropy ok on warfarin?"]
    q = "How much should I take?"
    assert app._caution_subjects_asked(q, prior) == []
    ms = _matches()
    assert app._drop_caution_subject_snippets(ms, q, prior) is ms


SEVEN = ["vitamin-a-syntropy", "clear-the-way", "angiogenx", "angiostasis", "estro-clear",
         "fibrosolve", "phytolacca-americana-oil-roll-on"]


def test_the_seven_cautions_opt_in_where_each_is_recorded():
    # Glen, 2026-10-21: the chat copies these cautions word for word too. Angiostasis
    # read back without the switch quoted one sentence and invented a reason.
    for slug in SEVEN:
        assert PRODUCTS[slug]["chat_caution"] is True, slug
    # Phytolacca alone has no corrections record; the other six must keep theirs.
    assert [s for s in SEVEN if s not in CORRECTIONS] == ["phytolacca-americana-oil-roll-on"]
    for slug in SEVEN[:-1]:
        assert CORRECTIONS[slug]["chat_caution"] is True, slug


def test_the_angiostasis_blood_thinner_answer_carries_the_whole_caution_and_its_rules():
    q = "I'm on a blood thinner. Can I take Angiostasis?"
    b = app.named_product_facts_block(q)
    warning = PRODUCTS["angiostasis"]["warning"]
    assert b.count(warning) == 1
    assert b.count(STAY) == 1 and b.index(STAY) > b.index(warning)
    assert b.count(RULE) == 1 and b.endswith("\n\n" + CLOSING)
    assert "blood thinner" in app._caution_subjects_asked(q)


def test_fibrosolve_s_unlabelled_caution_is_marked_once_in_its_description():
    b = app.named_product_facts_block("Can I take Fibrosolve with warfarin?")
    warning = PRODUCTS["fibrosolve"]["warning"]
    assert "Caution: " + warning in b and b.count(warning) == 1
    assert b.count(STAY) == 1 and b.index(STAY) > b.index(warning)


def test_a_labelled_caution_is_not_labelled_twice():
    b = app.named_product_facts_block("Is Angiostasis safe with a blood thinner?")
    assert "Caution: Caution:" not in b


@pytest.mark.parametrize("q", ["Can I take Fibrosolve with warfarin?",
                               "I'm on Eliquis. Can I take Fibrosolve?"])
def test_an_anticoagulant_caution_answers_a_blood_thinner_question(q):
    assert "blood thinner" in app._caution_subjects_asked(q)


def test_a_nursing_caution_answers_a_breastfeeding_question():
    assert "breastfeeding" in app._caution_subjects_asked("Is Estro-Clear ok while nursing?")
