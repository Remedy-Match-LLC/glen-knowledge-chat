"""A pet's remedies on its owner's open invoice (Glen 2026-11-14: "Skylar's remedies
should also be added to the same invoice since Agnes pays for her dog's remedies").

Each intake's raise replaces only its own Biofield lines. The pet's lines carry a
"For <name>" note; the owner's carry none. Hand-added lines stay.
"""
import sqlite3

import app as appmod
from dashboard import biofield_invoice as bfi
from dashboard import biofield_progress as bp

FEE = bfi.BIOFIELD_SLUG
AGNES_JULY = [{"slug": FEE, "qty": 1}] + [
    {"slug": f"rem-{i}", "qty": 1, "source": "biofield"} for i in range(3)]
HAND = {"slug": "hand-added", "qty": 2, "source": "self", "note": "Rae added"}


def _pet(slug, qty=1, note=""):
    return {"slug": slug, "qty": qty, "source": "biofield",
            "note": bfi.household_note("Skylar", note)}


def test_note_and_tag_round_trip():
    assert bfi.household_note("Skylar") == "For Skylar"
    assert bfi.household_note("Skylar", "With A and B") == "For Skylar | With A and B"
    assert bfi.household_tag_of("For Skylar | With A and B") == "For Skylar"
    assert bfi.household_tag_of("With A and B") == ""
    # The terrain note still reads back under the tag.
    assert bfi.note_ingredients("For Skylar | With A and B") == ["A", "B"]


def test_pet_raise_keeps_every_owner_line_and_adds_its_own():
    new = [_pet("es1"), _pet("es2")]
    merged = bfi.merge_household_lines(new, AGNES_JULY + [HAND], "For Skylar")
    slugs = [l["slug"] for l in merged]
    for it in AGNES_JULY + [HAND]:
        assert it["slug"] in slugs
    assert slugs.count("es1") == 1 and slugs.count("es2") == 1


def test_pet_reraise_replaces_only_its_own_lines():
    first = bfi.merge_household_lines([_pet("es1"), _pet("es2")], AGNES_JULY, "For Skylar")
    second = bfi.merge_household_lines([_pet("es3")], first, "For Skylar")
    slugs = [l["slug"] for l in second]
    assert "es1" not in slugs and "es2" not in slugs and "es3" in slugs
    assert all(it["slug"] in slugs for it in AGNES_JULY)


def test_owner_reraise_replaces_her_lines_and_keeps_the_pets():
    shared = bfi.merge_household_lines([_pet("es1")], AGNES_JULY + [HAND], "For Skylar")
    owner_new = [{"slug": FEE, "qty": 1}, {"slug": "rem-new", "qty": 1, "source": "biofield"}]
    merged = bfi.merge_household_lines(owner_new, shared, "")
    slugs = [l["slug"] for l in merged]
    assert "rem-0" not in slugs and "rem-new" in slugs       # her old schedule refreshed
    assert slugs.count(FEE) == 1
    assert "es1" in slugs and "hand-added" in slugs          # the pet's and Rae's stay
    pet_line = next(l for l in merged if l["slug"] == "es1")
    assert pet_line["note"] == "For Skylar"


def test_one_remedy_for_both_is_two_lines():
    shared = bfi.merge_household_lines([_pet("rem-0")], AGNES_JULY, "For Skylar")
    assert [l["slug"] for l in shared].count("rem-0") == 2


def test_override_price_and_format_survive_the_other_subjects_raise():
    items = AGNES_JULY + [{"slug": "rem-x", "qty": 1, "source": "biofield", "format": "refill",
                           "override": True, "unit_cents": 4200}]
    merged = bfi.merge_household_lines([_pet("es1")], items, "For Skylar")
    x = next(l for l in merged if l["slug"] == "rem-x")
    assert x["format"] == "refill" and x["unit_cents"] == 4200


def test_owner_and_pet_tests_may_hold_one_order(tmp_path):
    db = str(tmp_path / "p.db")
    with sqlite3.connect(db) as cx:
        assert bp.link_order(cx, "a56", 38) == (True, "")
        ok, why = bp.link_order(cx, "a64", 38)
        assert not ok and "a56" in why                       # unrelated test: refused
        assert bp.link_order(cx, "a64", 38, share_with=["a56"]) == (True, "")


def test_pet_lines_do_not_buy_the_owners_membership_months(monkeypatch):
    """A pet-only order on the owner's email grants the owner no supply months."""
    prod = {"slug": "es1", "name": "ES1", "bottle_type": "30 Caps"}
    monkeypatch.setattr(appmod, "_get_product", lambda s: prod if s == "es1" else None)
    pet_only = {"items": [_pet("es1")]}
    assert appmod._order_has_supply_line(pet_only) is False
    owner = {"items": [{"slug": "es1", "qty": 1, "source": "biofield"}]}
    assert appmod._order_has_supply_line(owner) is True
    # A hand-written note starting "For" on an ordinary line still counts.
    manual = {"items": [{"slug": "es1", "qty": 1, "source": "self", "note": "For sleep"}]}
    assert appmod._order_has_supply_line(manual) is True


