"""Live session part 2b: the hands-free segmenter (segment_utterance).

Glen, 14 November 2026, "yes" to the cue words: "head", "tail" and "remedy" each start
that field, "head and tail" fills both, "balanced" ticks the layer, "next layer" starts a
new one. Ruling 9: a dose spoken after a remedy fills dose and frequency. The rule itself
is written out above segment_utterance in dashboard/biofield_layer_match.py.
"""
import sqlite3

import pytest

from dashboard.biofield_authoring import init_auth_tables
from dashboard.biofield_layer_match import handsfree_events, segment_utterance


def ev(text, **kw):
    return segment_utterance(text, **kw)["events"]


def f(kind, text, both=False):
    return {"field": kind, "text": text, "both": both}


# ---- each cue word ---------------------------------------------------------------

@pytest.mark.parametrize("said,kind,words", [
    ("Head liver congestion.", "head", "liver congestion"),
    ("head, liver congestion", "head", "liver congestion"),
    ("HEAD is liver congestion", "head", "liver congestion"),
    ("Tail gut.", "tail", "gut"),
    ("Remedy Sulfur Syntropy.", "remedy", "Sulfur Syntropy"),
])
def test_each_field_cue_starts_that_field(said, kind, words):
    assert ev(said) == [f(kind, words)]


def test_head_and_tail_takes_precedence_over_head():
    assert ev("Head and tail liver congestion.") == [f("head", "liver congestion", True)]
    assert ev("head & tail, mold") == [f("head", "mold", True)]


def test_head_and_tail_said_after_the_stress_fills_both():
    """Ruling 5: either order."""
    assert ev("Liver congestion, head and tail.") == [f("head", "Liver congestion", True)]


def test_pressure_in_the_head_is_not_split():
    assert ev("Pressure in the Head") == []                 # no cue at its start
    assert ev("Head pressure in the head.") == [f("head", "pressure in the head")]
    assert ev("Tail, pressure in the head") == [f("tail", "pressure in the head")]
    # Mid-sentence "head and tail" is not a cue position either.
    assert ev("The head and tail of it is the gut") == []


def test_cue_words_are_whole_words():
    assert ev("Headache is bad.") == []
    assert ev("Remedies help.") == []
    assert ev("Tailbone pain") == []


def test_a_cue_after_a_sentence_boundary_splits():
    assert ev("We'll look at it now. Head kidney. Tail bladder!") == [
        f("head", "kidney"), f("tail", "bladder")]


def test_a_dot_inside_a_number_or_a_time_is_not_a_boundary():
    assert ev("Remedy liver detox 2.5 ml at 8:30") == [f("remedy", "liver detox 2.5 ml at 8:30")]


# ---- balanced ----------------------------------------------------------------------

@pytest.mark.parametrize("said", ["Balanced.", "balanced", "This layer is balanced.",
                                  "Layer balanced!"])
def test_balanced_alone_ticks_the_layer(said):
    assert ev(said) == [{"balanced": True}]


@pytest.mark.parametrize("said", ["Unbalanced hormones.", "Balanced hormones.",
                                  "It is not balanced yet"])
def test_balanced_inside_other_words_is_not_a_cue(said):
    assert ev(said) == []


def test_balanced_hormones_after_a_head_cue_is_the_heads_text():
    assert ev("Head, unbalanced hormones.") == [f("head", "unbalanced hormones")]
    assert ev("Head balanced hormones") == [f("head", "balanced hormones")]


# ---- next layer --------------------------------------------------------------------

def test_next_layer_and_a_cue_straight_after_it():
    assert ev("Next layer.") == [{"next_layer": True}]
    assert ev("Next layer, head kidney. Tail bladder.") == [
        {"next_layer": True}, f("head", "kidney"), f("tail", "bladder")]


def test_a_full_layer_in_one_utterance_keeps_its_order():
    assert ev("Head liver. Tail gut. Remedy Sobopla. Balanced. Next layer.") == [
        f("head", "liver"), f("tail", "gut"), f("remedy", "Sobopla"),
        {"balanced": True}, {"next_layer": True}]


# ---- a cue with nothing after it ----------------------------------------------------

def test_a_bare_cue_leaves_the_field_open_for_the_next_utterance():
    out = segment_utterance("Head.")
    assert out == {"events": [], "pending": "head", "after_remedy": False}
    nxt = segment_utterance("Liver congestion", pending=out["pending"])
    assert nxt["events"] == [f("head", "Liver congestion")] and nxt["pending"] is None
    # The open field takes one sentence only; after that, plain talk writes nothing.
    assert segment_utterance("And how are you feeling?", pending=None)["events"] == []


def test_a_bare_head_and_tail_fills_both_next():
    out = segment_utterance("Head and tail.")
    assert out["pending"] == "head_tail"
    assert ev("mold", pending="head_tail") == [f("head", "mold", True)]


