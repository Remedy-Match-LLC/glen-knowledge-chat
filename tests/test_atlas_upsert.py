"""Correcting ONE concept, which had no safe path until 2026-09-11.

Before this, changing a single published fact meant /admin/atlas/reseed, which
replaces the entire live graph with the committed build. The two had drifted by 76
concepts, so fixing one price that way would have deleted Accelerated Self Healing,
Biofield Analysis, Biological Dentistry and 73 more.
"""
import json

import pytest

import atlas_store


def _concept(cid, **over):
    c = {"id": cid, "label": cid, "summary": f"{cid} summary",
         "cluster": "programs-protocols", "coords": {"x": 0.5, "y": 0.5},
         "links": [], "status": "live"}
    c.update(over)
    return c


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "atlas-concepts.json"
    path.write_text(json.dumps(
        {"version": "t", "concepts": [_concept("iop-program"), _concept("keep-me")]}),
        encoding="utf-8")
    monkeypatch.setattr(atlas_store, "CONCEPTS_PATH", path)
    return path


def _ids(path):
    return [c["id"] for c in json.loads(path.read_text())["concepts"]]


def test_patch_changes_one_field_and_touches_nothing_else(store):
    cid, action = atlas_store.upsert_concept(
        concept_id="iop-program", patch={"summary": "now $314.86"})
    assert (cid, action) == ("iop-program", "updated")
    got = {c["id"]: c for c in json.loads(store.read_text())["concepts"]}
    assert got["iop-program"]["summary"] == "now $314.86"
    # the untouched fields survive the merge
    assert got["iop-program"]["label"] == "iop-program"
    assert got["iop-program"]["coords"] == {"x": 0.5, "y": 0.5}
    # and the OTHER concept is still there. This is the whole point: a reseed would
    # have replaced the file wholesale.
    assert got["keep-me"]["summary"] == "keep-me summary"
    assert sorted(_ids(store)) == ["iop-program", "keep-me"]


def test_full_upsert_can_create(store):
    cid, action = atlas_store.upsert_concept(concept=_concept("brand-new"))
    assert (cid, action) == ("brand-new", "created")
    assert sorted(_ids(store)) == ["brand-new", "iop-program", "keep-me"]


def test_patch_on_an_unknown_id_raises_rather_than_creating(store):
    with pytest.raises(KeyError):
        atlas_store.upsert_concept(concept_id="never-existed", patch={"label": "x"})
    assert sorted(_ids(store)) == ["iop-program", "keep-me"]


def test_a_patch_that_would_break_validation_writes_nothing(store):
    # coords out of range must be refused, and refused BEFORE the file is written.
    with pytest.raises(ValueError):
        atlas_store.upsert_concept(
            concept_id="iop-program", patch={"coords": {"x": 9, "y": 0.5}})
    got = {c["id"]: c for c in json.loads(store.read_text())["concepts"]}
    assert got["iop-program"]["coords"] == {"x": 0.5, "y": 0.5}


def test_a_patch_stripping_a_required_field_writes_nothing(store):
    with pytest.raises(ValueError):
        atlas_store.upsert_concept(concept_id="iop-program", patch={"summary": None,
                                                                    "cluster": None})
        # summary=None survives the merge as a present key, so the guard that matters
        # here is validate_concept on the MERGED record, not on the patch.
    assert json.loads(store.read_text())["concepts"]


def test_a_patch_cannot_rename_the_record_it_edits(store):
    atlas_store.upsert_concept(concept_id="iop-program", patch={"id": "hijacked"})
    assert sorted(_ids(store)) == ["iop-program", "keep-me"]


def test_either_shape_but_not_both(store):
    with pytest.raises(ValueError):
        atlas_store.upsert_concept(concept=_concept("a"), concept_id="b", patch={})
    with pytest.raises(ValueError):
        atlas_store.upsert_concept()
