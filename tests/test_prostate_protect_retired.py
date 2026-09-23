"""Prostate Protect is discontinued; Man Manna replaces it (Glen, 2026-09-22).

Retired the catalog's own way: inactive, with superseded_by, so the old product page,
old links and a chat answer naming it all resolve to Man Manna while order history
keeps the old slug.
"""
import json
from pathlib import Path

P = json.loads((Path(__file__).resolve().parents[1] / "data" / "products.json")
               .read_text())["products"]


def test_prostate_protect_is_retired_to_man_manna():
    pp = P["prostate-protect"]
    assert pp.get("inactive") is True
    assert pp.get("superseded_by") == "man-manna"


def test_the_replacement_is_live():
    mm = P["man-manna"]
    assert not mm.get("inactive") and mm.get("fmp_id") == "178"


def test_the_live_resolver_routes_the_old_slug_to_man_manna():
    import app
    p = app._get_product("prostate-protect")
    assert p is not None and p["slug"] == "man-manna"
