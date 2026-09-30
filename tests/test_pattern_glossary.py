import sqlite3
import pytest
from dashboard import pattern_glossary as pg


def _seed(cx):
    cx.executescript(
        "CREATE TABLE e4l_items (code TEXT PRIMARY KEY, category TEXT NOT NULL, "
        " subcategory TEXT, name TEXT NOT NULL, full_name TEXT, e4l_description TEXT, "
        " clinical_notes TEXT, sort_order INTEGER);"
        "CREATE TABLE e4l_pattern_structures (code TEXT NOT NULL, structure TEXT NOT NULL, "
        " stype TEXT, is_primary INTEGER DEFAULT 0, source_phrase TEXT, PRIMARY KEY(code,structure));"
        "CREATE TABLE formulations (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT);"
        "CREATE TABLE e4l_formulation_map (id INTEGER PRIMARY KEY, item_code TEXT, "
        " formulation_id INTEGER, priority INTEGER);"
    )
    cx.executemany("INSERT INTO formulations (id,name,url) VALUES (?,?,?)", [
        (1, "Nous Energy", ""), (2, "Holy Grail", ""), (3, "Terrain Restore", "")])
    cx.executemany("INSERT INTO e4l_formulation_map (item_code,formulation_id,priority) VALUES (?,?,?)", [
        ("ED1", 3, 3), ("ED1", 1, 1), ("ED1", 2, 2), ("ED1", 1, 4)])  # dup formulation 1
    cx.executemany("INSERT INTO e4l_items VALUES (?,?,?,?,?,?,?,?)", [
        ("ED1", "ED", "", "Source", "Source Driver", "Supports the body's fundamental energy source.", "", 1),
        ("Lead", "Environmental", "Heavy Metals", "Lead", "Lead", "", "", 2),   # structures-only
        ("ES9", "ES", "", "Ghost", "Ghost", "", "", 3),                          # neither -> excluded
    ])
    cx.executemany("INSERT INTO e4l_pattern_structures VALUES (?,?,?,?,?)", [
        ("ED1", "Heart", "organ", 1, ""),
        ("ED1", "Energy & Stamina", "function", 0, ""),
        ("Lead", "Nervous System", "system", 1, ""),
    ])
    cx.commit()


@pytest.fixture
def cx():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    _seed(c); return c


def test_slug_for_uses_code(cx):
    assert pg.slug_for("Heavy Metals") == "heavy-metals"
    assert pg.slug_for("ED1") == "ed1"


def test_pattern_remedies_ordered_and_deduped(cx):
    r = pg.pattern_remedies(cx, "ED1")
    # ordered by priority (1,2,3), formulation 1 deduped to its best (priority 1)
    assert [x["name"] for x in r] == ["Nous Energy", "Holy Grail", "Terrain Restore"]
    assert r[0]["priority"] == 1
    assert pg.pattern_remedies(cx, "NONE") == []
    assert pg.pattern_remedies(cx, "") == []


def test_get_pattern_shape_and_structure_order(cx):
    p = pg.get_pattern(cx, "ed1")
    assert p["code"] == "ED1" and p["name"] == "Source" and p["full_name"] == "Source Driver"
    assert p["description"].startswith("Supports the body")
    # primary structure first, then by stype/structure
    assert [s["structure"] for s in p["structures"]] == ["Heart", "Energy & Stamina"]
    assert p["structures"][0]["is_primary"] == 1
    assert p["has_page"] is True


def test_get_pattern_unknown_slug_is_none(cx):
    assert pg.get_pattern(cx, "nope") is None


def test_page_exists_rules(cx):
    assert pg.page_exists(cx, "ed1") is True          # described + structures
    assert pg.page_exists(cx, "lead") is True          # structures only
    assert pg.page_exists(cx, "ghost") is False        # neither
    assert pg.page_exists(cx, "missing") is False


def test_list_patterns_groups_and_excludes_empty(cx):
    groups = pg.list_patterns(cx)
    flat = {p["slug"]: p for g in groups for p in g["patterns"]}
    assert "ed1" in flat and "lead" in flat
    assert "ghost" not in flat                          # excluded (no page)
    assert flat["ed1"]["has_desc"] is True and flat["ed1"]["n_structures"] == 2
    assert flat["lead"]["has_desc"] is False and flat["lead"]["n_structures"] == 1
    cats = [g["category"] for g in groups]
    assert cats == sorted(set(cats), key=cats.index)   # each category once, stable


def test_pattern_remedies_carry_order_tier_and_conditions():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE formulations (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
        "CREATE TABLE e4l_formulation_map (id INTEGER PRIMARY KEY, item_code TEXT, "
        " finding_pattern TEXT, formulation_id INTEGER, priority INTEGER, order_tier INTEGER,"
        " qualifying_conditions TEXT);")
    c.executemany("INSERT INTO formulations VALUES (?,?)",
                  [(1, "Mucosa Syntropy"), (2, "Terrain Restore"), (3, "Odd")])
    c.executemany("INSERT INTO e4l_formulation_map (item_code,formulation_id,priority,"
                  "order_tier,qualifying_conditions) VALUES (?,?,?,?,?)", [
                      ("EI3", 1, 1, 1, None),
                      ("EI3", 2, 2, 2, '["leaky gut", "bloating"]'),
                      ("EI3", 3, 3, 2, "not json")])
    r = pg.pattern_remedies(c, "EI3", terms={"leaky gut": {"public": "leaky gut"},
                                             "bloating": {"public": "bloating"}})
    assert r[0] == {"name": "Mucosa Syntropy", "priority": 1, "order_tier": 1, "conditions": []}
    assert r[1]["order_tier"] == 2 and r[1]["conditions"] == ["leaky gut", "bloating"]
    assert r[2]["order_tier"] == 2 and r[2]["conditions"] == []


