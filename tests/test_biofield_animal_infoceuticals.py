"""An animal's scan interpretation proposes E4L infoceuticals, never Functional Formulations.

Glen, 2026-09-18: for an animal, the automated reading of the scan recommends the
E4L infoceuticals, "the names you are currently using: they name functions".

Glen, 2026-09-21, on Sasha Takahashi's intake: "it is proposing FF's to balance the
scan stresses even though Sasha is a cat."

Two faults, both covered here:

1. The rule was wired into Import Reveal only. Suggest remedies, the saved and
   pattern sets, per-layer candidates and the Full/Minimum program all built their
   proposals from the scan's FF coverage map with no species check at all.

2. Import Reveal read species from a `client_species` table in chat_log.db. That
   table does not exist on Glen's Mac, the lookup threw, the code caught it, and
   every animal imported as a human. `_animal_for`, twenty lines away, reads species
   from e4l.db and worked. The old test for this wiring grepped the source for the
   string "client_species" and so passed the whole time the route was broken; the
   route test below drives the real route instead.
"""
import sqlite3

import pytest

import dashboard.biofield_reveal_import as RI
from dashboard import biofield_stress as st
from dashboard.animal_infoceuticals import infoceutical_by_code
from dashboard.biofield_e4l import species_from_e4l

CAT = "sasha@x.com"
PERSON = "karin@x.com"

CATALOG = {
    "ed14-spleen": {"name": "ED14 Spleen Energetic Driver Infoceutical"},
    "es9-shock": {"name": "ES9 Shock - Auditory Processing Energetic Star Infoceutical"},
    "immune-modulation": {"name": "Immune Modulation"},
    "es1-old": {"name": "ES1 Immune Energetic Star Infoceutical", "inactive": True},
    "ed1-bar": {"name": "ED1 Something Bar"},              # a code prefix, not an infoceutical
    "ed10-x": {"name": "ED10 Heart Energetic Driver Infoceutical"},
}
MAP = infoceutical_by_code(CATALOG)

# Sasha's shape, one code of each kind:
#   ED14  has an infoceutical AND an FF covers it  -> required for everyone
#   ER43  an FF covers it but it has NO infoceutical -> required for a person only
#   ES9   has an infoceutical but NO FF covers it   -> seeded "optional"
# seed_from_scan marks a code required only when an FF covers it. That proxy is wrong
# for an animal in both directions, so the resolver re-derives it from the
# infoceuticals: ES9 must be proposed, ER43 must not be demanded.
FINDINGS = [{"code": "ED14", "name": "Spleen"}, {"code": "ER43", "name": "Spleen Rejuvenator"},
            {"code": "ES9", "name": "Audio"}]
FF_COVERAGE = {"Immune Modulation": {"ED14", "ER43"}}
ED14 = "ed14 spleen energetic driver infoceutical"
ES9 = "es9 shock - auditory processing energetic star infoceutical"
CHAIN = [{"layer": 1, "head": "Spleen", "remedy": ""}]


@pytest.fixture(autouse=True)
def _no_lookup_leak():
    st.set_animal_lookup(None)
    yield
    st.set_animal_lookup(None)


def _e4l(tmp_path):
    p = str(tmp_path / "e4l.db")
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE e4l_clients (client_id INTEGER, name TEXT, email TEXT, "
               "species TEXT, animal_name TEXT)")
    cx.execute("INSERT INTO e4l_clients VALUES (332311,'Sasha Takahashi',?, 'Cat','Sasha')",
               (CAT,))
    cx.execute("INSERT INTO e4l_clients VALUES (22086,'Karin Takahashi',?, 'Human','')",
               (PERSON,))
    cx.commit()
    cx.close()
    return p


def _seeded(tmp_path, *, animal):
    cx = sqlite3.connect(str(tmp_path / "c.db"))
    st.init_stress_tables(cx)
    st.seed_from_scan(cx, "a5", FINDINGS, FF_COVERAGE)
    st.set_animal_lookup((lambda cx, tid: MAP) if animal else (lambda cx, tid: None))
    return cx


# --- the two building blocks -------------------------------------------------

def test_infoceutical_by_code_maps_each_code_to_its_product():
    assert MAP["ED14"] == "ED14 Spleen Energetic Driver Infoceutical"
    assert MAP["ES9"] == "ES9 Shock - Auditory Processing Energetic Star Infoceutical"
    assert MAP["ED10"] == "ED10 Heart Energetic Driver Infoceutical"


def test_infoceutical_by_code_skips_inactive_and_non_infoceuticals():
    assert "ES1" not in MAP, "a retired product must never be proposed"
    assert "ED1" not in MAP, "a code-prefixed name that is not an infoceutical is not one"
    assert "Immune Modulation" not in MAP.values()


def test_species_comes_from_e4l_db(tmp_path):
    p = _e4l(tmp_path)
    assert species_from_e4l(p, CAT) == "Cat"
    assert species_from_e4l(p, " SASHA@X.COM ") == "Cat"
    assert species_from_e4l(p, PERSON) == "Human"
    assert species_from_e4l(p, "nobody@x.com") is None
    assert species_from_e4l(str(tmp_path / "missing.db"), CAT) is None


