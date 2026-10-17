"""Mind is split into Symptoms, Labs and Conditions, and a stress reads once.

Glen, 2026-10-16, via the ai-training-fc tab: "Let's categorize within Mind: Symptoms,
Labs, Conditions. And let's eliminate the duplication in each line." Asked where a
stress that is none of the three goes, he answered "Symptoms".
"""
import json
import sqlite3

import pytest

from dashboard import stress_kinds as sk
from dashboard.biofield_report_html import render_stress_panel, render_report_html
from dashboard.biofield_stress import add_stress, init_stress_tables, list_stresses


def _s(i, source, label, code="", kind=None):
    d = {"id": i, "source": source, "label": label, "code": code, "balanced": False,
         "balance": "required"}
    if kind:
        d["kind"] = kind
    return d


def test_mind_lists_symptoms_then_labs_then_conditions():
    html = render_stress_panel({"by_layer": [], "unassigned": [
        _s(1, "comm", "Cataract", kind="condition"),
        _s(2, "comm", "Fatigue", kind="symptom"),
        _s(3, "comm", "Ferritin high", kind="lab"),
        _s(4, "scan", "Liver", code="LV1")]})
    i = html.index
    assert i("Mind Observations") < i("data-kind=symptom") < i("Fatigue") \
        < i("data-kind=lab") < i("Ferritin high") < i("data-kind=condition") < i("Cataract")
    # Body has no subgroups.
    assert html.count("stress-subgroup") == 3


def test_unsorted_goes_under_symptoms_and_empty_subgroups_are_hidden():
    html = render_stress_panel({"by_layer": [], "unassigned": [
        _s(1, "paste", "Grief")]})
    assert "data-kind=symptom" in html and "data-kind=lab" not in html
    assert "data-kind=condition" not in html


def test_a_mined_stress_reads_once():
    html = render_stress_panel({"by_layer": [], "unassigned": [
        _s(1, "comm", "anemia", code="anemia"),
        _s(2, "tag", "Low Energy", code="pb:low-energy"),
        _s(3, "scan", "Liver congestion", code="LV12"),
        _s(4, "scan", "Spleen", code="spleen")]})
    assert "anemia anemia" not in html and "<b>anemia</b>" not in html
    assert "pb:low-energy" not in html
    assert "<b>LV12</b> Liver congestion" in html
    assert "<b>spleen</b>" not in html


def test_the_report_lists_a_balanced_stress_once():
    rep = {"client": {"name": "Pat"}, "layers": [], "test_id": "a1"}
    html = render_report_html(rep, stresses={"balanced": [
        {"code": "anemia", "label": "anemia", "source": "comm", "balanced_by": "Iron Out"}]})
    assert "<b>anemia</b>" not in html and "anemia" in html


@pytest.fixture()
def cx():
    c = sqlite3.connect(":memory:")
    init_stress_tables(c)
    return c


def test_list_stresses_marks_each_mind_stress_with_its_kind(cx):
    for label, source in (("cataract", "comm"), ("fatigue", "tag"), ("grief", "voice"),
                          ("Eyes", "clinical")):
        add_stress(cx, "a1", label, source=source)
    sk.ensure_schema(cx)
    cx.execute("INSERT INTO stress_kind(label_key,kind) VALUES('cataract','condition')")
    data = list_stresses(cx, "a1", [])
    kinds = {i["label"]: i.get("kind") for i in data["active"]}
    assert kinds == {"cataract": "condition", "fatigue": "symptom", "grief": None,
                     "Eyes": "condition"}
    assert data["unsorted"] == 1            # fatigue: asked about, not answered yet


def test_sort_asks_once_and_keeps_only_valid_answers(cx):
    calls = []

    def complete(system, user):
        calls.append(user)
        return json.dumps({"kinds": {"1": "Condition", "2": " lab", "3": "nonsense",
                                     "9": "lab", "x": "lab"}})
    labels = ["Cataract", "Ferritin high", "Grief", "Tinnitus"]
    assert sk.classify(cx, labels, complete) == 4
    # A wrong answer or no answer is a symptom (Glen: none of these -> Symptoms), and is
    # kept, so it is not asked about again.
    assert sk.known_kinds(cx, labels) == {"cataract": "condition", "ferritin high": "lab",
                                          "grief": "symptom", "tinnitus": "symptom"}
    items = [{"label": l, "source": "comm"} for l in labels]
    assert sk.unsorted_labels(cx, items) == []
    assert sk.classify(cx, [], complete) == 0 and len(calls) == 1
    assert sk.parse_reply("not json", labels) == {}
    assert sk.parse_reply(json.dumps({"kinds": []}), labels) == {}


def test_a_kind_once_decided_is_not_overwritten(cx):
    sk.classify(cx, ["anemia"], lambda s, u: json.dumps({"kinds": {"1": "condition"}}))
    sk.classify(cx, ["anemia"], lambda s, u: json.dumps({"kinds": {"1": "lab"}}))
    assert sk.known_kinds(cx, ["anemia"]) == {"anemia": "condition"}


# --- the route ---------------------------------------------------------------------

from biofield_local_app import create_app  # noqa: E402

_NONE = {"status": "none", "found": False, "findings": [], "days_ago": None, "fresh": False}


@pytest.fixture(autouse=True)
def _no_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def test_the_sort_route_sorts_and_the_panel_shows_it(tmp_path):
    db = str(tmp_path / "c.db")

    def complete(system, user):
        if "sort a client's health stress labels" in system:
            return json.dumps({"kinds": {"1": "condition", "2": "lab"}})
        return json.dumps({"stresses": []})
    c = create_app(db, scan_lookup=lambda e: _NONE, fetch_profile=lambda e: {},
                   fetch_recent_comms=lambda e: {}, interpret_complete=complete,
                   client_gmail=None, records_reader=None).test_client()
    tid = c.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    with sqlite3.connect(db) as x:
        add_stress(x, tid, "cataract", source="comm")
        add_stress(x, tid, "ferritin high", source="comm")
    assert c.get(f"/author/{tid}/stresses").get_json()["data"]["unsorted"] == 2
    assert c.post(f"/author/{tid}/stresses/sort").get_json() == {"ok": True, "sorted": 2}
    j = c.get(f"/author/{tid}/stresses").get_json()
    assert j["data"]["unsorted"] == 0
    html = j["html"]
    assert html.index("data-kind=lab") < html.index("ferritin high") \
        < html.index("data-kind=condition") < html.index("cataract")
    assert c.post(f"/author/{tid}/stresses/sort").get_json() == {"ok": True, "sorted": 0}



def test_each_batch_is_kept_before_the_next_call(cx):
    labels = [f"label {i}" for i in range(sk.BATCH + 5)]
    seen = []

    def complete(system, user):
        seen.append(cx.in_transaction)
        if len(seen) == 2:
            raise RuntimeError("down")
        return json.dumps({"kinds": {str(n): "symptom" for n in range(1, sk.BATCH + 1)}})
    with pytest.raises(RuntimeError):
        sk.classify(cx, labels, complete)
    cx.rollback()
    assert seen == [False, False]
    assert len(sk.known_kinds(cx, labels)) == sk.BATCH
