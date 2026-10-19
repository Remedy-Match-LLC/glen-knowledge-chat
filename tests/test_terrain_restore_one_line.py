"""All "in Terrain Restore" items on one Biofield are one Terrain Restore line
(Glen, 2026-10-19), named by a note under it."""
import sqlite3

from dashboard import biofield_invoice as inv

CATALOG = [
    {"slug": "terrain-restore", "name": "Terrain Restore"},
    {"slug": "pyrite-gem-elixir-in-terrain-restore", "name": "Pyrite Gem Elixir in Terrain Restore"},
    {"slug": "quinton-in-terrain-restore",
     "name": "Quinton Hypertonic Electrolytes in Terrain Restore"},
    {"slug": "oak-flower-essence-in-terrain-restore", "name": "Oak Flower Essence in Terrain Restore"},
    {"slug": "microbiome", "name": "Microbiome"},
]
PYRITE = "Pyrite Gem Elixir in Terrain Restore"
QUINTON = "Quinton Hypertonic Electrolytes in Terrain Restore"


def test_the_ingredient_and_the_note():
    assert inv.terrain_restore_ingredient(PYRITE) == "Pyrite Gem Elixir"
    assert inv.terrain_restore_ingredient("Terrain Restore") == ""
    assert inv.terrain_restore_ingredient("Microbiome") is None
    assert inv.terrain_restore_note(["A"]) == "With A"
    assert inv.terrain_restore_note(["A", "B"]) == "With A and B"
    assert inv.terrain_restore_note(["A", "B", "C"]) == "With A, B and C"


def test_josies_two_become_one_line_with_the_largest_count():
    built = inv.build_invoice_lines({}, [
        {"name": PYRITE, "qty": 1}, {"name": "Microbiome", "qty": 2},
        {"name": QUINTON, "qty": 3}], CATALOG, include_fee=False)
    assert built["lines"] == [
        {"slug": "terrain-restore", "qty": 3, "source": "biofield",
         "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"},
        {"slug": "microbiome", "qty": 2, "source": "biofield"}]


def test_a_single_item_takes_the_combined_form():
    built = inv.build_invoice_lines({}, [PYRITE], CATALOG, include_fee=False)
    assert built["lines"] == [{"slug": "terrain-restore", "qty": 1, "source": "biofield",
                               "note": "With Pyrite Gem Elixir"}]


def test_the_dearer_oak_essence_bills_as_terrain_restore():
    built = inv.build_invoice_lines({}, ["Oak Flower Essence in Terrain Restore"], CATALOG,
                                    include_fee=False)
    assert [l["slug"] for l in built["lines"]] == ["terrain-restore"]


def test_an_ingredient_named_twice_is_listed_once_and_plain_restore_joins():
    built = inv.build_invoice_lines({}, ["Terrain Restore", PYRITE, PYRITE], CATALOG,
                                    include_fee=False)
    assert built["lines"] == [{"slug": "terrain-restore", "qty": 1, "source": "biofield",
                               "note": "With Pyrite Gem Elixir"}]


def test_plain_terrain_restore_alone_carries_no_note():
    built = inv.build_invoice_lines({}, ["Terrain Restore"], CATALOG, include_fee=False)
    assert built["lines"] == [{"slug": "terrain-restore", "qty": 1, "source": "biofield"}]


def test_nothing_folds_when_terrain_restore_cannot_be_sold():
    for base in ({"slug": "terrain-restore", "name": "Terrain Restore", "inactive": True},
                 {"slug": "terrain-restore", "name": "Terrain Restore", "waitlist_only": True}):
        catalog = [base] + CATALOG[1:]
        built = inv.build_invoice_lines({}, [PYRITE, QUINTON], catalog, include_fee=False)
        assert [l["slug"] for l in built["lines"]] == [
            "pyrite-gem-elixir-in-terrain-restore", "quinton-in-terrain-restore"]


def test_an_unknown_name_still_goes_to_skipped():
    built = inv.build_invoice_lines({}, ["Made Up in Terrain Restore"], CATALOG,
                                    include_fee=False)
    assert built["lines"] == [] and built["skipped"] == ["Made Up in Terrain Restore"]


