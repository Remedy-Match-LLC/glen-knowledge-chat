"""Review round 3 of the always-$70 Terrain Restore change (2026-10-19): each test here
caught a change the other tests let through, or a path that still billed below $70."""
import json
import pathlib
import shutil
import sqlite3
import subprocess

import pytest

app_mod = pytest.importorskip("app")

CUSTOM = "terrain-restore-custom"
PYRITE = "pyrite-gem-elixir-in-terrain-restore"
QUINTON = "quinton-hypertonic-electrolytes-in-terrain-restore"
OAK = "oak-flower-essence-in-terrain-restore"
JASPER = "green-jasper-gem-elixir"
OLD_FOLDED = {"slug": "terrain-restore", "qty": 3, "price_cents": 5000,
              "note": "With Pyrite Gem Elixir and Quinton Hypertonic Electrolytes"}
PCT20 = {"type": "percent_off", "pct": 20, "scope": "all"}


def _accept(monkeypatch, items):
    from dashboard import practitioner_recommendations as pr
    monkeypatch.setattr(pr, "init_table", lambda cx: None)
    monkeypatch.setattr(pr, "active_for_patient",
                        lambda cx, e: {"status": "accepted", "items": items})


def test_merge_upgrades_an_old_folded_bottle_with_no_recommendation():
    """Kills A6. The portal display and the cart seed both read this merge. With no
    accepted recommendation it returns early, so the upgrade at its top is the only
    one: without it the cart seeds plain terrain-restore at the volume rate."""
    out = app_mod._merge_accepted_recommendation_items(
        sqlite3.connect(":memory:"), None, [dict(OLD_FOLDED)])
    assert [i["slug"] for i in out] == [CUSTOM]
    assert "price_cents" not in out[0]


def test_merge_never_folds_into_a_waitlisted_custom_bottle(monkeypatch):
    """Kills A9. "If the custom product cannot be sold, nothing folds.\""""
    _accept(monkeypatch, [{"slug": PYRITE, "qty": 1}, {"slug": QUINTON, "qty": 1}])
    real = app_mod._waitlist_block
    monkeypatch.setattr(app_mod, "_waitlist_block",
                        lambda s: True if s == CUSTOM else real(s))
    out = app_mod._merge_accepted_recommendation_items(
        sqlite3.connect(":memory:"), "j@example.com", [])
    assert CUSTOM not in {i["slug"] for i in out}
    assert {PYRITE, QUINTON} <= {i["slug"] for i in out}


def test_merge_never_folds_when_the_custom_product_is_missing(monkeypatch):
    """Kills A10. Plain Terrain Restore being sellable is not enough."""
    _accept(monkeypatch, [{"slug": PYRITE, "qty": 1}, {"slug": QUINTON, "qty": 1}])
    real = app_mod._get_product
    monkeypatch.setattr(app_mod, "_get_product",
                        lambda s, *a, **k: None if (s or "").strip() == CUSTOM else real(s, *a, **k))
    out = app_mod._merge_accepted_recommendation_items(
        sqlite3.connect(":memory:"), "j@example.com", [])
    assert CUSTOM not in {i["slug"] for i in out}


def test_the_fixed_price_cache_reloads_when_the_catalog_changes(monkeypatch):
    """Kills C5. A catalog edit must reach the cohort guard without a restart."""
    from dashboard import cohorts as co
    monkeypatch.setattr(co, "_FIXED_CACHE", [(1, 1), frozenset()])

    class _St:
        st_mtime_ns, st_size = 2, 2
    monkeypatch.setattr("os.stat", lambda *a, **k: _St())
    monkeypatch.setattr("json.load", lambda fh: {"products": {"new-fixed": {"fixed_price": True}}})
    assert co._never_cohort_priced("new-fixed") is True


