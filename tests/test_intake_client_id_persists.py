"""The E4L client id must survive a page reload.

Picking a client from the intake dropdown stores their E4L client_id in the page,
and "Check E4L now" sends it to the live portal as the reliable key. The save
route accepted client_id in its JSON and then silently dropped it: update_header
only took name, email and date. So the id lived until the next reload and the
scan check then fell back to matching on name.

Judith Tom is exactly why that matters: E4L holds her as "Judith Tom" while her
PDF says "Judith Ann Tom", so a name-based fallback is the weaker key for the
client who needed it most.
"""
import pytest

from dashboard import biofield_authoring as ba
from dashboard import db


@pytest.fixture()
def cx(tmp_path):
    c = db.connect(str(tmp_path / "b.db"))
    ba.init_auth_tables(c)
    yield c
    c.close()


def _new(cx):
    return ba.create_test(cx, "", "", "")


def test_the_client_id_is_stored_and_read_back(cx):
    tid = _new(cx)
    ba.update_header(cx, tid, name="Judith Tom", email="j@example.com",
                     date="2026-09-04", client_id="13933")
    rep = ba.authored_report(cx, tid)
    assert rep["client"]["e4l_client_id"] == "13933"


def test_a_save_that_omits_the_client_id_does_not_wipe_it(cx):
    """Per-field, like the rest of update_header: a pass that carries no id must
    not clobber one captured earlier."""
    tid = _new(cx)
    ba.update_header(cx, tid, client_id="13933")
    ba.update_header(cx, tid, name="Judith Tom")
    rep = ba.authored_report(cx, tid)
    assert rep["client"]["e4l_client_id"] == "13933"
    assert rep["client"]["name"] == "Judith Tom"


def test_clearing_the_client_id_is_possible(cx):
    """Choosing a different client must be able to REMOVE a stale id, or the
    check would keep querying the previous person."""
    tid = _new(cx)
    ba.update_header(cx, tid, client_id="13933")
    ba.update_header(cx, tid, client_id="")
    rep = ba.authored_report(cx, tid)
    assert rep["client"]["e4l_client_id"] == ""


def test_the_page_renders_the_stored_client_id():
    """It was hardcoded empty, so even a stored id never reached the browser and
    checkE4L() read a blank field on every reload."""
    from dashboard.biofield_report_html import render_author_html
    rep = {"test_id": "a1", "date": "2026-09-04", "layers": [],
           "schedule": {"slots": [], "entries": []},
           "client": {"name": "Judith Tom", "email": "j@example.com",
                      "e4l_client_id": "13933"}}
    html = render_author_html(rep)
    assert 'id=h_client_id value="13933"' in html
    # and an intake with no client selected renders empty rather than "None"
    rep["client"] = {"name": "", "email": ""}
    assert 'id=h_client_id value=""' in render_author_html(rep)


def test_the_save_route_forwards_the_client_id():
    """The route accepted client_id and dropped it. Parsed, not grepped."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "biofield_local_app.py").read_text()
    call = next(c for c in ast.walk(ast.parse(src))
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "update_header")
    kw = {k.arg for k in call.keywords}
    assert "client_id" in kw, "the save route still drops the E4L client id"
