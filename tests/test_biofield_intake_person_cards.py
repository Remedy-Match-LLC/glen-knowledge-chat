"""Biofield Intake: one card per person, with a status view one button away.

Glen, 2026-11-20: "Restructure the Biofield Intake so each person shows up with all of
that person's items", grouped possible, requested, started, finished, with a name search.
Then: "Let's have this be the default view, with a button to change the view to show
requests first, then possible, then tests started but not finished, finally finished
tests." People are grouped by email; one name under two emails gets two linked cards.
"""
import re

from dashboard.biofield_report_html import render_list_html


def _req(rid, email, name, status="pending_yes"):
    return {"id": rid, "email": email, "name": name, "status": status, "source": "chat",
            "requested_at": "2026-11-19T10:00:00", "quote": ""}


def _test(tid, email, name, done=False):
    return {"test_id": tid, "email": email, "name": name, "date": "2026-11-01",
            "progress": {"all_done": done}}


def _cards(html):
    return re.findall(r"<details class=pcard id=(pc-\d+)[^>]*>(.*?)</details>", html, re.S)


def test_one_card_holds_every_item_for_that_email():
    html = render_list_html([], "", [_test("a1", "Ann@X.com ", "Ann Lee"),
                                     _test("a2", "ann@x.com", "Ann Lee", done=True)],
                            [_req(1, "ann@x.com", "Ann Lee")])
    cards = _cards(html)
    assert len(cards) == 1
    body = cards[0][1]
    assert "/author/a1" in body and "/author/a2" in body and "/request/1/yes" in body
    assert "3 items" in body
    order = [body.index(x) for x in (">Possible<", ">Started, not finished<", ">Finished<")]
    assert order == sorted(order)


def test_each_action_button_exists_once():
    """Round 1 of this build: rendering both views doubled every button."""
    html = render_list_html([], "", [_test("a1", "a@x.com", "A")],
                            [_req(1, "b@x.com", "B"), _req(2, "c@x.com", "C", "requested")])
    assert html.count("/request/1/yes") == 1 and html.count("/request/2/start") == 1
    assert html.count("href='/author/a1'") == 1
    assert html.count("id=req-1 ") + html.count("id=req-1>") == 1


def test_cards_that_need_glen_come_first_and_finished_ones_fold():
    html = render_list_html([], "", [_test("a1", "done@x.com", "Done Only", done=True),
                                     _test("a2", "open@x.com", "Open Test")],
                            [_req(1, "ask@x.com", "Needs Yes")])
    names = re.findall(r"<summary><h3>([^<]+)</h3>", html)
    assert names == ["Needs Yes", "Open Test", "Done Only"]
    tags = re.findall(r"<details class=pcard[^>]*>", html)
    assert " open" in tags[0] and " open" in tags[1] and " open" not in tags[2]


def test_one_name_under_two_emails_gets_two_linked_cards():
    html = render_list_html([], "", [_test("a1", "one@x.com", "Sam Roe"),
                                     _test("a2", "two@x.com", "Sam Roe")], [])
    cards = _cards(html)
    assert len(cards) == 2
    (id0, b0), (id1, b1) = cards
    assert f"href='#{id1}'" in b0 and "two@x.com" in b0
    assert f"href='#{id0}'" in b1 and "one@x.com" in b1


def test_the_status_view_lists_groups_in_glens_order_and_starts_empty():
    html = render_list_html([], "", [_test("a1", "a@x.com", "A")], [_req(1, "b@x.com", "B")])
    st = html[html.index("<div id=intake-status>"):]
    ids = re.findall(r"<tbody id=st-(\w+)>", st)
    assert ids == ["requested", "possible", "started", "finished"]
    assert "data-ord" not in st.split("</div>")[0], "rows live in the cards until moved"


def test_every_row_knows_its_home_group_and_place():
    html = render_list_html([], "", [_test("a1", "a@x.com", "A")], [_req(1, "a@x.com", "A")])
    rows = re.findall(r"<tr [^>]*data-ord=(\d+) data-group=(\w+) data-home=(pc-\d+-\w+)", html)
    assert sorted(int(o) for o, _, _ in rows) == [0, 1]
    for _o, g, home in rows:
        assert home.endswith("-" + g) and f"<tbody id={home}>" in html


def test_the_button_and_search_are_wired():
    html = render_list_html([_t := {"test_id": "9", "name": "Fm Person", "email": "fm@x.com",
                                    "date": "2025", "layer_count": 3}], "",
                            [_test("a1", "a@x.com", "A")], [])
    assert "id=intake-view" in html and "Show by status" in html
    assert "id=intake-search" in html
    assert "localStorage.getItem('intake_view')==='status'" in html
    assert "try{localStorage.setItem('intake_view',v);}catch(e){}" in html
    assert re.search(r'<tr class=fmrow data-search="fm person fm@x.com">', html)
    assert "<div id=intake data-view=cards>" in html, "cards are the default"


def test_no_items_keeps_the_empty_note():
    html = render_list_html([], "", [], [])
    assert "None yet" in html and "pcard" not in html


def test_an_email_with_inner_spaces_still_joins_its_card():
    html = render_list_html([], "", [_test("a1", "ann@x.com", "Ann"),
                                     _test("a2", "ann @x.com", "Ann")], [])
    assert len(_cards(html)) == 1


def test_every_name_on_a_card_is_searchable_and_linked():
    html = render_list_html([], "", [_test("a1", "one@x.com", "Annie Lee"),
                                     _test("a2", "two@x.com", "Ann Lee")],
                            [_req(1, "one@x.com", "Ann Lee")])
    cards = _cards(html)
    assert len(cards) == 2
    tag0 = re.search(r"<details class=pcard id=pc-0[^>]*>", html).group(0)
    assert "annie lee" in tag0 and "ann lee" in tag0
    assert "href='#pc-1'" in cards[0][1], "linked through the second name"


def test_inside_a_card_possible_comes_before_requested():
    html = render_list_html([], "", [], [_req(1, "a@x.com", "A", "requested"),
                                         _req(2, "a@x.com", "A")])
    body = _cards(html)[0][1]
    assert body.index(">Possible<") < body.index(">Requested<")


def test_filemaker_rows_stay_searchable_with_no_new_items():
    html = render_list_html([{"test_id": "9", "name": "Fm", "email": "fm@x.com",
                              "date": "2025", "layer_count": 1}], "", [], [])
    assert "id=intake-search" in html and "class=fmrow" in html
    assert "id=intake-view hidden" in html


# ── Done on a Possible item (Glen, 2026-11-20: "for Possible items, add a Done button") ──

def test_a_possible_item_has_a_done_button_beside_yes_and_no():
    html = render_list_html([], "", [], [_req(1, "a@x.com", "A")])
    assert html.count('data-url="/request/1/done"') == 1
    assert "/request/1/yes" in html and "/request/1/no" in html


def test_a_requested_item_has_no_done_button():
    html = render_list_html([], "", [], [_req(2, "a@x.com", "A", "requested")])
    assert "/request/2/done" not in html


def test_a_done_item_sits_under_finished_without_buttons():
    html = render_list_html([], "", [_test("a1", "a@x.com", "A")],
                            [_req(3, "a@x.com", "A", "done")])
    body = _cards(html)[0][1]
    fin = body[body.index(">Finished<"):]
    assert "id=req-3" in fin and "Done without a test here" in fin
    assert "/request/3/" not in html
    assert "data-group=finished" in re.search(r"<tr id=req-3[^>]*>", html).group(0)
