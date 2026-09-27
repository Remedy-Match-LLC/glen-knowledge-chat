"""The console page for merging two people, and the links that open it.
Spec: docs/superpowers/specs/2026-09-26-merge-two-people-design.md"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "console-merge.html"


def _src(name):
    return (ROOT / "static" / name).read_text()


def _code(src):
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(^|[^:\"'])//[^\n]*", r"\1", src)


def test_page_exists_and_uses_no_browser_dialogs():
    code = _code(PAGE.read_text())
    for call in (r"\bconfirm\(", r"\balert\(", r"\bprompt\("):
        assert not re.search(call, code), call


def test_page_calls_the_merge_routes():
    page = PAGE.read_text()
    for route in ("/api/console/people/merge/preview", "/api/console/people/merge",
                  "/api/console/people/merges/", "/undo", "/api/people?q="):
        assert route in page, route


def test_client_page_offers_a_merge():
    page = _src("console-client.html")
    assert 'id="merge-person"' in page
    assert "/console/merge?email=" in page


def test_queue_review_opens_the_merge_page_and_never_applies():
    for name in ("console.html", "shaira-workspace.html"):
        code = _code(_src(name))
        assert "/console/merge?pending=" in code, name
        assert "/api/pending-merges/${mergeId}/apply" not in code, name


def test_no_all_caps_labels_on_the_queue():
    code = _src("console.html")
    assert ">KEEP:<" not in code and ">DELETE:<" not in code


def test_page_route_is_served_without_cache():
    import app as appmod
    appmod.app.config["TESTING"] = True
    r = appmod.app.test_client().get("/console/merge")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("Cache-Control", "")
