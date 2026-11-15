"""The live session block on the editor page (Glen, 15 November 2026).

"Regulation can go at the end of the BSI line." "Add a section to add stresses to the
SPIRIT stress list after Regulation." "Include everything from Terrain to the Live
transcript in the live session section, and move that section above the causal chain
section."
"""
from bs4 import BeautifulSoup

from dashboard.biofield_report_html import _FOLD_JS, render_author_html


def _page(**rep):
    base = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
            "layers": [], "schedule": []}
    base.update(rep)
    return render_author_html(base, [], "")


def _soup(**rep):
    return BeautifulSoup(_page(**rep), "html5lib")


def test_regulation_ends_the_bsi_row():
    s = _soup(bsi=[13, 8, 5, 3])
    row = s.find(id="bsi_a").find_parent("div")
    ids = [t.get("id") for t in row.find_all(["input", "span"]) if t.get("id")]
    assert ids == ["bsi_a", "bsi_b", "bsi_location", "bsi_c", "bsi_d",
                   "t_regulation_label", "t_regulation"]
    text = " ".join(row.get_text(" ").split())
    assert text == "Biological Score Index x to x Regulation: Level 4 · Positive"


def test_regulation_has_no_row_of_its_own_any_more():
    """Inverted from the old layout: a row whose label was "Regulation"."""
    s = _soup()
    labels = [lab.get_text(strip=True) for lab in s.find(id="terrainSegs").find_all("label")]
    assert "Regulation" not in labels
    assert s.find(id="t_regulation").find_parent("div").find(id="bsi_d") is not None
    # The warning and status lines stay.
    assert s.find(id="t_warn") is not None and s.find(id="tstat") is not None


def test_add_spirit_stress_sits_below_the_bsi_row():
    s = _soup()
    rows = s.find(id="terrainSegs").find_all("div", class_="btnrow", recursive=False)
    assert [r.get("id") for r in rows][-1] == "spiritAddRow"
    assert rows[-2].find(id="t_regulation") is not None
    add = s.find(id="spiritAddRow")
    assert add.find("label").get_text() == "Add SPIRIT stress"
    inp = add.find("input", id="spiritAdd")
    assert inp.has_attr("data-loccombo") and not inp.has_attr("list")
    assert add.find("button").get("onclick") == "spiritAddNow()"
    assert add.find(id="spiritStat") is not None


def test_the_live_session_is_one_section_above_the_causal_chain():
    h = _page()
    assert h.index("<h2>Live session (voice)</h2>") < h.index("<h2>Causal chain")
    s = BeautifulSoup(h, "html5lib")
    wrap = s.find(class_="wrap")
    kids = [k for k in wrap.find_all(recursive=False)
            if k.name not in ("script", "style", "datalist")]
    names = [(k.name, k.get("id"), k.get_text(strip=True)[:22] if k.name == "h2" else "")
             for k in kids]
    i = names.index(("h2", None, "Live session (voice)"))
    # The heading, its one-line note, then ONE wrapper holding everything up to the
    # transcript; the next top-level element is the Causal chain heading. The fold
    # script ends an h2 section at the next h2, section or .card, and the wrapper is
    # neither, so the whole block folds as one.
    assert names[i + 1][0] == "p" and names[i + 2][:2] == ("div", "liveSession")
    assert names[i + 3][0] == "h2" and names[i + 3][2].startswith("Causal chain")
    live = s.find(id="liveSession")
    assert "card" not in (live.get("class") or [])
    for iid in ("terrainSegs", "t_phase", "t_location", "bsi_a", "t_regulation",
                "spiritAdd", "recBtn", "hfBtn", "sessText", "sessSaved", "rstat"):
        assert live.find(id=iid) is not None, iid
    assert "n.matches('section,.card')" in _FOLD_JS