def test_pattern_remedies_old_schema_reads_as_first_order(cx):
    r = pg.pattern_remedies(cx, "ED1")
    assert all(x["order_tier"] == 1 and x["conditions"] == [] for x in r)


def test_pattern_rows_follow_code_rows_and_code_row_decides():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE formulations (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
        "CREATE TABLE e4l_formulation_map (id INTEGER PRIMARY KEY, item_code TEXT, "
        " finding_pattern TEXT, formulation_id INTEGER, priority INTEGER, order_tier INTEGER,"
        " qualifying_conditions TEXT);")
    c.executemany("INSERT INTO formulations VALUES (?,?)",
                  [(1, "Nous Energy"), (2, "Neuro-Magnesium")])
    c.executemany("INSERT INTO e4l_formulation_map (item_code,finding_pattern,formulation_id,"
                  "priority,order_tier,qualifying_conditions) VALUES (?,?,?,?,?,?)", [
                      (None, "MR", 1, 1, 1, None),
                      ("MR7", "MR7", 2, 1, 1, None),
                      ("MR2", "MR2", 1, 2, 2, '["fatigue"]')])
    assert [(x["name"], x["order_tier"]) for x in pg.pattern_remedies(c, "MR7")] == [
        ("Neuro-Magnesium", 1), ("Nous Energy", 1)]
    assert [(x["name"], x["order_tier"], x["conditions"])
            for x in pg.pattern_remedies(c, "MR2", terms={"fatigue": {"public": "fatigue"}})] \
        == [("Nous Energy", 2, ["fatigue"])]
    assert pg.pattern_remedies(c, "ED1") == []


def test_public_conditions_use_the_approved_wording_and_fail_closed():
    terms = {"diabetes": {"public": "blood sugar balance"},
             "high blood sugar": {"public": "blood sugar balance"},
             "covid vaccine": {"hide": True, "reason": "x"},
             "bloating": {"public": "bloating"}}
    assert pg.public_conditions(["diabetes", "high blood sugar", "bloating"], terms) == [
        "blood sugar balance", "bloating"]                       # deduped
    assert pg.public_conditions(["covid vaccine"], terms) == []   # hidden
    assert pg.public_conditions(["glaucoma"], terms) == []        # unmapped never shows raw
    assert pg.public_conditions(["bloating"], {}) == []           # no terms file -> nothing


def test_terms_file_covers_every_live_condition_and_shows_no_diagnosis():
    import json, os
    terms = pg.load_public_terms()
    assert terms, "data/condition_public_terms.json missing or empty"
    for v in terms.values():
        assert v.get("hide") is True or str(v.get("public") or "").strip(), v
        if v.get("hide"):
            assert str(v.get("reason") or "").strip(), v
    shown = " ".join(str(v.get("public") or "") for v in terms.values()).lower()
    for dx in ("diabetes", "glaucoma", "cataract", "osteoporosis", "ptsd", "covid",
               "neuropathy", "hypothyroid", "heart disease", "macular degeneration", "amd"):
        assert dx not in shown.split(" ") and dx not in shown, dx
    path = os.path.expanduser("~/AI-Training/e4l.db")
    if not os.path.exists(path):
        return
    cx = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        live = {r[0] for r in cx.execute(
            "SELECT DISTINCT j.value FROM e4l_formulation_map m, "
            "json_each(m.qualifying_conditions) j")}
    except sqlite3.OperationalError:
        return
    assert sorted(live - set(terms)) == []


def test_pattern_remedies_default_terms_never_show_a_stored_diagnosis():
    """End to end with the real terms file: stored diagnoses become public terms."""
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE formulations (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
        "CREATE TABLE e4l_formulation_map (id INTEGER PRIMARY KEY, item_code TEXT, "
        " finding_pattern TEXT, formulation_id INTEGER, priority INTEGER, order_tier INTEGER,"
        " qualifying_conditions TEXT);")
    c.executemany("INSERT INTO formulations VALUES (?,?)",
                  [(1, "Reverse AGE"), (2, "Fungifuge"), (3, "Vax Only")])
    c.executemany("INSERT INTO e4l_formulation_map (item_code,finding_pattern,formulation_id,"
                  "priority,order_tier,qualifying_conditions) VALUES (?,?,?,?,?,?)", [
                      ("ED15", "ED15", 1, 1, 2, '["diabetes", "high blood sugar", "aging"]'),
                      ("ED15", "ED15", 2, 2, 2, '["must have taken candida cleanse first"]'),
                      ("ED15", "ED15", 3, 3, 2, '["covid vaccine", "not in the file"]')])
    r = {x["name"]: x["conditions"] for x in pg.pattern_remedies(c, "ED15")}
    assert r["Reverse AGE"] == ["blood sugar balance", "aging"]
    assert r["Fungifuge"] == ["only after a Candida Cleanse"]
    assert r["Vax Only"] == []