def test_a_cached_fixed_price_slug_survives_an_unreadable_catalog(monkeypatch):
    """Kills C6. green-jasper-gem-elixir has no "-in-terrain-restore" ending, so only
    the cache protects it once the catalog read fails."""
    from dashboard import cohorts as co
    monkeypatch.setattr(co, "_FIXED_CACHE", [(1, 1), frozenset({JASPER})])
    monkeypatch.setattr("os.stat", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    assert co.policy_unit_cents(PCT20, slug=JASPER, list_cents=7000, is_ff=False) is None


def test_the_cohort_guard_reads_a_slug_as_typed(monkeypatch):
    """Kills C7. A line slug with stray case or spaces still never takes a cohort price."""
    from dashboard import cohorts as co
    monkeypatch.setattr(co, "_FIXED_CACHE", [None, frozenset()])
    monkeypatch.setattr("os.stat", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    assert co.policy_unit_cents(PCT20, slug=" Terrain-Restore-Custom ", list_cents=7000,
                                is_ff=False) is None
    assert co.policy_unit_cents(PCT20, slug=OAK.upper(), list_cents=7000, is_ff=False) is None


def test_the_engine_never_marks_the_custom_bottle_volume_eligible():
    """Kills D4. fixed_price is the only thing left between the bottle and a volume
    discount if this flag flips; keep both."""
    p = app_mod._get_product(CUSTOM)
    assert app_mod._engine_item(p, 1)["volume_eligible"] is False


def test_the_console_keeps_the_custom_bottles_note_and_held_list():
    """Kills J2. Republishing from the console must send the custom line's note and
    held list, or the client's line loses its ingredient names and cannot unfold."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = pathlib.Path("static/console-biofield-portal.html").read_text()
    code = src[src.index("const FOLDED_TR"):src.index("function syncOrderFromLayers(")]
    held = [{"name": "Pyrite Gem Elixir in Terrain Restore", "slug": PYRITE}]
    js = """
function mk(slug, note, held){
  const v = {'.rs': {value: slug}, '.rq': {value: '1'}, '.rp': {value: ''}};
  return {querySelector: s => v[s], dataset: {note: note, held: JSON.stringify(held)}};
}
const KIDS = [mk(%s, 'With Pyrite Gem Elixir', %s)];
function $(){ return {children: KIDS}; }
%s
process.stdout.write(JSON.stringify(collectItems()));
""" % (json.dumps(CUSTOM), json.dumps(held), code)
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True,
                                    check=True, timeout=30).stdout)
    assert out[0]["note"] == "With Pyrite Gem Elixir"
    assert out[0]["held"] == held


def test_an_old_reports_courtesy_price_on_a_single_bottle_does_not_bill():
    _lines, recs, _sub = app_mod._portal_priced_lines([{"slug": OAK, "qty": 1, "price_cents": 5000}])
    assert recs[0]["unit_cents"] == 7000


@pytest.mark.parametrize("slug", [JASPER, "forgiveness-flower-essence-in-terrain-restore-2",
                                  "self-esteem-flower-essence-in-terrain-restore-2"])
def test_every_fixed_price_slug_is_backstopped(monkeypatch, slug):
    from dashboard import cohorts as co
    monkeypatch.setattr(co, "_FIXED_CACHE", [None, frozenset()])
    monkeypatch.setattr("os.stat", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    assert co.policy_unit_cents(PCT20, slug=slug, list_cents=7000, is_ff=False) is None


def test_an_old_report_single_bottle_folds_to_70_with_no_recommendation():
    """An old report's single "X in Terrain Restore" at a courtesy $50 reads as the
    $70 custom bottle in the portal and the cart seed."""
    out = app_mod._merge_accepted_recommendation_items(
        sqlite3.connect(":memory:"), None, [{"slug": OAK, "qty": 1, "price_cents": 5000},
                                            {"slug": "microbiome", "qty": 1}])
    assert sorted(i["slug"] for i in out) == sorted([CUSTOM, "microbiome"])
    bottle = next(i for i in out if i["slug"] == CUSTOM)
    assert "price_cents" not in bottle and bottle["note"] == "With Oak Flower Essence"


def test_a_typed_price_on_the_custom_bottle_still_bills_at_checkout():
    _lines, recs, _sub = app_mod._portal_priced_lines(
        [{"slug": CUSTOM, "qty": 1, "price_cents": 6500, "note": "With Oak Flower Essence"}])
    assert recs[0]["unit_cents"] == 6500
