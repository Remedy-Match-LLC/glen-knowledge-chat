"""Server-side fold state for the client portal. Spec:
docs/superpowers/specs/2026-09-25-portal-folding-design.md"""
import json
import sqlite3

import pytest

from dashboard import portal_folds as pf


@pytest.fixture
def cx(tmp_path):
    c = sqlite3.connect(str(tmp_path / "f.db"))
    pf.init_table(c)
    yield c
    c.close()


def test_missing_record_is_empty(cx):
    assert pf.get(cx, "a@x.com", "client") == {"cards": {}, "seen": [], "before_fold_all": {}}


def test_roundtrip_and_email_is_lowercased(cx):
    st = {"cards": {"scans-history": True, "biofield-section": False},
          "seen": ["scans"], "before_fold_all": {"scans": {"scans-history": False}}}
    pf.put(cx, "A@X.com ", "client", st)
    assert pf.get(cx, "a@x.com", "client") == st


def test_viewers_are_separate_rows(cx):
    pf.put(cx, "a@x.com", "client", {"cards": {"c1": True}})
    pf.put(cx, "a@x.com", "staff:master", {"cards": {"c1": False}})
    assert pf.get(cx, "a@x.com", "client")["cards"] == {"c1": True}
    assert pf.get(cx, "a@x.com", "staff:master")["cards"] == {"c1": False}


def test_put_replaces_not_merges(cx):
    pf.put(cx, "a@x.com", "client", {"cards": {"c1": True, "c2": True}})
    pf.put(cx, "a@x.com", "client", {"cards": {"c2": False}})
    assert pf.get(cx, "a@x.com", "client")["cards"] == {"c2": False}


def test_clean_drops_unknown_keys_and_bad_values():
    got = pf.clean({"cards": {"ok": True, "bad": "yes", "": True, 5: True},
                    "seen": ["scans", 3, "scans", ""], "evil": 1,
                    "before_fold_all": {"scans": {"ok": False, "x": 1}, "": {}}})
    assert got == {"cards": {"ok": True}, "seen": ["scans"],
                   "before_fold_all": {"scans": {"ok": False}}}


def test_clean_of_non_dict_is_empty():
    assert pf.clean(None) == pf.empty()
    assert pf.clean([1, 2]) == pf.empty()


def test_card_cap(cx):
    many = {f"c{i}": True for i in range(pf.MAX_CARDS + 50)}
    assert len(pf.put(cx, "a@x.com", "client", {"cards": many})["cards"]) == pf.MAX_CARDS


def test_too_large_is_refused(cx):
    # 500 cards of 120-char ids alone stay under 64 KB; saved Restore layouts for
    # several pages are what can exceed it.
    many = {("k" * 110) + str(i): True for i in range(pf.MAX_CARDS)}
    with pytest.raises(pf.TooLarge):
        pf.put(cx, "a@x.com", "client",
               {"cards": many, "before_fold_all": {d: many for d in ("a", "b", "c")}})
    assert pf.get(cx, "a@x.com", "client") == pf.empty()


def test_malformed_stored_json_reads_as_empty(cx):
    cx.execute("INSERT INTO portal_fold_state VALUES (?,?,?,?)",
               ("a@x.com", "client", "{not json", "t"))
    cx.commit()
    assert pf.get(cx, "a@x.com", "client") == pf.empty()


def test_init_table_is_idempotent(cx):
    pf.init_table(cx)
    pf.init_table(cx)
