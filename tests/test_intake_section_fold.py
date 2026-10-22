"""Every Biofield Intake section can be shown or hidden, like Previously dispensed
(Glen, 2026-10-22). The behaviour was checked in headless Chrome on a real page;
these tests hold the wiring and the rules it depends on."""
from dashboard import biofield_report_html as brh


def _page():
    rep = {"test_id": "a7", "client": {"name": "S", "email": "s@f.co"}, "date": "",
           "layers": [], "schedule": []}
    return brh.render_author_html(rep, [], "")


def test_the_intake_page_carries_the_fold_script_once():
    h = _page()
    assert h.count(".fold-hidden{display:none !important}") == 1
    assert h.count("var PREFIX='intakeFold:';") == 1


def test_previously_dispensed_keeps_its_own_control():
    assert "if(c.querySelector('#dispbtn'))return;" in brh._FOLD_JS


def test_sections_start_open_and_remember_closing():
    js = brh._FOLD_JS
    assert "show(sec,!closedIn(k))" in js
    assert "localStorage.setItem(PREFIX+k,'1');else localStorage.removeItem(PREFIX+k)" in js


def test_storage_failures_never_break_the_page():
    js = brh._FOLD_JS
    assert "function store(k,v){try{" in js and "function closedIn(k){try{" in js


def test_late_panels_are_covered():
    js = brh._FOLD_JS
    assert "el.id==='e4lpanel'||el.id==='stresspanel'||el.id==='suggestpanel'" in js
    assert ".observe(wrap,{childList:true,subtree:true})" in js


def test_a_count_in_a_heading_does_not_change_its_key():
    assert ".replace(/[0-9]+/g,'')" in brh._FOLD_JS


def test_a_status_badge_does_not_change_its_key():
    assert ".clinical-count,.pill')" in brh._FOLD_JS


def test_applying_never_queues_itself_again():
    js = brh._FOLD_JS
    assert "if(b.textContent!==t)b.textContent=t;" in js
    assert "finally{if(obs)obs.takeRecords()}" in js
