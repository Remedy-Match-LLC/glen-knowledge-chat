"""PRL section in the Biofield Intake scan panel (Glen, 2026-10-17).

"Unchecked checkboxes for each item and each group. Checking an item automatically
checks that group. Add a second checkbox for each group that checks all items in
that group." "Unchecking all unchecks every item in the group, except any that had
been individually checked."
"""
import json
import os
import sqlite3

os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("PINECONE_API_KEY", "dummy")

import pytest

from dashboard import biofield_clinical_sections as cs
from dashboard import biofield_prl_section as ps
from dashboard.biofield_report_html import render_e4l_panel, render_prl_section

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANCREAS = 53                      # seed: PancreVen, Digest Premier, Apple Cider Vinegar
CODES = ["ED12", "EI10", "ET7"]    # three of the seed's items for Pancreas


@pytest.fixture
def cx(tmp_path):
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    yield con
    con.close()


def _area(cx, test_id="a49", fa=PANCREAS, codes=CODES):
    return next(a for a in ps.view(cx, test_id, codes)["areas"] if a["focus_area_id"] == fa)


def _names(fa=PANCREAS):
    return [p["name"] for p in ps.products_for(fa)]


def _ticked(area):
    return {i["name"]: i["via"] for i in area["items"] if i["checked"]}


# --- the formulation beside each product: the portal's rule ---------------

def test_ff_matches_the_portal_for_every_seed_product():
    import app
    seed = json.load(open(os.path.join(ROOT, "data", "prl_seed.json")))["products"]
    for p in seed:
        assert ps.ff_view(p.get("best_ff"), p.get("relation")) == \
            app._prl_ff_view(p.get("best_ff"), p.get("relation")), p["name"]


@pytest.mark.parametrize("name", ["Electrolyte Mineral Manna", "AllerFree", "Fungifuge"])
def test_never_recommended_products_are_never_the_ff(name):
    assert ps.ff_view(name, "equivalent") is None


# --- the boxes -------------------------------------------------------------

def test_everything_starts_unchecked(cx):
    area = _area(cx)
    assert not area["checked"] and not area["all_on"] and not _ticked(area)


def test_ticking_an_item_ticks_its_group(cx):
    ps.act(cx, "a49", PANCREAS, "item_on", _names()[0])
    area = _area(cx)
    assert area["checked"] and _ticked(area) == {_names()[0]: "item"}


def test_all_ticks_every_item_and_unticking_all_keeps_the_individual_ones(cx):
    first, *rest = _names()
    ps.act(cx, "a49", PANCREAS, "item_on", first)
    ps.act(cx, "a49", PANCREAS, "all_on")
    area = _area(cx)
    assert area["all_on"] and set(_ticked(area)) == set(_names())
    ps.act(cx, "a49", PANCREAS, "all_off")
    area = _area(cx)
    assert _ticked(area) == {first: "item"} and area["checked"] and not area["all_on"]


def test_unticking_all_with_nothing_individual_unticks_the_group(cx):
    ps.act(cx, "a49", PANCREAS, "all_on")
    ps.act(cx, "a49", PANCREAS, "all_off")
    area = _area(cx)
    assert not _ticked(area) and not area["checked"]


def test_unticking_the_last_item_unticks_a_group_only_items_ticked(cx):
    ps.act(cx, "a49", PANCREAS, "item_on", _names()[0])
    ps.act(cx, "a49", PANCREAS, "item_off", _names()[0])
    assert not _area(cx)["checked"]


def test_a_group_ticked_by_itself_stays_ticked_and_raises_the_flag(cx):
    ps.act(cx, "a49", PANCREAS, "group_on")
    ps.act(cx, "a49", PANCREAS, "item_on", _names()[0])
    ps.act(cx, "a49", PANCREAS, "item_off", _names()[0])
    area = _area(cx)
    assert area["checked"] and area["empty"]


def test_unticking_the_group_unticks_its_items(cx):
    ps.act(cx, "a49", PANCREAS, "all_on")
    ps.act(cx, "a49", PANCREAS, "group_off")
    area = _area(cx)
    assert not area["checked"] and not _ticked(area) and not area["all_on"]


def test_ticks_belong_to_one_client(cx):
    ps.act(cx, "a49", PANCREAS, "all_on")
    assert not _ticked(_area(cx, "a50"))


def test_a_product_from_another_area_is_refused(cx):
    with pytest.raises(ValueError):
        ps.act(cx, "a49", PANCREAS, "item_on", "Not A Pancreas Product")


# --- ratings ----------------------------------------------------------------

def test_ratings_sort_each_area_and_x_is_ten(cx):
    a, b, c = _names()
    ps.upsert_rating(cx, PANCREAS, {"name": a, "overall": 7, "research": 5, "uniqueness": 8})
    ps.upsert_rating(cx, PANCREAS, {"name": c, "overall": 10, "research": 2, "uniqueness": 1})
    area = _area(cx)
    assert [(i["name"], i["rating"]) for i in area["items"]] == [(c, "X21"), (a, "758"), (b, "")]


