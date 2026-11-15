"""Increment 4b: the extensible five-fold-dimensions framework + the
depth-of-penetration reach match-check."""
import sqlite3
from dashboard.biofield_dimensions import (
    init_dimension_tables, seed_dimensions, list_dimensions, dimension_values,
    tag, get_tag, depth_match, depth_label, DEPTH_KEY, migrate_flipped_numbering)


def _cx(tmp_path):
    cx = sqlite3.connect(str(tmp_path / "chat_log.db"))
    init_dimension_tables(cx)
    seed_dimensions(cx)
    return cx


def test_seed_has_the_dimensions_with_depth_ordered_and_matchcheck(tmp_path):
    cx = _cx(tmp_path)
    dims = list_dimensions(cx)
    keys = [d["key"] for d in dims]
    assert DEPTH_KEY in keys
    assert len(keys) >= 14
    depth = next(d for d in dims if d["key"] == DEPTH_KEY)
    assert depth["has_match_check"] == 1 and depth["ordered"] == 1
    vals = dimension_values(cx, DEPTH_KEY)
    assert [v["rank"] for v in vals] == [1, 2, 3, 4, 5]
    # Glen, 14 November 2026: numbered 5 (shallow) to 1 (deep).
    assert [v["code"] for v in vals] == ["nucleus", "cytoplasm", "ecf", "blood", "gut"]
    assert vals[0]["value"].startswith("Nucleo") and vals[4]["value"].startswith("Gut")


def test_pathology_and_prognosis_numbered_5_mild_to_1_grave(tmp_path):
    cx = _cx(tmp_path)
    assert [v["code"] for v in dimension_values(cx, "pathology_types")] == [
        "neoplasia", "dysplasia", "metaplasia", "hyperplasia", "hypertrophy"]
    assert [v["code"] for v in dimension_values(cx, "stages_of_prognosis")] == [
        "certain_death", "life_threatening", "degenerative", "serious", "self_limiting"]


def test_seed_is_idempotent(tmp_path):
    cx = _cx(tmp_path)
    seed_dimensions(cx)
    assert len([d for d in list_dimensions(cx) if d["key"] == DEPTH_KEY]) == 1
    assert len(dimension_values(cx, DEPTH_KEY)) == 5


def test_tag_roundtrip(tmp_path):
    cx = _cx(tmp_path)
    tag(cx, "auth_stress", "5", DEPTH_KEY, 4)
    assert get_tag(cx, "auth_stress", "5", DEPTH_KEY) == 4
    tag(cx, "auth_stress", "5", DEPTH_KEY, 2)        # update in place
    assert get_tag(cx, "auth_stress", "5", DEPTH_KEY) == 2
    assert get_tag(cx, "auth_stress", "9", DEPTH_KEY) is None


def test_depth_match_logic():
    # Ranks run 5 (gut, shallow) to 1 (nucleus, deep): deeper is a LOWER rank.
    assert depth_match(3, 1) == "ok"        # remedy reaches deeper than the stress
    assert depth_match(1, 1) == "ok"
    assert depth_match(1, 3) == "shallow"   # remedy falls short of the stress depth
    assert depth_match(None, 1) == "unknown"
    assert depth_match(1, None) == "unknown"


def _rank(cx, code):
    return next(v["rank"] for v in dimension_values(cx, DEPTH_KEY) if v["code"] == code)


def test_depth_match_flipped_numbering_by_code(tmp_path):
    cx = _cx(tmp_path)
    # A gut-level remedy against a cytoplasm stress falls short: flagged.
    assert depth_match(_rank(cx, "cytoplasm"), _rank(cx, "gut")) == "shallow"
    # A nucleus remedy against a blood stress reaches deeper: passes.
    assert depth_match(_rank(cx, "blood"), _rank(cx, "nucleus")) == "ok"


def test_depth_label(tmp_path):
    cx = _cx(tmp_path)
    assert depth_label(cx, 1).lower().startswith("nucle")
    assert depth_label(cx, 5).startswith("Gut")
    assert depth_label(cx, None) == ""


def _old_numbering_db(tmp_path):
    """A database seeded before 14 November 2026: ranks 1..5 ran shallow to deep."""
    cx = sqlite3.connect(str(tmp_path / "old.db"))
    init_dimension_tables(cx)
    old = {DEPTH_KEY: ["gut", "blood", "ecf", "cytoplasm", "nucleus"],
           "pathology_types": ["hypertrophy", "hyperplasia", "metaplasia",
                               "dysplasia", "neoplasia"],
           "stages_of_prognosis": ["self_limiting", "serious", "degenerative",
                                   "life_threatening", "certain_death"],
           "states_of_matter": ["solid", "liquid", "gas", "plasma", "condensate"]}
    for key, codes in old.items():
        for i, code in enumerate(codes, 1):
            cx.execute("INSERT INTO biofield_dimension_values VALUES (?,?,?,?)",
                       (key, i, code, code))
    tags = [("auth_stress", "7", DEPTH_KEY, 4),        # old 4 = cytoplasm
            ("auth_remedy", "7", DEPTH_KEY, 1),        # old 1 = gut
            ("auth_stress", "8", "pathology_types", 5),  # old 5 = neoplasia
            ("auth_stress", "8", "stages_of_prognosis", 2),  # old 2 = serious
            ("auth_stress", "8", "states_of_matter", 2)]     # not renumbered
    cx.executemany("INSERT INTO biofield_dimension_tags VALUES (?,?,?,?)", tags)
    cx.commit()
    return cx


def test_old_tags_migrated_once_by_code(tmp_path):
    cx = _old_numbering_db(tmp_path)
    seed_dimensions(cx)

    def code_of(target, tid, dim):
        r = get_tag(cx, target, tid, dim)
        return next(v["code"] for v in dimension_values(cx, dim) if v["rank"] == r)

    assert code_of("auth_stress", "7", DEPTH_KEY) == "cytoplasm"
    assert code_of("auth_remedy", "7", DEPTH_KEY) == "gut"
    assert code_of("auth_stress", "8", "pathology_types") == "neoplasia"
    assert code_of("auth_stress", "8", "stages_of_prognosis") == "serious"
    assert get_tag(cx, "auth_stress", "8", "states_of_matter") == 2   # untouched
    # The migrated pair still reads as "remedy falls short".
    assert depth_match(get_tag(cx, "auth_stress", "7", DEPTH_KEY),
                       get_tag(cx, "auth_remedy", "7", DEPTH_KEY)) == "shallow"
    # Idempotent: a second seed (every app start) and a direct call change nothing.
    seed_dimensions(cx)
    assert migrate_flipped_numbering(cx) == 0
    assert code_of("auth_stress", "7", DEPTH_KEY) == "cytoplasm"
    assert code_of("auth_stress", "8", "pathology_types") == "neoplasia"


def test_fresh_db_tags_not_flipped(tmp_path):
    cx = _cx(tmp_path)
    tag(cx, "auth_stress", "1", DEPTH_KEY, 2)
    seed_dimensions(cx)
    assert get_tag(cx, "auth_stress", "1", DEPTH_KEY) == 2