# --- the shared resolver: every suggestion path goes through it ---------------

def test_a_person_still_gets_the_ff(tmp_path):
    cx = _seeded(tmp_path, animal=False)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert "immune modulation" in [p["remedy"].lower() for p in data["picks"]]


def test_an_animal_gets_the_infoceuticals_not_the_ff(tmp_path):
    cx = _seeded(tmp_path, animal=True)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    names = {p["remedy"].lower() for p in data["picks"]}
    assert names == {ED14, ES9}
    assert "immune modulation" not in names


def test_an_infoceutical_code_no_ff_covers_is_still_proposed(tmp_path):
    """ES9 is seeded optional because no FF covers it. For an animal it has an
    infoceutical, so it must be proposed."""
    cx = _seeded(tmp_path, animal=True)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert ES9 in {p["remedy"].lower() for p in data["picks"]}


def test_a_code_with_no_infoceutical_is_not_demanded_of_an_animal(tmp_path):
    """ER43 is required only because an FF covers it. An animal must neither be offered
    that FF nor told the code is uncovered."""
    cx = _seeded(tmp_path, animal=True)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert "Spleen Rejuvenator" not in data["uncovered"]
    assert all("ER43" not in st.scan_coverage(cx, "a5").get(p["remedy"].lower(), set())
               for p in data["picks"])


def test_a_persons_codes_are_unchanged(tmp_path):
    """ES9 stays optional for a person, and ER43 stays covered by the FF."""
    cx = _seeded(tmp_path, animal=False)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert [p["remedy"].lower() for p in data["picks"]] == ["immune modulation"]
    assert data["uncovered"] == []


def test_an_animals_non_scan_stress_stays_uncovered(tmp_path, monkeypatch):
    """A manual stress would inherit the FFs Glen chose for people with it. For an
    animal it stays uncovered instead."""
    cx = _seeded(tmp_path, animal=False)
    st.add_stress(cx, "a5", "Worry", source="manual", balance="required")
    monkeypatch.setattr(st, "historical_remedies", lambda cx, label: {"spleen support"})
    person = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert "Worry" not in person["uncovered"], "control: the history must cover a person"
    st.set_animal_lookup(lambda cx, tid: MAP)
    data = st.resolve_remedy_set(cx, "a5", CHAIN, force_computed=True)
    assert "Worry" in data["uncovered"]
    assert "spleen support" not in {p["remedy"].lower() for p in data["picks"]}


def test_an_animals_saved_ff_set_is_not_shown(tmp_path):
    """Sasha's test 42 already had a saved set of FFs. It must not come back."""
    cx = _seeded(tmp_path, animal=True)
    st.save_remedy_set(cx, "a5", ["Immune Modulation", "Brain Boost"])
    data = st.resolve_remedy_set(cx, "a5", CHAIN)
    names = {p["remedy"].lower() for p in data["picks"]}
    assert "immune modulation" not in names and "brain boost" not in names
    assert names == {ED14, ES9}


def test_an_animal_ignores_a_pattern_template_of_ffs(tmp_path):
    """Templates are learned mostly from people with the same stress pattern."""
    cx = _seeded(tmp_path, animal=False)
    st.save_pattern_set(cx, "a5", CHAIN, ["Immune Modulation"])
    st.set_animal_lookup(lambda cx, tid: MAP)
    data = st.resolve_remedy_set(cx, "a5", CHAIN)
    assert "immune modulation" not in [p["remedy"].lower() for p in data["picks"]]


def test_an_animals_layer_candidates_are_infoceuticals_only(tmp_path):
    cx = _seeded(tmp_path, animal=True)
    lc = st.layer_candidates(cx, "a5", CHAIN, fallback_by_code={"ED14": ["Immune Modulation"]})
    offered = {c["remedy"].lower() for L in lc for c in L["candidates"]}
    assert "immune modulation" not in offered
    assert ED14 in offered


def test_an_animals_blank_layer_gets_no_ff_fallback(tmp_path):
    """A layer whose only code has no infoceutical (ER43) takes the caller's fallback,
    which is the FF formulation map. An animal must not be offered it."""
    chain = [{"layer": 1, "head": "Spleen", "remedy": ""},
             {"layer": 2, "head": "Spleen Rejuvenator", "remedy": ""}]
    cx = _seeded(tmp_path, animal=True)
    lc = st.layer_candidates(cx, "a5", chain, fallback_by_code={"ER43": ["Spleen Support"]})
    layer2 = next(L for L in lc if L["n"] == 2)
    assert layer2["codes"] == ["ER43"], "the test needs a layer holding only ER43"
    assert "spleen support" not in {c["remedy"].lower() for c in layer2["candidates"]}

def test_scan_coverage_is_the_single_source(tmp_path):
    """The Full/Minimum program built its own copy of the coverage from the table.
    It now reads scan_coverage, so it inherits the animal rule."""
    cx = _seeded(tmp_path, animal=True)
    assert st.scan_coverage(cx, "a5") == {ED14: {"ED14"}, ES9: {"ES9"}}
    st.set_animal_lookup(lambda cx, tid: None)
    assert st.scan_coverage(cx, "a5") == {"immune modulation": {"ED14", "ER43"}}


