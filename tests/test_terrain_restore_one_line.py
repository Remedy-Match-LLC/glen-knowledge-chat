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


def test_a_long_ingredient_list_is_not_cut_short():
    import app as appmod
    note = inv.terrain_restore_note([f"Flower Essence Number {i}" for i in range(20)])
    assert 300 < len(note) < 1000
    _, items_rec, _ = appmod._portal_priced_lines(
        [{"slug": "terrain-restore", "qty": 1, "note": note}])
    assert items_rec[0]["note"] == note


# ── Review round 1: lists built outside the Biofield builders ─────────────────
_NAMES = {c["slug"]: c["name"] for c in CATALOG}
_name_of = _NAMES.get


def test_a_list_synced_from_layers_folds_on_publish():
    out = inv.fold_reorder_items(
        [{"slug": "pyrite-gem-elixir-in-terrain-restore", "qty": 1},
         {"slug": "microbiome", "qty": 1},
         {"slug": "quinton-in-terrain-restore", "qty": 2, "price_cents": 5000}],
        _name_of, base_ok=True)
    assert out == [
        {"slug": "terrain-restore", "qty": 2, "names": [PYRITE, QUINTON],
         "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"},
        {"slug": "microbiome", "qty": 1}]


def test_a_folded_line_keeps_what_it_holds_when_another_joins():
    by_names = {"slug": "terrain-restore", "qty": 1, "names": [PYRITE],
                "note": "With Pyrite Gem Elixir"}
    by_note = {"slug": "terrain-restore", "qty": 1, "note": "With Pyrite Gem Elixir"}
    for folded in (by_names, by_note):
        out = inv.fold_reorder_items(
            [folded, {"slug": "quinton-in-terrain-restore", "qty": 1}], _name_of, True)
        assert len(out) == 1
        assert out[0]["note"] == "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"
    # Folding twice changes nothing.
    once = inv.fold_reorder_items([by_names], _name_of, True)
    assert inv.fold_reorder_items(once, _name_of, True) == once


def test_a_not_ready_ingredient_is_never_hidden_inside_the_bottle():
    out = inv.fold_reorder_items(
        [{"slug": "pyrite-gem-elixir-in-terrain-restore", "qty": 1},
         {"slug": "quinton-in-terrain-restore", "qty": 1}],
        _name_of, True, ready=lambda sl: sl != "quinton-in-terrain-restore")
    assert [i["slug"] for i in out] == ["terrain-restore", "quinton-in-terrain-restore"]
    assert out[0]["note"] == "With Pyrite Gem Elixir"


def test_a_remedy_written_without_the_suffix_folds_by_its_catalog_name():
    out = inv.fold_terrain_restore(
        [{"slug": "pyrite-gem-elixir-in-terrain-restore", "name": "Pyrite Gem Elixir"}],
        True, name_of=_name_of)
    assert out[0]["slug"] == "terrain-restore" and out[0]["note"] == "With Pyrite Gem Elixir"


def test_the_publish_route_folds_the_list_rae_sends(monkeypatch, tmp_path):
    import app as appmod
    monkeypatch.setattr(appmod, "LOG_DB", str(tmp_path / "chat_log.db"))
    appmod._init_auth_tables()
    monkeypatch.setattr(appmod, "CONSOLE_SECRET", "test-secret")
    c = appmod.app.test_client()
    r = c.post("/api/console/biofield-portal?key=test-secret", json={
        "email": "j@example.com", "name": "Josie", "content": {
            "greeting": "Aloha", "layers": [{"n": 1, "title": "Lens", "remedy": PYRITE}],
            "reorder_items": [
                {"slug": "pyrite-gem-elixir-in-terrain-restore", "qty": 1},
                {"slug": "quinton-hypertonic-electrolytes-in-terrain-restore", "qty": 1}]}})
    assert r.status_code == 200, r.data[:300]
    items = c.get(f"/api/portal/{r.get_json()['token']}").get_json()["reorder_items"]
    assert [(i["slug"], i.get("note")) for i in items] == [
        ("terrain-restore", "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes")]


