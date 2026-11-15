"""Live session part 2a, the editor page: a mic beside every Head, Tail and new-remedy
input, the Balanced box in every card footer, the uncovered count, and client-first
lists. Glen's rulings of 14 November 2026."""
import re

from dashboard.biofield_report_html import _AUTHOR_JS, render_author_html


def _layer(rid, layer, head, tail, remedy, balanced=0):
    return {"rid": rid, "id": rid, "layer": layer, "stored_layer": layer, "head": head,
            "most_affected": tail, "remedy": remedy, "dosage": "", "frequency": "",
            "timing": "", "confirmed": 1, "origin": "live", "zone": "top",
            "layer_balanced": balanced}


def _page(layers=(), **kw):
    rep = {"test_id": "a7", "client": {"name": "J", "email": "j@x.com"}, "date": "",
           "layers": list(layers), "schedule": []}
    return render_author_html(rep, [], "", **kw)


def _chain(h):
    i = h.index("id=chaintbl")
    return h[i:h.index("<datalist id=catalog>", i)]


LAYERS = [_layer(1, 1, "Liver", "Gut", "Sobopla", 1), _layer(2, 1, "Liver", "Gut", "TMG", 1),
          _layer(3, 2, "Kidney", "Bladder", "TMG", 0)]


def test_every_head_tail_and_new_remedy_has_its_own_mic():
    c = _chain(_page(LAYERS))
    for gid in ("g0", "g1", "gnew"):
        for field, kind in (("head", "head"), ("most", "tail"), ("nr_remedy", "remedy")):
            iid = f"{gid}_{field}"
            mic = re.search(r"<button type=button class=mic data-mic=\"%s\"[^>]*>" % iid, c)
            assert mic, iid
            assert f"onclick=\"recField('{iid}','{kind}')\"" in mic.group(0)
            # The mic sits right after its own input, so it reads as that field's.
            assert c.index(f"id={iid} ") < c.index(f'data-mic="{iid}"')
    assert c.count("class=mic ") == 9


def test_typing_into_a_voice_filled_field_clears_the_unmatched_mark():
    """One page listener, so each field's own oninput stays as it was."""
    m = re.search(r"// recField:begin\n(.*?)// recField:end", _AUTHOR_JS, re.S).group(1)
    assert "document.addEventListener('input'" in m
    assert "t.dataset.voice!==undefined)clearVoice(t)" in m


def test_balanced_box_in_each_card_footer_and_none_on_the_new_card():
    c = _chain(_page(LAYERS))
    feet = re.findall(r"<div class=lfoot>(.*?)</div>", c)
    assert len(feet) == 2
    assert "id=g0_balanced checked " in feet[0] and "setLayerBalanced('g0',this.checked)" in feet[0]
    assert "id=g1_balanced " in feet[1] and "id=g1_balanced checked" not in feet[1]
    assert "gnew_balanced" not in c
    assert "<div class='lcard lbalanced' draggable=true data-gid=g0" in c
    assert "<div class=lcard draggable=true data-gid=g1" in c


def test_uncovered_count_shows_and_is_absent_when_not_given():
    assert "3 detected stresses not covered by any layer yet" in _page(LAYERS, uncovered_count=3)
    assert "1 detected stress not covered by any layer yet" in _page(LAYERS, uncovered_count=1)
    assert "uncoveredCount" not in _page(LAYERS)


def test_lists_load_client_first():
    assert "/api/vocab?limit=5000&test_id=__TID__" in _AUTHOR_JS
    assert "/api/vocab?limit=5000&test_id=a7" in _page(LAYERS)


def test_new_visible_text_has_no_caps_or_em_dash():
    c = _chain(_page(LAYERS, uncovered_count=2))
    m = re.search(r"// recField:begin\n(.*?)// recField:end", _AUTHOR_JS, re.S).group(1)
    visible = re.findall(r">([^<]+)<", c) + re.findall(r"title=\"([^\"]+)\"", c)
    visible += re.findall(r"fstat\('([^']+)'", m)
    new_bits = [v for v in visible if any(w in v for w in (
        "Balanced", "Speak", "not covered", "Listening", "Not matched", "Stopped",
        "Nothing heard", "Layer balanced", "Not saved"))]
    assert new_bits
    for v in new_bits:
        assert "—" not in v and "&mdash;" not in v, v
        assert not re.search(r"\b[A-Z]{3,}\b", v), v