def test_the_handoff_seed_folds_in_layer_order(tmp_path):
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    from dashboard.biofield_handoff import build_portal_seed
    with sqlite3.connect(str(tmp_path / "c.db")) as cx:
        init_auth_tables(cx)
        tid = create_test(cx, "J", "j@example.com", "2026-10-19")
        add_chain_row(cx, tid, 1, "Lens", "Lens", PYRITE)
        add_chain_row(cx, tid, 2, "Gut", "Gut", "Microbiome")
        add_chain_row(cx, tid, 3, "Blood", "Blood", QUINTON)
        seed = build_portal_seed(cx, tid, lambda nm: inv.resolve_line_slug(nm, CATALOG),
                                 name="Josie", terrain_restore_ok=True)
    items = seed["reorder_items"]
    assert [i["slug"] for i in items] == ["terrain-restore", "microbiome"]
    assert items[0]["note"] == "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"
    assert items[0]["names"] == [PYRITE, QUINTON]
    # The report keeps each ingredient under its own layer.
    assert [L["remedy"] for L in seed["layers"]] == [PYRITE, "Microbiome", QUINTON]


def test_the_portal_order_keeps_the_note():
    import app as appmod
    lines, items_rec, _ = appmod._portal_priced_lines(
        [{"slug": "terrain-restore", "qty": 1, "note": "With Pyrite Gem Elixir"}])
    assert items_rec and items_rec[0]["note"] == "With Pyrite Gem Elixir"


def test_the_handoff_invoice_is_one_line_at_the_largest_count(tmp_path, monkeypatch):
    """The hand-off rebuilds Rae's invoice from the seed: the folded line must keep
    its note and take the largest bottle count of the remedies it holds."""
    from biofield_local_app import create_app
    from dashboard.biofield_authoring import add_chain_row, create_test, init_auth_tables
    import dashboard as _d
    monkeypatch.delenv("CONSOLE_SECRET", raising=False)
    monkeypatch.setattr(_d, "CONSOLE_SECRET", "", raising=False)
    db = str(tmp_path / "chat_log.db")
    with sqlite3.connect(db) as cx:
        init_auth_tables(cx)
        cx.execute("CREATE TABLE fmp_snap_products (product_name TEXT, doses_per_bottle INTEGER)")
        tid = create_test(cx, "Josie", "j@example.com", "2026-10-19")
        add_chain_row(cx, tid, 1, "Lens", "Lens", PYRITE, "10 drops", "daily", "")
        add_chain_row(cx, tid, 2, "Blood", "Blood", QUINTON, "10 drops", "daily", "")
        cx.execute("UPDATE biofield_auth_chain SET bottles='3' WHERE remedy=?", (QUINTON,))
    monkeypatch.setattr(inv, "default_handoff_push", lambda *a, **k: {"ok": True})
    captured = {}
    client = create_app(
        db, invoice_fetch_catalog=lambda: CATALOG,
        invoice_create=lambda c, lines, replace_open=False, invoice_note=None,
                       idempotency_key="": captured.update(lines=lines) or {"ok": True, "order_id": 5},
        invoice_paid_check=lambda email: {"paid": True},
    ).test_client()
    r = client.post("/author/%s/handoff" % tid, json={})
    assert r.get_json()["ok"] is True, r.data[:300]
    assert captured["lines"] == [{
        "slug": "terrain-restore", "qty": 3, "source": "biofield",
        "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"}]


def _publish(cx, tid, catalog):
    from dashboard import biofield_portal_publish as bpp
    return bpp.build_portal_content(cx, f"a{tid}" if not str(tid).startswith("a") else tid,
                                    special_price_cents=0, catalog=catalog)


def test_the_portal_report_folds_at_the_largest_count_and_only_when_sellable():
    from dashboard.biofield_authoring import add_chain_row, create_test
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Josie", "j@example.com", "2026-10-19")
    add_chain_row(cx, f"a{tid}", 1, "Lens", "Lens", PYRITE, "10 drops", "daily", "")
    add_chain_row(cx, f"a{tid}", 2, "Blood", "Blood", QUINTON, "10 drops", "daily", "")
    cx.execute("UPDATE biofield_auth_chain SET bottles='2' WHERE remedy=?", (QUINTON,))
    catalog = {c["slug"]: {"name": c["name"]} for c in CATALOG}
    items = _publish(cx, tid, catalog)["content"]["reorder_items"]
    assert items == [{"slug": "terrain-restore", "qty": 2,
                      "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"}]
    for flag in ("inactive", "waitlist_only"):
        catalog["terrain-restore"] = {"name": "Terrain Restore", flag: True}
        items = _publish(cx, tid, catalog)["content"]["reorder_items"]
        assert [i["slug"] for i in items] == [
            "pyrite-gem-elixir-in-terrain-restore", "quinton-in-terrain-restore"]