def test_an_unreviewed_rating_shows_only_on_its_client_until_approved(cx):
    a = _names()[0]
    ps.upsert_rating(cx, PANCREAS, {"name": a, "overall": 7, "research": 5, "uniqueness": 8},
                     origin_test="a49")
    mine = next(i for i in _area(cx)["items"] if i["name"] == a)
    theirs = next(i for i in _area(cx, "a50")["items"] if i["name"] == a)
    assert mine["rating"] == "758" and not mine["reviewed"] and theirs["rating"] == ""
    assert ps.approve_rating(cx, PANCREAS, a)
    assert next(i for i in _area(cx, "a50")["items"] if i["name"] == a)["rating"] == "758"


def test_a_per_client_rating_edit(cx):
    a = _names()[0]
    ps.upsert_rating(cx, PANCREAS, {"name": a, "overall": 7, "research": 5, "uniqueness": 8})
    ps.act(cx, "a49", PANCREAS, "rating", a, "x99")
    assert next(i for i in _area(cx)["items"] if i["name"] == a)["rating"] == "X99"
    assert next(i for i in _area(cx, "a50")["items"] if i["name"] == a)["rating"] == "758"
    with pytest.raises(ValueError):
        ps.act(cx, "a49", PANCREAS, "rating", a, "0")


def test_the_botanical_switch_hides_botanical_products(cx):
    a = _names()[0]
    ps.upsert_rating(cx, PANCREAS, {"name": a, "overall": 7, "research": 5, "uniqueness": 8,
                                    "botanical": True})
    cs.save_botanical_setting(cx, "a49", avoid=True, exceptions="ginger")
    area = _area(cx)
    # Glen, 2026-10-18: "show the hidden products grayed out".
    assert [i["name"] for i in area["items"] if i.get("hidden")] == [a]
    assert area["botanical_hidden"] == 1


def _greyed_first(cx):
    a = _names()[0]
    ps.upsert_rating(cx, PANCREAS, {"name": a, "overall": 7, "research": 5, "uniqueness": 8,
                                    "botanical": True}, origin_test="a49")
    cs.save_botanical_setting(cx, "a49", avoid=True, exceptions="")
    return a


def test_all_never_ticks_a_greyed_product(cx):
    a = _greyed_first(cx)
    ps.act(cx, "a49", PANCREAS, "all_on")
    area = _area(cx)
    assert a not in _ticked(area)
    assert sorted(_ticked(area)) == sorted(n for n in _names() if n != a)
    assert [i["name"] for i in area["items"] if i.get("hidden")] == [a]


def test_ticking_a_greyed_product_unhides_and_selects_it_and_it_stays_unhidden(cx):
    a = _greyed_first(cx)
    ps.act(cx, "a49", PANCREAS, "item_on", a)
    item = next(i for i in _area(cx)["items"] if i["name"] == a)
    assert item["checked"] and not item.get("hidden")
    # Unticked by hand, it stays unhidden, and All may tick it from then on.
    ps.act(cx, "a49", PANCREAS, "item_off", a)
    item = next(i for i in _area(cx)["items"] if i["name"] == a)
    assert not item["checked"] and not item.get("hidden")
    ps.act(cx, "a49", PANCREAS, "all_on")
    assert a in _ticked(_area(cx))


def test_a_product_ticked_by_hand_stays_when_all_is_unticked(cx):
    a = _greyed_first(cx)
    ps.act(cx, "a49", PANCREAS, "item_on", a)
    ps.act(cx, "a49", PANCREAS, "all_on")
    ps.act(cx, "a49", PANCREAS, "all_off")
    assert list(_ticked(_area(cx))) == [a]


def test_unhiding_is_for_one_client_only(cx):
    a = _greyed_first(cx)
    cs.save_botanical_setting(cx, "a50", avoid=True, exceptions="")
    ps.approve_rating(cx, PANCREAS, a)
    ps.act(cx, "a49", PANCREAS, "item_on", a)
    assert [i["name"] for i in _area(cx, "a50")["items"] if i.get("hidden")] == [a]


def test_a_greyed_product_renders_greyed_with_a_working_box(cx):
    from dashboard.biofield_report_html import render_prl_section
    a = _greyed_first(cx)
    html = render_prl_section(ps.view(cx, "a49", CODES))
    assert html.count("class='prl-item greyed'") == 1
    assert "a tick unhides and selects one" in html
    start = html.index("class='prl-item greyed'")
    assert "disabled" not in html[start:html.index("</div>", start)]


# --- the empty-group flag ----------------------------------------------------