def _checkout_portal(monkeypatch, tmp_path, note):
    import app as appmod
    from dashboard import client_portal as cp, orders as O
    db = str(tmp_path / "log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    item = {"slug": "terrain-restore", "qty": 1}
    if note:
        item["note"] = note
    with sqlite3.connect(db) as cx:
        O.init_orders_table(cx)
        cp.init_client_portal_table(cx)
        tok, _ = cp.upsert_portal(cx, "j@example.com", "Josie",
                                  {"greeting": "Aloha", "layers": [], "reorder_items": [item]})
    monkeypatch.setattr(appmod, "_get_product",
                        lambda s: {"slug": s, "name": "Terrain Restore", "price_cents": 7000}
                        if s == "terrain-restore" else None)
    import dashboard.stripe_pay as sp
    monkeypatch.setattr(sp, "create_checkout_session", lambda *a, **k: {"url": "https://s.test"})
    monkeypatch.setattr(sp, "create_itemized_checkout_session",
                        lambda *a, **k: {"url": "https://s.test"})
    monkeypatch.setattr(appmod, "_STRIPE_ACTIVE", True)
    return appmod, db, tok


def _saved_items(db):
    import json
    with sqlite3.connect(db) as cx:
        row = cx.execute("SELECT items_json FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0])


def test_checkout_takes_the_note_from_the_report_never_the_post(monkeypatch, tmp_path):
    for stored in ("With Pyrite Gem Elixir", None):
        sub = tmp_path / str(bool(stored))
        sub.mkdir()
        appmod, db, tok = _checkout_portal(monkeypatch, sub, stored)
        r = appmod.app.test_client().post(f"/api/portal/{tok}/checkout", json={
            "items": [{"slug": "terrain-restore", "qty": 1, "note": "<script>x</script>"}]})
        assert r.status_code == 200, r.data[:300]
        saved = _saved_items(db)
        assert [i.get("note") for i in saved] == [stored]
        assert saved[0]["unit_cents"] == 7000


def test_the_order_button_and_the_portal_page_carry_the_note(monkeypatch, tmp_path):
    appmod, db, tok = _checkout_portal(monkeypatch, tmp_path, "With Pyrite Gem Elixir")
    c = appmod.app.test_client()
    shown = c.get(f"/api/portal/{tok}").get_json()["reorder_items"]
    assert shown[0]["note"] == "With Pyrite Gem Elixir"
    r = c.post(f"/api/portal/{tok}/checkout")
    assert r.status_code == 200, r.data[:300]
    assert _saved_items(db)[0]["note"] == "With Pyrite Gem Elixir"


def test_the_console_editor_keeps_the_note_and_names():
    import pathlib
    src = pathlib.Path("static/console-biofield-portal.html").read_text()
    add = src[src.index("function addItem("):src.index("function collectItems(")]
    collect = src[src.index("function collectItems("):src.index("function syncOrderFromLayers(")]
    assert "el.dataset.note = it.note" in add and "el.dataset.names" in add
    assert "esc(it.note)" in add
    assert "o.note = el.dataset.note" in collect and "o.names = JSON.parse" in collect


def test_the_portal_report_leaves_a_not_ready_ingredient_out_of_the_bottle():
    from dashboard.biofield_authoring import add_chain_row, create_test
    cx = sqlite3.connect(":memory:")
    tid = create_test(cx, "Josie", "j@example.com", "2026-10-19")
    add_chain_row(cx, f"a{tid}", 1, "Lens", "Lens", PYRITE, "10 drops", "daily", "")
    add_chain_row(cx, f"a{tid}", 2, "Blood", "Blood", QUINTON, "10 drops", "daily", "")
    catalog = {c["slug"]: {"name": c["name"]} for c in CATALOG}
    catalog["quinton-in-terrain-restore"]["waitlist_only"] = True
    items = _publish(cx, tid, catalog)["content"]["reorder_items"]
    assert [(i["slug"], i.get("note")) for i in items] == [
        ("terrain-restore", "With Pyrite Gem Elixir"), ("quinton-in-terrain-restore", None)]


def test_the_handoff_bills_no_bottle_for_a_not_ready_ingredient(tmp_path, monkeypatch):
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
        add_chain_row(cx, tid, 1, "Lens", "Lens", QUINTON, "10 drops", "daily", "")
        add_chain_row(cx, tid, 2, "Gut", "Gut", "Microbiome", "1 cap", "daily", "")
    monkeypatch.setattr(inv, "default_handoff_push", lambda *a, **k: {"ok": True})
    catalog = [dict(c) for c in CATALOG]
    next(c for c in catalog if c["slug"] == "quinton-in-terrain-restore")["waitlist_only"] = True
    captured = {}
    client = create_app(
        db, invoice_fetch_catalog=lambda: catalog,
        invoice_create=lambda c, lines, replace_open=False, invoice_note=None,
                       idempotency_key="": captured.update(lines=lines) or {"ok": True, "order_id": 5},
        invoice_paid_check=lambda email: {"paid": True},
    ).test_client()
    assert client.post("/author/%s/handoff" % tid, json={}).get_json()["ok"] is True
    assert [l["slug"] for l in captured["lines"]] == ["microbiome"]


def test_edit_invoice_seeds_the_note_from_the_report(monkeypatch, tmp_path):
    import pathlib
    import app as appmod
    from dashboard import portal_biofield_reports as pbr
    db = str(tmp_path / "log.db")
    monkeypatch.setattr(appmod, "LOG_DB", db)
    monkeypatch.setattr(appmod, "_bos_actor", lambda: "rae")
    with sqlite3.connect(db) as cx:
        pbr.init_table(cx)
        pbr.upsert_report(cx, "j@example.com", "2026-10-19", "", {
            "layers": [], "reorder_items": [
                {"slug": "terrain-restore", "qty": 1, "note": "With Pyrite Gem Elixir"},
                {"slug": "microbiome", "qty": 1}]}, "confirmed")
    j = appmod.app.test_client().get(
        "/api/console/client-recommended-products?email=j@example.com").get_json()
    assert [p.get("note") for p in j["products"]] == ["With Pyrite Gem Elixir", None]
    html = pathlib.Path("static/order-new.html").read_text()
    assert "note:String(rec.note||'')" in html