def test_keep_note_leaves_the_invoice_note_off_an_update(monkeypatch):
    sent = {}
    monkeypatch.setenv("CONSOLE_SECRET", "fake")

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true, "order_id": 38, "totals": {}, "lines": []}'

    import json, urllib.request
    def fake_open(req, timeout=0):
        sent["body"] = json.loads(req.data.decode())
        return _R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    bfi.default_create_order({"name": "A", "email": "a@x"}, [{"slug": "es1", "qty": 1}],
                             invoice_note=bfi.KEEP_NOTE, update_order_id=38)
    assert "invoice_note" not in sent["body"] and sent["body"]["update_order_id"] == 38
    bfi.default_create_order({"name": "A", "email": "a@x"}, [{"slug": "es1", "qty": 1}],
                             invoice_note=bfi.KEEP_NOTE)
    assert sent["body"]["invoice_note"] == bfi.DEFAULT_INVOICE_NOTE


# ---- 2026-11-14 review, round 2 --------------------------------------------------

def test_two_pets_with_one_first_name_keep_their_own_lines():
    jones = {"slug": "es9", "qty": 1, "source": "biofield", "note": "For Skylar Jones"}
    merged = bfi.merge_household_lines(
        [{"slug": "es1", "qty": 1, "source": "biofield", "note": "For Skylar Smith"}],
        AGNES_JULY + [jones], "For Skylar Smith",
        known_tags={"For Skylar Smith", "For Skylar Jones"})
    assert "es9" in [l["slug"] for l in merged]


def test_a_renamed_pet_replaces_its_old_lines():
    old = {"slug": "es9", "qty": 1, "source": "biofield", "note": "For Sky"}
    merged = bfi.merge_household_lines(
        [_pet("es1")], AGNES_JULY + [old], "For Skylar",
        own_tags={"For Sky"}, known_tags={"For Sky", "For Skylar"})
    slugs = [l["slug"] for l in merged]
    assert "es9" not in slugs and "es1" in slugs and "rem-0" in slugs


def test_a_pet_replaces_its_own_untagged_lines_from_before_sharing():
    pet_only = [{"slug": "es1", "qty": 1, "source": "biofield"}]
    merged = bfi.merge_household_lines(
        [_pet("es2")], pet_only, "For Skylar", own_tags={""}, known_tags={"For Skylar"})
    assert [l["slug"] for l in merged] == ["es2"]


def test_the_owners_raise_keeps_a_pets_hand_added_line_of_the_same_product():
    hand_pet = {"slug": "rem-0", "qty": 1, "source": "self", "note": "For Skylar"}
    merged = bfi.merge_household_lines(
        [{"slug": "rem-0", "qty": 1, "source": "biofield"}], AGNES_JULY + [hand_pet], "",
        known_tags={"For Skylar"})
    assert sum(1 for l in merged if l["slug"] == "rem-0") == 2


def test_an_ordinary_note_starting_for_is_not_a_pet_tag(monkeypatch):
    prod = {"slug": "immune-modulation", "name": "IM", "bottle_type": "30 Caps"}
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: prod if s == "immune-modulation" else None)
    assert bfi.household_tag_of("For sleep") == ""
    assert bfi.household_tag_of("For Sleep", {"For Skylar"}) == ""
    assert bfi.household_tag_of("For Skylar", {"For Skylar"}) == "For Skylar"
    owner = {"items": [{"slug": "immune-modulation", "qty": 1, "source": "biofield",
                        "note": "For sleep", "line_cents": 4500}]}
    assert appmod._order_has_supply_line(owner) is True
    # The owner's re-raise replaces her "For sleep" line, never keeps it twice.
    merged = bfi.merge_household_lines(
        [{"slug": "rem-0", "qty": 1, "source": "biofield"}],
        [{"slug": "rem-0", "qty": 1, "source": "biofield", "note": "For Sleep"}], "",
        known_tags={"For Skylar"})
    assert len(merged) == 1


def test_a_pets_hand_added_line_buys_the_owner_no_months(monkeypatch):
    prod = {"slug": "es1", "name": "ES1", "bottle_type": "30 Caps"}
    monkeypatch.setattr(appmod, "_get_product", lambda s: prod if s == "es1" else None)
    hand_pet = {"items": [{"slug": "es1", "qty": 1, "source": "self",
                           "note": "For Skylar", "line_cents": 3000}]}
    assert appmod._order_has_supply_line(hand_pet) is False