def test_next_layer_and_balanced_close_an_open_field():
    assert segment_utterance("Head. Next layer.")["pending"] is None
    assert segment_utterance("Remedy. Balanced.")["pending"] is None


# ---- a dose after a remedy (ruling 9) ------------------------------------------------

def test_a_dose_sentence_after_a_remedy_joins_it_in_the_same_utterance():
    assert ev("Remedy Sobopla. Two drops twice a day.") == [
        f("remedy", "Sobopla Two drops twice a day")]


def test_a_dose_in_the_next_utterance_is_a_dose_event():
    first = segment_utterance("Remedy Sobopla.")
    assert first["after_remedy"] is True
    nxt = segment_utterance("Two drops twice a day.", after_remedy=first["after_remedy"])
    assert nxt["events"] == [{"field": "dose", "text": "Two drops twice a day",
                              "dose": "2 drops", "frequency": "twice a day"}]
    assert nxt["after_remedy"] is True             # more dose may follow


def test_dose_sentences_keep_attaching_to_the_remedy():
    """Review of c031051e: "Twice a day" was lost after "Two drops"."""
    assert ev("Remedy Sobopla. Two drops. Twice a day.") == [
        f("remedy", "Sobopla Two drops Twice a day")]
    nxt = segment_utterance("Two drops. Twice a day.", after_remedy=True)
    assert nxt["events"] == [{"field": "dose", "text": "Two drops Twice a day",
                              "dose": "2 drops", "frequency": "twice a day"}]
    # Across utterances too.
    a = segment_utterance("Remedy Sobopla. Two drops.")
    b = segment_utterance("Twice a day.", after_remedy=a["after_remedy"])
    assert b["events"] == [{"field": "dose", "text": "Twice a day", "dose": "",
                            "frequency": "twice a day"}]


def test_plain_talk_or_a_cue_ends_the_remedys_dose():
    assert ev("How are you? Two drops.", after_remedy=True) == []
    assert segment_utterance("How are you?", after_remedy=True)["after_remedy"] is False
    assert segment_utterance("Remedy Sobopla. Head liver.")["after_remedy"] is False


@pytest.mark.parametrize("said", ["Okay, head liver.", "ok head liver", "So head liver",
                                  "Um, uh, head liver", "Alright now head liver",
                                  "And head liver"])
def test_a_filler_word_before_a_cue_is_skipped(said):
    assert ev(said) == [f("head", "liver")]


def test_filler_and_plain_talk_still_write_nothing():
    assert ev("Okay, how are you feeling today?") == []
    assert ev("So that is pressure in the head") == []
    assert ev("Okay, balanced.") == [{"balanced": True}]
    assert ev("Sobopla is good", ) == []


def test_a_dose_with_no_remedy_before_it_writes_nothing():
    assert ev("Two drops twice a day.") == []
    assert ev("Head liver. Two drops twice a day.") == [f("head", "liver")]


# ---- empty --------------------------------------------------------------------------

@pytest.mark.parametrize("said", ["", "   ", None, ". , !"])
def test_empty_input(said):
    assert segment_utterance(said) == {"events": [], "pending": None, "after_remedy": False}


def test_a_junk_pending_value_is_ignored():
    assert segment_utterance("liver", pending="nonsense")["events"] == []


# ---- matching each event -------------------------------------------------------------

@pytest.fixture
def cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "t.db"))
    init_auth_tables(cx)
    cx.execute("CREATE TABLE fmp_snap_products(id_pk TEXT,product_name TEXT,dosage TEXT,"
               "dosage_freq TEXT,dosage_timing TEXT)")
    cx.execute("INSERT INTO fmp_snap_products VALUES('1','Sulfur Syntropy','1 capsule',"
               "'twice a day','with food, or as guided.')")
    cx.execute("CREATE TABLE fmp_snap_client_active_main_stress(id_pk TEXT, main_stress TEXT)")
    cx.executemany("INSERT INTO fmp_snap_client_active_main_stress VALUES(?,?)",
                   [("1", "Liver Congestion"), ("2", "Pressure in the Head")])
    cx.commit()
    return cx


def test_each_field_event_is_matched_with_the_cue_already_off(cx):
    out = handsfree_events(cx, "Head pressure in the head. Remedy sulfur centropy 10 drops daily.")
    head, rem = out["events"]
    assert head["match"] == {"value": "Pressure in the Head", "matched": True,
                             "also_tail": False}
    assert rem["match"]["value"] == "Sulfur Syntropy" and rem["match"]["matched"] is True
    assert rem["match"]["dosing"] == {"dosage": "10 drops", "frequency": "daily",
                                      "timing": "with food, or as guided."}


def test_an_unmatched_phrase_keeps_its_words(cx):
    m = handsfree_events(cx, "Tail zzqx florp")["events"][0]["match"]
    assert m["matched"] is False and m["value"] == "Zzqx Florp"