def _josie_conditions(cx):
    cs.upsert_item(cx, "Blood sugar", "foods_emphasize",
                   {"name": "Beans", "overall": 9, "research": 8, "uniqueness": 6})
    cs.upsert_item(cx, "Blood sugar", "nutrients",
                   {"name": "Chromium", "overall": 7, "research": 6, "uniqueness": 5,
                    "dose": "200 mcg"})
    ps.save_condition_map(cx, PANCREAS, ["blood sugar", "gut"])
    ps.remember_rows(cx, "a49", ["Blood sugar"])


def test_the_flag_draws_on_her_linked_conditions(cx):
    _josie_conditions(cx)
    ps.act(cx, "a49", PANCREAS, "group_on")
    area = _area(cx)
    assert area["linked"] == ["Blood sugar"]
    assert [i["name"] for i in area["foods"]["emphasize"]] == ["Beans"]
    assert [(i["name"], i["dose"]) for i in area["nutrients"]] == [("Chromium", "200 mcg")]
    ps.act(cx, "a49", PANCREAS, "empty_choice", value="nutrients")
    html = render_prl_section(ps.view(cx, "a49", CODES))
    assert "Ticked with no product." in html and "Chromium, 200 mcg" in html
    assert "value=nutrients checked" in html


def test_a_linked_condition_the_client_does_not_have_is_not_used(cx):
    _josie_conditions(cx)
    ps.remember_rows(cx, "a49", ["Vertigo"])
    ps.act(cx, "a49", PANCREAS, "group_on")
    area = _area(cx)
    assert area["linked"] == [] and area["nutrients"] == []
    html = render_prl_section(ps.view(cx, "a49", CODES))
    assert "matches none of her Clinical Summary conditions" in html
    assert "value=foods disabled" in html and "value=nutrients disabled" in html
    assert "value=bare " in html and "value=omit " in html


def test_an_unknown_choice_is_refused(cx):
    with pytest.raises(ValueError):
        ps.act(cx, "a49", PANCREAS, "empty_choice", value="sometimes")


# --- the panel -----------------------------------------------------------------

def test_a_scan_with_no_focus_area_says_so():
    html = render_prl_section({"areas": [], "prl_link": ps.PRL_LINK})
    assert "This scan hits none of the PRL focus areas." in html


def test_prl_sits_between_infoceuticals_and_stresses(cx):
    prl = render_prl_section(ps.view(cx, "a49", CODES))
    html = render_e4l_panel({"found": True, "status": "fresh", "message": "m",
                             "infoceuticals": [{"code": "ED12", "name": "Kidney"}],
                             "stresses": [{"code": "MR1", "name": "x"}]}, prl_html=prl)
    assert html.index("Infoceuticals") < html.index("PRL supplements") < html.index("Stresses")
    assert "https://truly.vip/prl" in html


def test_the_display_name_replaces_e4ls_name(cx):
    ps.save_condition_map(cx, PANCREAS, [], display_name="Pancreas and blood sugar")
    assert _area(cx)["name"] == "Pancreas and blood sugar"


# --- the route and the loader --------------------------------------------------

def test_prl_route(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    client = create_app(db).test_client()
    ok = client.post("/author/a49/prl", json={"focus_area_id": PANCREAS, "action": "all_on"})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    assert client.post("/author/a49/prl", json={"action": "all_on"}).status_code == 400
    assert client.post("/author/a49/prl", json={"focus_area_id": PANCREAS,
                                                "action": "explode"}).status_code == 400
    with sqlite3.connect(db) as con:
        assert set(_ticked(_area(con))) == set(_names())


def test_loader_refuses_a_name_not_in_the_seed_and_rolls_back(tmp_path):
    from scripts.load_prl_content import load
    folder = tmp_path / "prl"
    folder.mkdir()
    a = _names()[0]
    (folder / "prl-ratings.json").write_text(json.dumps({"focus_areas": [
        {"focus_area_id": PANCREAS, "focus_area_name": "Pancreas", "items": [
            {"name": a, "overall": 7, "research": 5, "uniqueness": 8, "josie_note": "n"},
            {"name": "Invented Product", "overall": 9, "research": 9, "uniqueness": 9}]}]}))
    (folder / "focus-area-conditions.json").write_text(json.dumps({"focus_areas": [
        {"focus_area_id": PANCREAS, "condition_keys": ["blood sugar"]},
        {"focus_area_id": 7, "focus_area_name": "Memory", "condition_keys": []}]}))
    con = sqlite3.connect(str(tmp_path / "chat_log.db"))
    report = load(con, str(folder), "a49")
    assert report["ratings_added"] == 1 and report["notes"] == 1
    assert report["not_in_seed"] == [f"{PANCREAS}: Invented Product"]
    assert report["areas_mapped"] == 1 and report["areas_unmapped"] == ["Memory"]
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM biofield_prl_rating").fetchone() == (0,)
