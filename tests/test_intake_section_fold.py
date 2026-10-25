"""Every Biofield Intake section can be shown or hidden, like Previously dispensed.
Glen, 2026-10-22: "Start with sections closed. leave open when opened during the session
only, or when returning to that page until the report and invoice is done and client
notified." The behaviour was checked in headless Chrome on a real page; these tests
hold the wiring and the rules it depends on."""
import sqlite3

from dashboard import biofield_report_html as brh
from dashboard import biofield_test_notified as btn


def _page(**kw):
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    return brh.render_author_html(rep, [], "", **kw)


def test_the_intake_page_carries_the_fold_script_once():
    h = _page()
    assert h.count(".fold-hidden{display:none !important}") == 1
    assert h.count("PREFIX='intakeOpen:'+TID+':';") == 1


def test_open_state_is_per_test_and_kept_until_the_client_is_notified():
    h = _page()
    assert "var TID='a7',DONE=false," in h
    assert "return DONE?window.sessionStorage:window.localStorage" in h


def test_after_the_notice_the_tests_saved_opens_are_cleared():
    h = _page(client_notified=True)
    assert "var TID='a7',DONE=true," in h
    assert "if(lk&&lk.indexOf(PREFIX)===0)localStorage.removeItem(lk)" in h


def test_sections_start_closed():
    js = brh._FOLD_JS
    assert "if(s.disp)return;var k=s.k,open=openIn(k);" in js
    assert "return !!b&&b.getItem(PREFIX+k)==='1'" in js


def test_previously_dispensed_keeps_its_own_control_and_is_in_the_bar():
    js = brh._FOLD_JS
    assert "if(c.querySelector('#dispbtn')){out.push({h:c,body:[],disp:true});return}" in js
    assert "list.forEach(function(s){if(s.disp)return;" in js


def test_a_pinned_bar_lists_every_section_with_open_and_close_all():
    js = brh._FOLD_JS
    assert "#intakeNav{position:sticky;top:40px;" in js
    assert "[['Open all',true],['Close all',false]]" in js
    assert "if(cur)navClick(cur)" in js


def test_each_section_has_a_bar_above_and_below():
    js = brh._FOLD_JS
    assert "var top=s.h,bits=headBar(top,k);" in js and "bot=bar('fold-bottom',k," in js
    assert "setText(bot.firstChild,'▴ Hide '+s.label);" in js


def test_a_closed_section_shows_a_one_line_count():
    js = brh._FOLD_JS
    assert "setText(bits.sum,open||s.h.querySelector('.clinical-count')?'':summary(s));" in js
    assert "if(boxes.length>1)return boxes.filter(function(x){return x.checked}).length+' of '+boxes.length+' ticked';" in js


def test_opening_closing_and_jumping_keep_the_page_still():
    js = brh._FOLD_JS
    assert "function steady(el,change){" in js
    assert "steady(h,function(){set(key,!openIn(key))})" in js
    assert ".rail{top:calc(52px + var(--intake-nav-h,0px)) !important}" in js


def test_storage_failures_never_break_the_page():
    js = brh._FOLD_JS
    assert "function store(k,open){try{" in js and "function openIn(k){try{" in js


def test_late_panels_are_covered():
    js = brh._FOLD_JS
    assert "el.id==='e4lpanel'||el.id==='stresspanel'||el.id==='suggestpanel'" in js
    assert ".observe(wrap,{childList:true,subtree:true})" in js


def test_a_count_or_badge_in_a_heading_does_not_change_its_key():
    js = brh._FOLD_JS
    assert ".replace(/[0-9]+/g,'')" in js
    assert ".clinical-count,.pill,.fold-bar,.fold-bit')" in js


def test_applying_never_queues_itself_again():
    js = brh._FOLD_JS
    assert "function setText(el,t){if(el.textContent!==t)el.textContent=t}" in js
    assert "finally{if(obs)obs.takeRecords()}" in js
    # observe() returns nothing: assigned in one line, obs stayed null and the guard
    # above never ran (found 2026-10-22).
    assert "obs=new MutationObserver(later);\nobs.observe(wrap," in js


def test_a_test_id_cannot_break_out_of_the_script():
    rep = {"test_id": "a7'</script>", "client": {}, "date": "", "layers": [], "schedule": []}
    h = brh.render_author_html(rep, [], "")
    # Only letters, digits, - and _ survive, so no quote or tag reaches the script.
    assert "var TID='a7x27ltscriptgt',DONE=false," in h


def test_notified_is_recorded_once_and_read_back():
    cx = sqlite3.connect(":memory:")
    assert btn.notified_at(cx, "a7") == ""
    btn.mark(cx, "a7")
    first = btn.notified_at(cx, "a7")
    btn.mark(cx, "a7")
    assert first and btn.notified_at(cx, "a7") == first


def test_publish_records_the_notice_only_when_the_email_was_delivered():
    src = open(__import__("biofield_local_app").__file__).read()
    assert '            if res.get("emailed"):\n                try:\n                    from dashboard import biofield_test_notified as _btn' in src
    assert "client_notified=bool(_notified))" in src


def test_a_jump_clears_the_bars_and_dropdowns_sit_above_them():
    js = brh._FOLD_JS
    assert "var gap=top.getBoundingClientRect().top-navBottom()-8;if(gap<0)window.scrollBy(0,gap)}" in js
    assert ".dd{z-index:9500 !important}" in js


def test_the_title_is_the_top_bar():
    # Glen, 2026-10-22: the show/hide bar contains the section's title.
    js = brh._FOLD_JS
    assert "h.classList.add('fold-top','fold-head')" in js
    assert "if(c&&c!==h&&h.contains(c))return;toggle()" in js


def test_a_bar_item_opens_a_closed_section_and_closes_an_open_one():
    # Glen, 2026-10-24: a closed item opens and scrolls to it; an open one only closes.
    js = brh._FOLD_JS
    assert "if(cur)navClick(cur)" in js and "if(cur)goTo(cur)" not in js
    assert "function navClick(s){if(!isOpen(s)){goTo(s);return}" in js
    assert "steady(firstVisibleTop(),function(){if(s.disp)" in js
    assert "else set(s.k,false)})}" in js