# --- the app wiring, driven through the real routes ---------------------------

@pytest.fixture
def _open_gate(monkeypatch):
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    import dashboard as _d
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)


def _client(tmp_path, email):
    from biofield_local_app import create_app
    db = str(tmp_path / "chat_log.db")
    app = create_app(db, e4l_db=_e4l(tmp_path),
                     scan_lookup=lambda e: {"found": False, "layers": []})
    client = app.test_client()
    tid = client.post("/author/new").headers["Location"].rstrip("/").split("/")[-1]
    client.post(f"/author/{tid}/header", json={"name": "Sasha", "email": email,
                                               "date": "2026-09-21"})
    return client, tid, db


@pytest.mark.parametrize("email,expected", [(CAT, True), (PERSON, False)])
def test_import_reveal_reads_species_from_e4l_db(tmp_path, monkeypatch, _open_gate,
                                                email, expected):
    """chat_log.db here has no client_species table, exactly as on Glen's Mac."""
    seen = {}

    def capture(*a, **k):
        seen["is_animal"] = k.get("is_animal")
        return {"found": False}

    monkeypatch.setattr(RI, "synthesize_reveal_layers", capture)
    client, tid, db = _client(tmp_path, email)
    assert not sqlite3.connect(db).execute(
        "SELECT 1 FROM sqlite_master WHERE name='client_species'").fetchone()
    client.post(f"/author/{tid}/e4l/import-reveal", json={})
    assert seen.get("is_animal") is expected


def test_the_app_wires_the_animal_lookup(tmp_path, _open_gate):
    """create_app must install the lookup, or the resolver change is inert."""
    cat_dir, person_dir = tmp_path / "cat", tmp_path / "person"
    cat_dir.mkdir()
    person_dir.mkdir()

    _client_c, tid, db = _client(cat_dir, CAT)
    got = st.animal_infoceuticals(sqlite3.connect(db), tid)
    assert got, "a cat's test must resolve to an infoceutical map"
    assert all("infoceutical" in n.lower() for n in got.values())

    _client_p, tid2, db2 = _client(person_dir, PERSON)
    assert st.animal_infoceuticals(sqlite3.connect(db2), tid2) is None


# --- what counts as balancing a stress, for an animal ------------------------

def _active_codes(cx, chain):
    return {s["code"] for s in st.list_stresses(cx, "a5", chain)["active"] if s.get("code")}


def test_an_infoceutical_on_an_animals_chain_balances_its_code(tmp_path):
    """Once Glen puts the proposed infoceutical on the chain, the stress it covers must
    read balanced and stop being proposed. It used to stay open, because the balance
    check read only the FF coverage table."""
    cx = _seeded(tmp_path, animal=True)
    # The head must NOT match the stress label: a head that equals a label balances
    # that stress whatever the remedy, which would pass this test with no fix at all.
    chain = [{"layer": 1, "head": "Immune Layer",
              "remedy": "ED14 Spleen Energetic Driver Infoceutical"}]
    assert "ED14" not in _active_codes(cx, chain)
    picks = {p["remedy"].lower() for p in
             st.resolve_remedy_set(cx, "a5", chain, force_computed=True)["picks"]}
    assert ED14 not in picks, "a stress already balanced must not be proposed again"


def test_an_ff_on_an_animals_chain_does_not_balance_its_code(tmp_path):
    """Sasha's chain carried Immune Modulation from the broken import. It must not count
    as balancing her ED14, or the infoceutical for ED14 is never proposed."""
    cx = _seeded(tmp_path, animal=True)
    chain = [{"layer": 1, "head": "Immune Layer", "remedy": "Immune Modulation"}]
    assert "ED14" in _active_codes(cx, chain)
    picks = {p["remedy"].lower() for p in
             st.resolve_remedy_set(cx, "a5", chain, force_computed=True)["picks"]}
    assert ED14 in picks


def test_an_ff_on_a_persons_chain_still_balances_its_code(tmp_path):
    cx = _seeded(tmp_path, animal=False)
    chain = [{"layer": 1, "head": "Immune Layer", "remedy": "Immune Modulation"}]
    assert "ED14" not in _active_codes(cx, chain)


def test_an_ff_already_on_an_animals_chain_shows_only_as_the_current_pick(tmp_path):
    """Review shows each layer's current remedy beside the alternatives, so an FF that is
    already on the chain still appears. It must appear as the current pick only, never
    as a proposed alternative."""
    cx = _seeded(tmp_path, animal=True)
    chain = [{"layer": 1, "head": "Spleen", "remedy": "Immune Modulation"}]
    layer = st.layer_candidates(cx, "a5", chain, fallback_by_code={})[0]
    not_infoceutical = [c for c in layer["candidates"]
                        if "infoceutical" not in c["remedy"].lower()]
    assert [c["remedy"] for c in not_infoceutical] == ["Immune Modulation"]
    assert all(c["source"] == "current" and c["is_default"] for c in not_infoceutical)
    assert ED14 in {c["remedy"].lower() for c in layer["candidates"]}
