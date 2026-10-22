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
    assert "show(sec,openIn(k))" in js
    assert "return !!b&&b.getItem(PREFIX+k)==='1'" in js


def test_previously_dispensed_keeps_its_own_control():
    assert "if(c.querySelector('#dispbtn'))return;" in brh._FOLD_JS


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
    assert ".clinical-count,.pill')" in js


def test_applying_never_queues_itself_again():
    js = brh._FOLD_JS
    assert "if(b.textContent!==t)b.textContent=t;" in js
    assert "finally{if(obs)obs.takeRecords()}" in js


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
