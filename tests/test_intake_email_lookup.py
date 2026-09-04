"""The Email box on the intake header must find a client, like the name box does.

Glen, 2026-09-03, making an intake for Judith Tom: her E4L record is under
judithannltom@yahoo.com while her people record is tomjudy62@gmail.com, so he
looked the address up in E4L and typed it into the Email field. Nothing happened.
The field carried no handler at all, so it stored text and never filled her name,
her date, or her E4L client id, and no scan check ran.

The email box LOOKS like a lookup field and is the one you reach for when E4L is
where you found the client. It should behave like one.

The JS is rendered and EXECUTED under node rather than pattern-matched: a string
search would pass on the function's own name appearing in a comment.
"""
import json
import shutil
import subprocess

import pytest

from dashboard.biofield_report_html import render_author_html

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")

REP = {"test_id": "a1", "client": {"name": "", "email": ""},
       "date": "", "layers": [], "schedule": {"slots": [], "entries": []}}

# What /api/e4l/clients returns for a substring of Judith's yahoo address: the
# endpoint groups by NAME and hands back every email under that name.
JUDITH = [{"name": "Judith Tom",
           "emails": [{"email": "judithannltom@yahoo.com", "client_id": 13933,
                       "last_scan_date": "2017-10-22"}]}]


def _fn(name, src=None):
    src = src if src is not None else render_author_html(REP)
    start = src.index("function %s(" % name)
    # keep a leading `async`, or the extracted body has an await in a sync function
    if src[max(0, start - 6):start] == "async ":
        start -= 6
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def _run(js):
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _harness(typed, clients, extra=""):
    """Run emailSearch() then click the first suggestion, reporting the form state."""
    return _run("""
      const fields = {h_email: %s, h_name: '', h_date: '', h_client_id: ''};
      const dd = {style:{}, innerHTML:''};
      const els = {h_edd: dd};
      const val = id => fields[id] || '';
      const set = (id, v) => { fields[id] = v; };
      const _esc = s => String(s == null ? '' : s);
      const _today = () => '2026-09-03';
      let checked = 0;
      const afterClientSelected = async () => { checked++; };
      let E4L_CLIENT_ID = null;
      const document = { getElementById: id => els[id] || null };
      const fetch = async () => ({ json: async () => ({clients: %s}) });
      %s
      %s
      %s
      (async () => {
        await emailSearch();
        const rows = dd._rows || [];
        // Captured BEFORE picking: choosing a suggestion closes the dropdown, so
        // reading display afterwards would always say hidden.
        const shown = dd.style.display === 'block';
        if (rows.length) pickEmailRow(rows[0]);
        console.log(JSON.stringify({
          shown: shown, count: rows.length,
          rows: rows.map(r => r.email),
          name: fields.h_name, email: fields.h_email, date: fields.h_date,
          clientId: E4L_CLIENT_ID, checked,
        }));
      })();
    """ % (json.dumps(typed), json.dumps(clients),
           _fn("hideEDD"), _fn("emailSearch"), _fn("pickEmailRow")) + extra)


def test_typing_an_email_finds_the_client():
    out = _harness("judithannltom", JUDITH)
    assert out["shown"] is True and out["rows"] == ["judithannltom@yahoo.com"]


def test_picking_the_email_fills_the_name_date_and_client_id():
    """The whole point: name, date and the E4L client id come along for free."""
    out = _harness("judithannltom", JUDITH)
    assert out["name"] == "Judith Tom"
    assert out["email"] == "judithannltom@yahoo.com"
    assert out["date"] == "2026-09-03"
    assert out["clientId"] == 13933
    assert out["checked"] == 1, "selecting an email must run the same E4L check"


def test_only_addresses_that_actually_match_are_offered():
    """The endpoint groups by NAME, so a name hit drags in that client's other
    addresses. Offering those under an email search would hand back the very
    address the practitioner was trying to avoid."""
    two = [{"name": "Judith Tom", "emails": [
        {"email": "judithannltom@yahoo.com", "client_id": 13933, "last_scan_date": "2017-10-22"},
        {"email": "tomjudy62@gmail.com", "client_id": 999, "last_scan_date": None}]}]
    out = _harness("yahoo", two)
    assert out["rows"] == ["judithannltom@yahoo.com"]


def test_a_short_fragment_does_not_search():
    out = _harness("ju", JUDITH)
    assert out["count"] == 0 and out["shown"] is False


def test_no_match_closes_the_dropdown_rather_than_showing_an_empty_one():
    out = _harness("nobody@example.com", [])
    assert out["count"] == 0 and out["shown"] is False


def test_the_email_field_is_wired_and_has_its_own_dropdown():
    html = render_author_html(REP)
    assert "emailSearch()" in html and "h_edd" in html
    # The name picker must not have been disturbed.
    assert "nameSearch()" in html and "h_dd" in html
